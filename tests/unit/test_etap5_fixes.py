"""Regresje z adwersaryjnego przeglądu Etapu 5 (API + launcher + konsola).

Pokrywa: uwierzytelnianie Bearer + RBAC, mapowanie błędów routera na kody HTTP,
spójność liczników usage/audyt, przebudowę orkiestratora po nadpisaniu runtime,
walidację parametru ``limit`` audytu, malformed body, fail-closed launchera oraz
atomowość łańcucha audytu pod współbieżnością.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.config.errors import ConfigError
from husarz.launcher.cli import _cmd_up, _resolve_api_token, build_parser
from husarz.orchestrator import (
    PHASE_PLAN,
    PHASE_REFLECT,
    PHASE_SYNTH,
    build_orchestrator,
)
from husarz.router import ChatResponse
from husarz.router.errors import (
    AllModelsFailedError,
    NoModelAvailableError,
    RateLimitExceededError,
)
from husarz.security import AuditLog

pytestmark = pytest.mark.unit

_CONSOLE = (
    Path(__file__).resolve().parents[2] / "src" / "husarz" / "api" / "static" / "console.html"
)


class ScriptedRouter:
    """Router testowy: odpowiada wg fazy, echo dla specjalistów (bez sieci)."""

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        content = request.messages[-1].content
        if PHASE_PLAN in content:
            return ChatResponse(
                model="glm", content='{"steps": [{"agent": "bielik", "task": "x"}]}'
            )
        if PHASE_REFLECT in content:
            return ChatResponse(model="glm", content='{"done": true}')
        if PHASE_SYNTH in content:
            return ChatResponse(model="glm", content="Gotowe.")
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] ok")


class RaisingRouter:
    """Router, który zawsze rzuca podany wyjątek (symulacja awarii backendu)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        raise self._exc


def _app(repo_config_dir: Path, **kwargs):  # noqa: ANN003
    config = load_config(repo_config_dir)
    kwargs.setdefault("router", ScriptedRouter())
    return create_app(
        config,
        config_dir=repo_config_dir,
        audit=AuditLog(),
        prompts_dir=repo_config_dir.parent / "prompts",
        **kwargs,
    )


# --- Uwierzytelnianie Bearer + RBAC (blocker: brak auth) --------------------


def test_auth_disabled_without_token(repo_config_dir: Path) -> None:
    # Bez api_token API działa jak dotąd (tryb dev/loopback) — brak regresji.
    client = TestClient(_app(repo_config_dir))
    assert client.get("/api/agents").status_code == 200


def test_auth_required_when_token_set(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir, api_token="s3cret"))
    assert client.get("/api/health").status_code == 200  # liveness zawsze otwarte
    assert client.get("/api/agents").status_code == 401  # brak nagłówka
    assert client.get("/api/agents", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/agents", headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_rbac_operator_cannot_write_config(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir, api_token="s3cret"))  # rola domyślna: operator
    hdr = {"Authorization": "Bearer s3cret"}
    assert client.post("/api/orchestrate", json={"task": "x"}, headers=hdr).status_code == 200
    # operator ma config:read/agent:run, ale NIE config:write.
    denied = client.post("/api/config/runtime", json={"overrides": {}}, headers=hdr)
    assert denied.status_code == 403


def test_rbac_viewer_cannot_orchestrate(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir, api_token="s3cret", api_role="viewer"))
    hdr = {"Authorization": "Bearer s3cret"}
    assert client.get("/api/config/summary", headers=hdr).status_code == 200
    assert client.post("/api/orchestrate", json={"task": "x"}, headers=hdr).status_code == 403


def test_rbac_admin_can_write_config(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir, api_token="s3cret", api_role="admin"))
    hdr = {"Authorization": "Bearer s3cret"}
    ok = client.post("/api/config/runtime", json={"overrides": {}}, headers=hdr)
    assert ok.status_code == 200


# --- Mapowanie błędów routera na kody HTTP (major: gołe 500) ----------------


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (RateLimitExceededError("limit"), 429),
        (NoModelAvailableError("brak"), 503),
        (AllModelsFailedError([]), 502),
    ],
)
def test_orchestrate_maps_router_errors(repo_config_dir: Path, exc: Exception, status: int) -> None:
    client = TestClient(_app(repo_config_dir, router=RaisingRouter(exc)))
    assert client.post("/api/orchestrate", json={"task": "x"}).status_code == status


# --- Liczniki usage/audyt spójne przy porażce (major) ----------------------


def test_usage_counts_attempts_and_failures(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir, router=RaisingRouter(RateLimitExceededError("x"))))
    assert client.get("/api/usage").json()["orchestrations"] == 0
    client.post("/api/orchestrate", json={"task": "x"})  # 429
    usage = client.get("/api/usage").json()
    assert usage["orchestrations"] == 1  # próba policzona (spójnie z audytem)
    assert usage["failures"] == 1


def test_usage_increments_on_success(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir))
    client.post("/api/orchestrate", json={"task": "x"})
    usage = client.get("/api/usage").json()
    assert usage["orchestrations"] == 1
    assert usage["failures"] == 0


# --- Przebudowa orkiestratora po nadpisaniu runtime (major) -----------------


def test_config_runtime_rebuilds_orchestrator(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    prompts = repo_config_dir.parent / "prompts"
    calls = {"n": 0}

    def factory(cfg):  # noqa: ANN001
        calls["n"] += 1
        return build_orchestrator(cfg, ScriptedRouter(), prompts_dir=prompts)

    app = create_app(
        config,
        config_dir=repo_config_dir,
        audit=AuditLog(),
        orchestrator_factory=factory,
        prompts_dir=prompts,
    )
    client = TestClient(app)
    assert calls["n"] == 1  # zbudowany raz przy starcie
    applied = client.post(
        "/api/config/runtime", json={"overrides": {"platform": {"log_level": "DEBUG"}}}
    )
    assert applied.json()["ok"] is True
    assert applied.json()["summary"]["log_level"] == "DEBUG"
    assert calls["n"] == 2  # przebudowany po nadpisaniu (nie działa na starej konfiguracji)
    # zmiana widoczna także w audycie i podsumowaniu
    assert client.get("/api/config/summary").json()["log_level"] == "DEBUG"
    assert any(
        e["action"] == "config.runtime_override" for e in client.get("/api/audit").json()["entries"]
    )


# --- Walidacja parametru limit audytu (minor) -------------------------------


def test_audit_limit_zero_and_negative(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir))
    client.post("/api/orchestrate", json={"task": "x"})
    zero = client.get("/api/audit?limit=0")
    assert zero.status_code == 200
    assert zero.json()["entries"] == []  # limit=0 → pusto, NIE „wszystko"
    assert zero.json()["count"] >= 1  # ale całkowita liczba nadal raportowana
    assert client.get("/api/audit?limit=-1").status_code == 422  # zakres pilnowany


# --- Malformed / źle otypowane body (minor) ---------------------------------


def test_validate_wrong_typed_overrides_returns_422(repo_config_dir: Path) -> None:
    client = TestClient(_app(repo_config_dir))
    resp = client.post("/api/config/validate", json={"overrides": "to nie jest obiekt"})
    assert resp.status_code == 422


# --- Launcher: fail-closed + rozwiązanie tokenu (major/minor) ---------------


def test_up_refuses_non_loopback_without_token(repo_config_dir: Path) -> None:
    args = build_parser().parse_args(
        [
            "up",
            "--config",
            str(repo_config_dir),
            "--host",
            "0.0.0.0",  # noqa: S104 - literał celowo testowany (odmowa startu)
            "--profile",
            "dev",
        ]
    )
    assert _cmd_up(args) == 2  # otwarte control-plane bez tokenu = odmowa startu


def test_up_loopback_builds_app_without_serving(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, kw=kw))
    prompts = repo_config_dir.parent / "prompts"
    args = build_parser().parse_args(
        ["up", "--config", str(repo_config_dir), "--host", "127.0.0.1", "--prompts", str(prompts)]
    )
    assert _cmd_up(args) == 0
    assert "app" in served  # aplikacja zbudowana i przekazana do uvicorn (nie uruchamiamy serwera)


def test_up_allow_insecure_permits_open_bind(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Świadome --allow-insecure pozwala na nasłuch poza loopbackiem bez tokenu.
    import uvicorn

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, kw=kw))
    prompts = repo_config_dir.parent / "prompts"
    args = build_parser().parse_args(
        [
            "up",
            "--config",
            str(repo_config_dir),
            "--host",
            "0.0.0.0",  # noqa: S104 - literał celowo testowany (opt-out)
            "--allow-insecure",
            "--prompts",
            str(prompts),
        ]
    )
    assert _cmd_up(args) == 0
    assert "app" in served


def test_resolve_api_token_from_env(repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUSARZ_TEST_TOKEN", "  z-sekretu  ")
    config = load_config(
        repo_config_dir,
        runtime_overrides={"security": {"auth": {"api_token_ref": "env:HUSARZ_TEST_TOKEN"}}},
    )
    assert _resolve_api_token(config) == "z-sekretu"  # zwrócony i przycięty


def test_resolve_api_token_none_when_unset(repo_config_dir: Path) -> None:
    assert _resolve_api_token(load_config(repo_config_dir)) is None


def test_resolve_api_token_fails_closed_when_unresolvable(repo_config_dir: Path) -> None:
    config = load_config(
        repo_config_dir,
        runtime_overrides={"security": {"auth": {"api_token_ref": "env:BRAK_TAKIEGO_SEKRETU"}}},
    )
    with pytest.raises(ConfigError):
        _resolve_api_token(config)


# --- Konsola: escapowanie XSS (blocker) — regresja na źródle ----------------


def test_console_escapes_interpolated_data() -> None:
    html = _CONSOLE.read_text(encoding="utf-8")
    assert "const esc =" in html
    # Dane z API renderowane w innerHTML muszą przechodzić przez esc().
    for token in ("esc(x.name)", "esc(x.model)", "esc(e.actor)", "esc(e.action)"):
        assert token in html, f"brak escapowania: {token}"


# --- Audyt: atomowość łańcucha pod współbieżnością (major) ------------------


def test_audit_chain_atomic_under_threads() -> None:
    log = AuditLog()
    threads_count, per_thread = 16, 40

    def worker(idx: int) -> None:
        for i in range(per_thread):
            log.record("t", f"a{idx}-{i}", {"i": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert log.verify() is True  # brak zerwania łańcucha mimo równoległości
    assert len(log.entries) == threads_count * per_thread
