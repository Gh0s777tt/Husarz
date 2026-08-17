"""Kontrolowana ekspozycja szczegółów audytu przez API (allowlista, deny-by-default).

Dziennik na dysku niesie PEŁNY kontekst zdarzenia — w tym argumenty wywołania narzędzia,
rozmiar odpowiedzi i przypięty adres IP. `GET /api/audit` czyta natomiast każdy z uprawnieniem
``audit:read``, więc wystawiamy wyłącznie to, co odpowiada na pytanie o rozliczalność:
KTÓRE narzędzie, JAKA akcja, CZY się powiodło.

Testy pilnują trzech rzeczy, w tej kolejności ważności:

1. **Nic poza allowlistą nie wychodzi** — zwłaszcza ``args`` (treść od modelu: ścieżki,
   zapytania, potencjalnie sekrety) i ``pinned_ip`` (topologia sieci operatora).
2. **Deny-by-default** — akcja spoza mapy nie ujawnia niczego, więc NOWY typ wpisu audytu
   nie zacznie wyciekać payloadu przez przeoczenie.
3. **Wystawiany podzbiór faktycznie dociera** do API i do konsoli — inaczej cała zmiana jest
   niewidoczna dla operatora, jak `principal` przed poprawką z Etapu 13c.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.api.audit_view import public_detail
from husarz.config import load_config
from husarz.security import AuditLog

pytestmark = pytest.mark.security

# Pełny szczegół `tool.call` tak, jak zapisuje go pętla narzędziowa (husarz.agents.tool_loop).
_FULL_TOOL_CALL = {
    "tool": "web",
    "action": "fetch",
    "ok": True,
    "args": {"url": "https://przyklad.example/tajne?token=SEKRET"},
    "bytes": 4096,
    "pinned_ip": "203.0.113.7",
}


# --- 1. Nic poza allowlistą ------------------------------------------------


def test_args_are_never_exposed() -> None:
    """NAJWAŻNIEJSZY niezmiennik: argumenty wywołania nie opuszczają dysku."""
    public = public_detail("tool.call", _FULL_TOOL_CALL)
    assert "args" not in public
    assert "SEKRET" not in str(public)


def test_network_and_size_details_are_not_exposed() -> None:
    """`pinned_ip` (topologia sieci) i `bytes` (kanał boczny o treści) zostają w dzienniku."""
    public = public_detail("tool.call", _FULL_TOOL_CALL)
    assert "pinned_ip" not in public
    assert "bytes" not in public
    assert "203.0.113.7" not in str(public)


def test_nested_values_are_dropped_even_under_allowed_key() -> None:
    """Zagnieżdżona struktura mogłaby przemycić treść pod DOZWOLONĄ nazwą — odrzucamy."""
    public = public_detail("tool.call", {"tool": {"nazwa": "web", "sekret": "SEKRET"}, "ok": True})
    assert "tool" not in public
    assert "SEKRET" not in str(public)
    assert public == {"ok": True}


def test_long_values_are_truncated() -> None:
    """Dozwolone pole nie może być kanałem wycieku przez rozdętą długość."""
    public = public_detail("tool.call", {"tool": "x" * 5000})
    assert len(str(public["tool"])) <= 64


# --- 2. Deny-by-default ----------------------------------------------------


def test_unknown_action_exposes_nothing() -> None:
    """Akcja spoza allowlisty → pusto. To jest stan DOMYŚLNY, nie wyjątek."""
    assert public_detail("chat", {"model": "husarz-local", "prompt": "poufne"}) == {}
    assert public_detail("config.runtime_override", {"keys": ["a"]}) == {}
    assert public_detail("nowa.akcja.z.przyszlosci", {"cokolwiek": "wrazliwe"}) == {}


def test_allowlisted_action_with_empty_detail_is_safe() -> None:
    assert public_detail("tool.call", {}) == {}


# --- 3. Wystawiany podzbiór dociera ----------------------------------------


def test_tool_call_reports_which_tool_and_outcome() -> None:
    assert public_detail("tool.call", _FULL_TOOL_CALL) == {
        "action": "fetch",
        "ok": True,
        "tool": "web",
    }


def test_bool_keeps_its_type_not_collapsed_to_int() -> None:
    """`bool` jest podklasą `int` — nieuważna kolejność sprawdzeń zamieniłaby False na 0."""
    public = public_detail("tool.call", {"ok": False})
    assert public["ok"] is False


def test_deny_reports_reason() -> None:
    """Przy odmowie operator musi wiedzieć, CO poprawić — stąd `reason`."""
    public = public_detail("tool.deny", {"tool": "shell", "action": "run", "reason": "allowlist"})
    assert public == {"action": "run", "reason": "allowlist", "tool": "shell"}


def test_toolloop_limit_reports_threshold() -> None:
    assert public_detail("toolloop.limit", {"max_iterations": 8}) == {"max_iterations": 8}


# --- End-to-end przez API ---------------------------------------------------


def test_api_exposes_tool_name_but_not_args(repo_config_dir: Path) -> None:
    """REGRESJA: konsola pokazywała `tool.call` bez nazwy narzędzia — pytanie o to, KTÓRE
    narzędzie zadziałało, pozostawało bez odpowiedzi. Teraz odpowiada, nadal bez argumentów."""
    audit = AuditLog()
    audit.record("kopijnik", "tool.call", dict(_FULL_TOOL_CALL), principal="user:abc")
    client = TestClient(
        create_app(
            load_config(repo_config_dir),
            audit=audit,
            prompts_dir=repo_config_dir.parent / "prompts",
        )
    )
    body = client.get("/api/audit?limit=5").json()
    entry = body["entries"][-1]
    assert body["verified"] is True
    assert entry["detail"] == {"action": "fetch", "ok": True, "tool": "web"}
    assert "SEKRET" not in client.get("/api/audit?limit=5").text


def test_api_hides_details_of_non_tool_actions(repo_config_dir: Path) -> None:
    """Wpisy `chat` niosą na dysku model i kontekst — przez API nie ujawniamy ich wcale."""
    audit = AuditLog()
    audit.record("api", "chat", {"model": "husarz-local", "poufne": "SEKRET"})
    client = TestClient(
        create_app(
            load_config(repo_config_dir),
            audit=audit,
            prompts_dir=repo_config_dir.parent / "prompts",
        )
    )
    response = client.get("/api/audit?limit=5")
    assert response.json()["entries"][-1]["detail"] == {}
    assert "SEKRET" not in response.text


def test_disk_log_still_carries_everything(repo_config_dir: Path) -> None:
    """Ekspozycja jest WĘŻSZA niż zapis — dziennik na dysku nie może stracić kontekstu."""
    audit = AuditLog()
    audit.record("kopijnik", "tool.call", dict(_FULL_TOOL_CALL))
    stored = audit.entries[-1].detail
    assert stored["args"] == _FULL_TOOL_CALL["args"]
    assert stored["pinned_ip"] == "203.0.113.7"
    assert audit.verify() is True
