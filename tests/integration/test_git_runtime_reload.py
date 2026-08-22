"""Integracja: nadpisanie runtime propaguje politykę egress do serwisu Git — 0 sieci/DNS.

Regresja na fail-open kill-switcha (adwersaryjny przegląd Etapu 15b): `git_service` musi być
PRZEBUDOWANY z bieżącego configu przy ``POST /api/config/runtime`` (przez ``git_service_factory``).
Bez tego zmiana polityki egress — łącznie z przełączeniem na profil ``airgap`` — nie obowiązywała
aż do restartu, mimo że CAŁA warstwa anty-SSRF opiera się na tej bramce, a jest to jedyna ścieżka
wychodząca niosąca token z prawem ZAPISU do repozytoriów.

Drugi niezmiennik: przebudowa NIE gubi połączeń dodanych przez API (magazyn jest przekazywany
do nowego serwisu), więc utwardzenie nie kasuje danych operatora.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.git import build_git_service
from husarz.git.models import GitConnection, GitProviderKind
from husarz.security import AuditLog

pytestmark = pytest.mark.integration


class FakeGitTransport:
    """Transport testowy — zwraca pustą listę repozytoriów. Bez sieci."""

    def __call__(self, method, target, headers, json, timeout):  # type: ignore[no-untyped-def]
        return 200, []


class FakeSecrets:
    def resolve(self, ref: str) -> str | None:
        return "sekret-pat"


def _fake_resolve(host: str) -> list[str]:
    return ["140.82.121.6"]


def _conn() -> GitConnection:
    return GitConnection(
        name="gh",
        provider=GitProviderKind.GITHUB,
        api_base="https://api.github.com",
        token_ref="env:GH",
    )


def test_git_service_rebuilt_on_runtime_override(repo_config_dir: Path) -> None:
    config = load_config(
        repo_config_dir,
        runtime_overrides={
            "git": {"enabled": True},
            "security": {"egress": {"default_policy": "deny", "allowlist": ["api.github.com"]}},
        },
    )
    built: list[Any] = []

    przekazane_magazyny: list[Any] = []

    def factory(cfg: Any, store: Any) -> Any:
        # Magazyn przychodzi OD API (serwis AKTUALNY), a nie z domknięcia fabryki na
        # serwis z chwili startu. To domknięcie gubiło połączenia, gdy Git był przy
        # starcie wyłączony i włączono go dopiero nadpisaniem runtime.
        przekazane_magazyny.append(store)
        service = build_git_service(
            cfg.git,
            cfg.security,
            secrets=FakeSecrets(),
            transport=FakeGitTransport(),
            resolve=_fake_resolve,
            store=store,
        )
        built.append(service)
        return service

    client = TestClient(
        create_app(
            config,
            config_dir=repo_config_dir,
            audit=AuditLog(),
            git_service_factory=factory,
        )
    )
    # Połączenie dodane przez API (magazyn w pamięci) — musi przeżyć przebudowę.
    client.post(
        "/api/git/connections",
        json={
            "name": "gh",
            "provider": "github",
            "api_base": "https://api.github.com",
            "token_ref": "env:GH",
        },
    )
    assert client.get("/api/git/connections/gh/repos").status_code == 200

    # Zaostrzenie polityki w runtime: allowlista bez hosta dostawcy.
    resp = client.post(
        "/api/config/runtime",
        json={"overrides": {"security": {"egress": {"default_policy": "deny", "allowlist": []}}}},
    )
    assert resp.status_code == 200 and resp.json()["ok"] is True

    # Serwis PRZEBUDOWANY z nowego configu → egress blokuje (403).
    # Bez fabryki byłoby dalej 200 — fail-open kill-switch.
    assert client.get("/api/git/connections/gh/repos").status_code == 403
    # ...a połączenie NIE zniknęło przy przebudowie (magazyn przekazany dalej).
    assert [c["name"] for c in client.get("/api/git/connections").json()] == ["gh"]


def test_git_service_without_factory_stays_static(repo_config_dir: Path) -> None:
    """Bez fabryki (testy/back-compat) serwis pozostaje statyczny — zachowanie niezmienione."""
    config = load_config(repo_config_dir, runtime_overrides={"git": {"enabled": True}})
    service = build_git_service(
        config.git,
        config.security,
        secrets=FakeSecrets(),
        transport=FakeGitTransport(),
        resolve=_fake_resolve,
    )
    service.add(_conn())
    client = TestClient(
        create_app(config, config_dir=repo_config_dir, audit=AuditLog(), git_service=service)
    )
    assert client.get("/api/git/connections").status_code == 200
