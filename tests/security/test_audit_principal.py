"""Korelacja principal↔wywołanie w audycie (Etap 13c).

Dziennik odpowiadał dotąd na pytanie „kto WYKONAŁ" (`actor`: `kopijnik`, `puszkarz`, `api`),
ale nie „na czyje ŻĄDANIE". Przy jednym operatorze to bez znaczenia; przy wielu kontach audyt
przestaje być śladem rozliczalności — nie da się powiązać wywołania narzędzia z użytkownikiem.

Testy pilnują trzech rzeczy:

1. `principal` jest objęty łańcuchem skrótów (nie da się go dopiąć ani odpiąć bez wykrycia),
2. dzienniki SPRZED tej zmiany nadal przechodzą `verify` (dodanie kolumny nie może wyglądać
   jak manipulacja całą historią),
3. referencja jest po ID konta, nie po nazwie użytkownika (niemodyfikowalny log bez PII).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.router.types import ChatResponse, Usage
from husarz.security import AuditLog
from husarz.security.audit import AuditEntry

pytestmark = pytest.mark.security


def _clock() -> datetime:
    return datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


# --- Łańcuch skrótów --------------------------------------------------------


def test_principal_is_covered_by_hash_chain() -> None:
    """Podmiana `principal` w istniejącym wpisie MUSI unieważnić skrót."""
    log = AuditLog(clock=_clock)
    log.record("kopijnik", "tool.call", {"tool": "shell"}, principal="user:abc")
    assert log.verify() is True

    entry = log.entries[0]
    tampered = AuditEntry(**{**asdict(entry), "principal": "user:ktos-inny"})
    forged = AuditLog(clock=_clock)
    forged._entries.append(tampered)  # noqa: SLF001 - test celowo dotyka wnętrza
    assert forged.verify() is False


def test_stripping_principal_is_detected() -> None:
    """Odpięcie wywołania od użytkownika też jest manipulacją — i też jest wykrywane."""
    log = AuditLog(clock=_clock)
    log.record("kopijnik", "tool.call", {"tool": "shell"}, principal="user:abc")
    stripped = AuditEntry(**{**asdict(log.entries[0]), "principal": ""})
    forged = AuditLog(clock=_clock)
    forged._entries.append(stripped)  # noqa: SLF001
    assert forged.verify() is False


def test_legacy_entries_without_principal_still_verify(tmp_path: Path) -> None:
    """ZGODNOŚĆ WSTECZ: dziennik sprzed dodania kolumny musi nadal przechodzić `verify`.

    Payload pomija `principal`, gdy jest pusty — dzięki temu stare wpisy hashują się
    dokładnie tak jak wcześniej. Inaczej aktualizacja Husarza oznaczałaby, że każdy
    istniejący dziennik nagle wygląda na zmanipulowany.
    """
    # Wpis w formacie SPRZED zmiany: plik JSONL bez pola `principal`.
    source = AuditLog(clock=_clock)
    source.record("api", "chat", {"model": "m"})
    legacy_line = {
        key: value for key, value in asdict(source.entries[0]).items() if key != "principal"
    }
    path = tmp_path / "audit.log"
    path.write_text(json.dumps(legacy_line, ensure_ascii=False) + "\n", encoding="utf-8")

    loaded = AuditLog.load(path)
    assert loaded.entries[0].principal == ""
    assert loaded.verify() is True


def test_chain_continues_across_mixed_entries() -> None:
    """Wpisy z principalem i bez mieszają się w jednym łańcuchu bez fałszywego alarmu."""
    log = AuditLog(clock=_clock)
    log.record("api", "chat", {"model": "m"})
    log.record("kopijnik", "tool.call", {"tool": "shell"}, principal="user:abc")
    log.record("api", "config.runtime_override", {"keys": []}, principal="token:admin")
    assert log.verify() is True
    assert [e.principal for e in log.entries] == ["", "user:abc", "token:admin"]


# --- Referencja wywołującego ------------------------------------------------


def test_principal_ref_uses_account_id_not_username() -> None:
    """Nazwa użytkownika bywa e-mailem — do NIEMODYFIKOWALNEGO logu trafia ID konta."""
    from husarz.accounts import Principal
    from husarz.api.app import _principal_ref

    assert _principal_ref(None) == ""
    ref = _principal_ref(Principal(role="operator", user_id="deadbeef", username="ala@example.com"))
    assert ref == "user:deadbeef"
    assert "ala@example.com" not in ref


def test_machine_token_is_distinguishable_from_user() -> None:
    """Token maszynowy nie ma konta — zapisujemy rolę, by odróżnić go od wywołania człowieka."""
    from husarz.accounts import Principal
    from husarz.api.app import _principal_ref

    assert _principal_ref(Principal(role="operator", user_id=None)) == "token:operator"


# --- End-to-end przez API ---------------------------------------------------


class _StubRouter:
    def complete(self, request: Any, **kwargs: Any) -> ChatResponse:
        return ChatResponse(model="stub", content="ok", usage=Usage(total_tokens=1))


def test_chat_audit_entry_carries_caller(repo_config_dir: Path) -> None:
    from husarz.accounts import AccountService

    service = AccountService(allow_registration=True)
    account = service.create_account("ala", "haslo-1234", role="operator")
    audit = AuditLog()
    client = TestClient(
        create_app(
            load_config(repo_config_dir),
            audit=audit,
            router=_StubRouter(),
            prompts_dir=repo_config_dir.parent / "prompts",
            accounts=service,
        )
    )
    token = client.post(
        "/api/auth/login", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    client.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "cześć"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    chats = [entry for entry in audit.entries if entry.action == "chat"]
    assert chats, "wywołanie czatu musi zostawić wpis"
    assert chats[0].principal == f"user:{account.user_id}"
    assert audit.verify() is True


def test_orchestration_audit_entries_carry_caller(repo_config_dir: Path) -> None:
    """Kluczowy przypadek: wpisy Z GŁĘBI orkiestracji też niosą „na czyje żądanie"."""
    from husarz.accounts import AccountService

    service = AccountService(allow_registration=True)
    account = service.create_account("ala", "haslo-1234", role="operator")
    audit = AuditLog()
    client = TestClient(
        create_app(
            load_config(repo_config_dir),
            audit=audit,
            router=_StubRouter(),
            prompts_dir=repo_config_dir.parent / "prompts",
            accounts=service,
        )
    )
    token = client.post(
        "/api/auth/login", json={"username": "ala", "password": "haslo-1234"}
    ).json()["token"]
    client.post(
        "/api/orchestrate",
        json={"task": "zbadaj temat"},
        headers={"Authorization": f"Bearer {token}"},
    )
    entries = [entry for entry in audit.entries if entry.action == "orchestrate"]
    assert entries and entries[0].principal == f"user:{account.user_id}"
    assert audit.verify() is True
