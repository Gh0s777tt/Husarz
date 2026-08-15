"""Rozliczanie tokenów orkiestracji wobec limitu konta (Etap 7b).

Do tej pory ``/api/orchestrate`` SPRAWDZAŁ limit (``_enforce_quota``), ale nigdy go NIE
naliczał — rozliczenie (``_record_tokens``) było wyłącznie na ścieżce czatu. Konto z ustawioną
kwotą mogło więc orkiestrować bez końca: `tokens_used` nie rosło, więc `check_quota` nigdy nie
trafiał w próg. A orkiestracja to najdroższy endpoint: plan + N delegacji + refleksja + synteza,
czyli wiele wywołań modelu na jedno żądanie.

Testy pilnują, że zużycie jest sumowane ze WSZYSTKICH faz i że limit realnie domyka pętlę.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.router.types import ChatResponse, Usage, UsageMeter
from husarz.security import AuditLog

pytestmark = pytest.mark.security


class CountingRouter:
    """Router-atrapa: każde wywołanie zgłasza stałe zużycie i zlicza wywołania."""

    def __init__(self, tokens_per_call: int = 10) -> None:
        self.calls = 0
        self._tokens = tokens_per_call

    def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        self.calls += 1
        return ChatResponse(
            model="stub",
            content="Odpowiedź.",
            usage=Usage(
                prompt_tokens=self._tokens,
                completion_tokens=self._tokens,
                total_tokens=self._tokens,
            ),
        )


class SilentRouter:
    """Router, który NIE raportuje zużycia (część backendów tak robi)."""

    def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        return ChatResponse(model="stub", content="Odpowiedź.", usage=None)


# --- UsageMeter -------------------------------------------------------------


def test_meter_sums_across_calls() -> None:
    meter = UsageMeter()
    meter.add(Usage(prompt_tokens=3, completion_tokens=5, total_tokens=8))
    meter.add(Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    snapshot = meter.snapshot()
    assert snapshot is not None
    assert (snapshot.prompt_tokens, snapshot.completion_tokens, snapshot.total_tokens) == (4, 6, 10)


def test_meter_without_any_report_returns_none() -> None:
    """Brak danych z backendu to NIE „zero tokenów" — nie naliczamy zmyślonych wartości."""
    meter = UsageMeter()
    meter.add(None)
    meter.add(Usage())  # wszystkie pola None
    assert meter.snapshot() is None


def test_meter_ignores_missing_fields() -> None:
    meter = UsageMeter()
    meter.add(Usage(total_tokens=7))  # backend podał tylko sumę
    snapshot = meter.snapshot()
    assert snapshot is not None
    assert snapshot.total_tokens == 7 and snapshot.prompt_tokens == 0


# --- Orkiestrator: sumowanie ze wszystkich faz ------------------------------


def _orchestrator(router: Any, repo_config_dir: Path) -> Any:
    from husarz.orchestrator import build_orchestrator

    config = load_config(repo_config_dir)
    return build_orchestrator(config, router, prompts_dir=repo_config_dir.parent / "prompts")


def test_orchestration_sums_usage_from_every_phase(repo_config_dir: Path) -> None:
    """Wynik niesie sumę ze WSZYSTKICH wywołań modelu, nie tylko z ostatniego."""
    router = CountingRouter(tokens_per_call=10)
    result = _orchestrator(router, repo_config_dir).run("zbadaj temat")
    assert router.calls >= 2  # co najmniej plan + synteza
    assert result.usage is not None
    assert result.usage.total_tokens == 10 * router.calls


def test_orchestration_usage_none_when_backend_silent(repo_config_dir: Path) -> None:
    result = _orchestrator(SilentRouter(), repo_config_dir).run("zbadaj temat")
    assert result.usage is None


# --- API: limit realnie domyka pętlę ---------------------------------------
# Używamy PRAWDZIWEGO AccountService (jak testy kont) — atrapa nie pokryłaby ścieżki
# uwierzytelnienia ani zapisu `tokens_used`, a właśnie one decydują o egzekwowaniu limitu.


def _client_with_account(
    repo_config_dir: Path, router: Any, *, quota: int | None
) -> tuple[TestClient, Any, str]:
    """Zwraca ``(klient, serwis kont, nagłówek autoryzacji)`` dla konta z limitem."""
    from husarz.accounts import AccountService

    service = AccountService(allow_registration=True, default_token_quota=quota)
    service.create_account("ala", "haslo-1234", role="operator")
    config = load_config(repo_config_dir)
    client = TestClient(
        create_app(
            config,
            audit=AuditLog(),
            router=router,
            prompts_dir=repo_config_dir.parent / "prompts",
            accounts=service,
        )
    )
    token = client.post(
        "/api/auth/login", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    return client, service, token


def test_orchestrate_records_tokens_against_quota(repo_config_dir: Path) -> None:
    """REGRESJA: endpoint sprawdzał limit, ale go NIE naliczał — kwota była nieegzekwowalna."""
    router = CountingRouter(tokens_per_call=10)
    client, _service, token = _client_with_account(repo_config_dir, router, quota=1_000_000)
    hdr = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/orchestrate", json={"task": "zbadaj"}, headers=hdr).status_code == 200
    me = client.get("/api/auth/me", headers=hdr).json()
    assert me["tokens_used"] == 10 * router.calls
    assert me["tokens_used"] > 0, "orkiestracja MUSI naliczyć zużycie na koncie"


def test_quota_eventually_blocks_orchestration(repo_config_dir: Path) -> None:
    """Skoro zużycie rośnie, limit domyka pętlę — dawniej można było orkiestrować bez końca."""
    client, _service, token = _client_with_account(
        repo_config_dir, CountingRouter(tokens_per_call=10), quota=25
    )
    hdr = {"Authorization": f"Bearer {token}"}

    assert client.post("/api/orchestrate", json={"task": "raz"}, headers=hdr).status_code == 200
    codes = [
        client.post("/api/orchestrate", json={"task": f"próba {i}"}, headers=hdr).status_code
        for i in range(3)
    ]
    assert 402 in codes, f"limit powinien zablokować orkiestrację, kody: {codes}"


def test_silent_backend_does_not_fabricate_usage(repo_config_dir: Path) -> None:
    """Backend bez raportowania zużycia nie może powodować naliczania zmyślonych wartości."""
    client, _service, token = _client_with_account(repo_config_dir, SilentRouter(), quota=100)
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/orchestrate", json={"task": "zbadaj"}, headers=hdr).status_code == 200
    assert client.get("/api/auth/me", headers=hdr).json()["tokens_used"] == 0
