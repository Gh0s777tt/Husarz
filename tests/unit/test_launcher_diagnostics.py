"""Kontrola startowa: model celujący w port, który zajmuje sam Husarz.

Pułapka jest wbudowana w dostarczoną konfigurację: launcher domyślnie nasłuchuje na 8000
(`--port`), a `config/models.yaml` ma endpoint vLLM na `http://localhost:8000/v1`. Kto
uruchomi jedno i drugie zgodnie z naszą własną dokumentacją, temu żądanie do modelu wróci
do API Husarza — i dostanie mylący błąd zamiast diagnozy.

Testy pilnują obu stron kontraktu: że kolizja JEST wykrywana i że kontrola NIE krzyczy
fałszywie (zdalny backend, model wyłączony, inny port, nasłuch nie-loopback). Fałszywy
alarm w komunikacie startowym jest kosztowny — uczy operatora ignorować ostrzeżenia.
"""

from __future__ import annotations

from pathlib import Path

from husarz.config import load_config
from husarz.launcher.diagnostics import format_port_conflicts, port_conflicts


def _with_endpoint(config, model_id: str, endpoint: str):  # type: ignore[no-untyped-def]
    """Zwraca kopię configu ze zmienionym endpointem jednego modelu."""
    spec = config.models.registry[model_id].model_copy(update={"endpoint": endpoint})
    registry = {**config.models.registry, model_id: spec}
    return config.model_copy(
        update={"models": config.models.model_copy(update={"registry": registry})}
    )


# --- Wykrywanie kolizji -----------------------------------------------------


def test_shipped_config_collides_on_default_port(repo_config_dir: Path) -> None:
    """DOSTARCZONY config + DOMYŚLNY port = kolizja. To jest właśnie ten realny przypadek."""
    config = load_config(repo_config_dir)
    conflicts = port_conflicts(config, host="127.0.0.1", port=8000)
    assert conflicts, "config/models.yaml ma endpoint na :8000 — kontrola musi to złapać"
    assert all(c.port == 8000 for c in conflicts)


def test_wildcard_bind_also_collides(repo_config_dir: Path) -> None:
    """Nasłuch na 0.0.0.0 obejmuje loopback, więc endpoint na localhost NADAL koliduje."""
    config = load_config(repo_config_dir)
    # noqa S104: argument kontroli diagnostycznej, nie adres nasłuchu — test niczego nie binduje.
    assert port_conflicts(config, host="0.0.0.0", port=8000)  # noqa: S104


def test_conflicts_are_sorted_and_deduplicated_per_model(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    conflicts = port_conflicts(config, host="127.0.0.1", port=8000)
    ids = [c.model_id for c in conflicts]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))


# --- Brak fałszywych alarmów ------------------------------------------------


def test_no_conflict_on_different_port(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    assert port_conflicts(config, host="127.0.0.1", port=8079) == []


def test_remote_backend_is_not_a_conflict(repo_config_dir: Path) -> None:
    """`http://gpu-box:8000` to INNA maszyna — ostrzeżenie byłoby fałszywe."""
    config = load_config(repo_config_dir)
    model_id = next(
        m for m, s in load_config(repo_config_dir).models.registry.items() if s.endpoint
    )
    patched = _with_endpoint(config, model_id, "http://gpu-box:8000/v1")
    assert all(c.model_id != model_id for c in port_conflicts(patched, host="127.0.0.1", port=8000))


def test_non_loopback_bind_reports_nothing(repo_config_dir: Path) -> None:
    """Nasłuch na konkretnym adresie LAN nie zajmuje loopbacku — brak kolizji z localhost."""
    config = load_config(repo_config_dir)
    assert port_conflicts(config, host="192.168.1.10", port=8000) == []


def test_disabled_model_is_ignored(repo_config_dir: Path) -> None:
    """Model wyłączony nie zostanie użyty — ostrzeganie o nim to szum."""
    config = load_config(repo_config_dir)
    colliding = port_conflicts(config, host="127.0.0.1", port=8000)[0].model_id
    spec = config.models.registry[colliding].model_copy(update={"enabled": False})
    registry = {**config.models.registry, colliding: spec}
    patched = config.model_copy(
        update={"models": config.models.model_copy(update={"registry": registry})}
    )
    assert all(
        c.model_id != colliding for c in port_conflicts(patched, host="127.0.0.1", port=8000)
    )


def test_malformed_endpoint_does_not_raise(repo_config_dir: Path) -> None:
    """Kontrola startowa NIGDY nie wywraca startu — chory URL to sprawa walidacji configu."""
    config = load_config(repo_config_dir)
    model_id = next(m for m, s in config.models.registry.items() if s.endpoint)
    for bad in ("http://host:99999/v1", "nie-url", "", "http:///v1"):
        patched = _with_endpoint(config, model_id, bad)
        port_conflicts(patched, host="127.0.0.1", port=8000)  # nie może rzucić


# --- Komunikat --------------------------------------------------------------


def test_message_names_model_port_and_remedy(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    conflicts = port_conflicts(config, host="127.0.0.1", port=8000)
    lines = format_port_conflicts(conflicts, port=8000)
    joined = "\n".join(lines)
    assert "8000" in joined
    assert conflicts[0].model_id in joined
    assert "--port" in joined and "config/models.yaml" in joined


def test_no_message_without_conflicts() -> None:
    assert format_port_conflicts([], port=8000) == []
