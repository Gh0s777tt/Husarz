"""Wspólne fikstury testowe dla Husarza."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from husarz.config import HusarzConfig

# Katalog repo i realny katalog przykładowej konfiguracji (musi walidować się out-of-the-box).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_CONFIG_DIR = PROJECT_ROOT / "config"

# Minimalny, poprawny rejestr modeli (backend "mock" — bez sieci).
MINIMAL_MODELS_YAML = """\
default: m1
registry:
  m1:
    backend: mock
    model: test-model
    tags: [general, code]
  m2:
    backend: mock
    model: test-model-2
    tags: [polish]
"""


@pytest.fixture(autouse=True)
def _no_real_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twardy bezpiecznik: ŻADEN test nie odpytuje realnego DNS.

    Od Etapu 15 ścieżki wychodzące (``web``, wtyczki MCP, Git) rozwiązują nazwy, żeby
    przypiąć adres IP (ADR-0020). Bez tej blokady test z hostem `api.github.com` po cichu
    wychodziłby do sieci — działałby lokalnie, a padał w CI bez WAN i w profilu airgap.
    Resolver jest wstrzykiwalny wszędzie (``resolve=...``), więc poprawny test go podaje;
    ten bezpiecznik zamienia „zapomniałem wstrzyknąć" w natychmiastowy, czytelny błąd.
    """

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "Test odpytał realny DNS (socket.getaddrinfo). Wstrzyknij resolver testowy "
            "przez `resolve=...` (WebTool/PluginService/GitService/build_*)."
        )

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture
def repo_config_dir() -> Path:
    """Ścieżka do przykładowej konfiguracji repo."""
    return REPO_CONFIG_DIR


@pytest.fixture
def write_config(tmp_path: Path):
    """Fabryka: tworzy katalog konfiguracji z podanych plików.

    Użycie:
        cfg = write_config({"models.yaml": "...", "agents/x.yaml": "..."})
    Zwraca ścieżkę katalogu. ``models.yaml`` jest wymagane przez loader.
    """

    def _make(files: dict[str, str]) -> Path:
        config_dir = tmp_path / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            path = config_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return config_dir

    return _make


@pytest.fixture
def minimal_config_dir(write_config) -> Path:
    """Najmniejsza poprawna konfiguracja (tylko wymagane models.yaml)."""
    return write_config({"models.yaml": MINIMAL_MODELS_YAML})


@pytest.fixture
def make_config():
    """Fabryka obiektów ``HusarzConfig`` do testów routera.

    Buduje konfigurację wprost z podanych struktur (rejestr modeli + routing),
    z pominięciem plików YAML. Walidacja krzyżowa nadal obowiązuje.
    """

    def _make(
        *,
        registry: dict[str, Any],
        default: str,
        chat: str | None = None,
        agent_models: dict[str, str] | None = None,
        rules: list[dict[str, Any]] | None = None,
        fallbacks_enabled: bool = True,
        cost_controls: dict[str, Any] | None = None,
    ) -> HusarzConfig:
        return HusarzConfig(
            models={
                "default": default,
                "registry": registry,
                # `chat` bywa przedmiotem testu (diagnoza modelu trybu czatu); domyślnie
                # pada na model domyślny, tak jak w dostarczonej konfiguracji.
                **({"chat": chat} if chat is not None else {}),
            },
            routing={
                "agent_models": agent_models or {},
                "rules": rules or [],
                "fallbacks_enabled": fallbacks_enabled,
                "cost_controls": cost_controls or {},
            },
        )

    return _make
