"""Testy integracji Git: klienci dostawców (mock transport), bramka egress,
magazyn połączeń i GitService (rozwiązanie tokenu z referencji)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config.schema import EgressConfig, EgressPolicy
from husarz.git import (
    FileGitConnectionStore,
    GitConnection,
    GitHubProvider,
    GitLabProvider,
    GitProviderKind,
    GitService,
    build_git_service,
    build_provider,
)
from husarz.git.errors import GitAuthError, GitConnectionError, GitError
from husarz.router.egress import EgressError
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.unit


class FakeTransport:
    """Transport testowy: dopasowuje odpowiedź po (metoda, fragment URL). Bez sieci."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method, target, headers, json, timeout):  # noqa: ANN001
        url = target.connect_url
        self.calls.append((method, url, json))
        for (m, frag), resp in self._responses.items():
            if m == method and frag in url:
                return resp
        return 404, {"message": "not found"}


def _fake_resolve(host: str) -> list[str]:
    """Resolver testowy: każda nazwa → adres publiczny (żaden test nie odpytuje DNS)."""
    return ["140.82.121.6"]


class FakeSecrets:
    def __init__(self, value: str | None) -> None:
        self._value = value

    def resolve(self, ref: str) -> str | None:
        return self._value


# --- Dostawcy (mock transport) ----------------------------------------------


def test_github_list_repositories() -> None:
    t = FakeTransport(
        {
            ("GET", "/user/repos"): (
                200,
                [
                    {
                        "full_name": "acme/app",
                        "default_branch": "main",
                        "private": True,
                        "html_url": "https://github.com/acme/app",
                    }
                ],
            )
        }
    )
    repos = GitHubProvider(
        PinnedTarget.direct("https://api.github.com"), "tok", t
    ).list_repositories()
    assert repos[0].full_name == "acme/app"
    assert repos[0].private is True
    assert t.calls[0][2] is None  # GET bez body


def test_github_create_pull_request() -> None:
    t = FakeTransport(
        {("POST", "/repos/acme/app/pulls"): (201, {"number": 7, "html_url": "u", "title": "Fix"})}
    )
    pr = GitHubProvider(
        PinnedTarget.direct("https://api.github.com"), "tok", t
    ).create_pull_request("acme/app", title="Fix", head="feat", base="main", body="opis")
    assert pr.number == 7
    assert t.calls[0][2]["head"] == "feat"  # payload przekazany


def test_github_auth_error() -> None:
    t = FakeTransport({("GET", "/user/repos"): (401, {"message": "Bad credentials"})})
    with pytest.raises(GitAuthError):
        GitHubProvider(PinnedTarget.direct("https://api.github.com"), "zły", t).list_repositories()


def test_github_error_status() -> None:
    t = FakeTransport({("POST", "/pulls"): (422, {"message": "PR już istnieje"})})
    with pytest.raises(GitError):
        GitHubProvider(PinnedTarget.direct("https://api.github.com"), "t", t).create_pull_request(
            "a/b", title="x", head="h", base="m"
        )


def test_gitlab_list_and_create_mr_urlencodes_path() -> None:
    t = FakeTransport(
        {
            ("GET", "/projects"): (
                200,
                [
                    {
                        "path_with_namespace": "grp/app",
                        "default_branch": "main",
                        "visibility": "private",
                        "web_url": "w",
                    }
                ],
            ),
            ("POST", "/merge_requests"): (201, {"iid": 3, "web_url": "w3", "title": "Fix"}),
        }
    )
    provider = GitLabProvider(PinnedTarget.direct("https://gitlab.com/api/v4"), "tok", t)
    assert provider.list_repositories()[0].full_name == "grp/app"
    pr = provider.create_pull_request("grp/app", title="Fix", head="feat", base="main")
    assert pr.number == 3
    # Ścieżka projektu URL-encoded ('/' → %2F) w adresie MR.
    assert "grp%2Fapp/merge_requests" in t.calls[1][1]


# --- Bramka egress ----------------------------------------------------------


def _conn() -> GitConnection:
    return GitConnection(
        name="gh",
        provider=GitProviderKind.GITHUB,
        api_base="https://api.github.com",
        token_ref="env:GH",
    )


def test_build_provider_egress_denied_by_default() -> None:
    with pytest.raises(EgressError):
        build_provider(
            _conn(), "tok", EgressConfig(), resolve=_fake_resolve
        )  # deny-all, brak allowlisty


def test_build_provider_egress_allowed_when_allowlisted() -> None:
    egress = EgressConfig(default_policy=EgressPolicy.DENY, allowlist=["api.github.com"])
    provider = build_provider(
        _conn(), "tok", egress, transport=FakeTransport({}), resolve=_fake_resolve
    )
    assert isinstance(provider, GitHubProvider)


def _conn_with(api_base: str) -> GitConnection:
    return GitConnection(
        name="x", provider=GitProviderKind.GITHUB, api_base=api_base, token_ref="env:X"
    )


def test_build_provider_blocks_internal_host_ssrf() -> None:
    # Nawet przy egress=allow host wewnętrzny (metadata/loopback) jest twardo blokowany.
    allow = EgressConfig(default_policy=EgressPolicy.ALLOW)
    for host in ("https://169.254.169.254", "https://127.0.0.1", "https://localhost"):
        with pytest.raises(EgressError):
            build_provider(_conn_with(host), "tok", allow, resolve=_fake_resolve)


def test_build_provider_rejects_non_https_and_userinfo() -> None:
    allow = EgressConfig(default_policy=EgressPolicy.ALLOW)
    with pytest.raises(GitError):
        build_provider(
            _conn_with("http://api.github.com"), "tok", allow, resolve=_fake_resolve
        )  # nie-https
    with pytest.raises(GitError):
        build_provider(
            _conn_with("https://user@api.github.com"), "tok", allow, resolve=_fake_resolve
        )  # userinfo


def test_github_create_pr_encodes_repo_path() -> None:
    t = FakeTransport({("POST", "/pulls"): (201, {"number": 1, "html_url": "u", "title": "x"})})
    GitHubProvider(PinnedTarget.direct("https://api.github.com"), "tok", t).create_pull_request(
        "acme/a b", title="x", head="h", base="m"
    )
    assert "acme/a%20b/pulls" in t.calls[0][1]  # spacja zakodowana, '/' zachowany


def test_list_repositories_skips_non_dict_items() -> None:
    t = FakeTransport(
        {
            ("GET", "/user/repos"): (
                200,
                [
                    None,
                    "x",
                    {
                        "full_name": "a/b",
                        "default_branch": "main",
                        "private": False,
                        "html_url": "u",
                    },
                ],
            )
        }
    )
    repos = GitHubProvider(
        PinnedTarget.direct("https://api.github.com"), "tok", t
    ).list_repositories()
    assert [r.full_name for r in repos] == ["a/b"]


def test_file_store_load_corrupt_raises_clean(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text('{"connections": [{"name": "x"}]}', encoding="utf-8")  # brak wymaganych pól
    with pytest.raises(GitConnectionError):
        FileGitConnectionStore(path)


# --- Magazyn połączeń -------------------------------------------------------


def test_connection_store_add_get_remove_and_duplicate() -> None:
    svc = GitService()
    svc.add(_conn())
    assert svc.get("gh").provider is GitProviderKind.GITHUB
    with pytest.raises(GitConnectionError):
        svc.add(_conn())  # duplikat nazwy
    svc.remove("gh")
    with pytest.raises(GitConnectionError):
        svc.get("gh")


def test_file_connection_store_persists(tmp_path: Path) -> None:
    path = tmp_path / "conns.json"
    GitService(FileGitConnectionStore(path)).add(_conn())
    reloaded = GitService(FileGitConnectionStore(path))
    assert reloaded.get("gh").api_base == "https://api.github.com"
    assert not (tmp_path / "conns.json.tmp").exists()  # zapis atomowy, bez śmiecia


# --- GitService: rozwiązanie tokenu -----------------------------------------


def test_service_provider_for_resolves_token() -> None:
    svc = GitService(
        secrets=FakeSecrets("realny-token"),
        egress=EgressConfig(default_policy=EgressPolicy.ALLOW),
        transport=FakeTransport({("GET", "/user/repos"): (200, [])}),
        resolve=_fake_resolve,
    )
    svc.add(_conn())
    assert svc.provider_for("gh").list_repositories() == []


def test_service_provider_for_missing_token_raises_auth() -> None:
    svc = GitService(
        secrets=FakeSecrets(None), egress=EgressConfig(default_policy=EgressPolicy.ALLOW)
    )
    svc.add(_conn())
    with pytest.raises(GitAuthError):
        svc.provider_for("gh")


def test_service_provider_for_unknown_connection() -> None:
    with pytest.raises(GitConnectionError):
        GitService().provider_for("nieznane")


def test_build_git_service_from_config(tmp_path: Path) -> None:
    from husarz.config.schema import GitConfig, SecurityConfig

    git = GitConfig(enabled=True, connections_path=tmp_path / "c.json")
    svc = build_git_service(git, SecurityConfig(), secrets=FakeSecrets("t"))
    svc.add(_conn())
    assert svc.get("gh").name == "gh"
