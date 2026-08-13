"""Loader konfiguracji Husarza.

Implementuje hierarchię nadpisań (od najniższego do najwyższego priorytetu):

    defaults (kod / Pydantic)
      -> config/*.yaml
        -> ENV (HUSARZ_*)
          -> sekrety (dostawca)          # rozwiązywane referencjami w runtime
            -> nadpisania runtime (panel)

Loader nie zawiera żadnych wartości "na sztywno". Wszystkie domyślne wartości
pochodzą ze schematu (Pydantic). Błędy dają czytelne komunikaty, nie crash.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from husarz.config.errors import (
    ConfigFileNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    format_validation_error,
)
from husarz.config.schema import HusarzConfig

DEFAULT_CONFIG_DIR = Path("./config")
ENV_PREFIX = "HUSARZ_"
NESTED_DELIMITER = "__"
CONFIG_DIR_ENV = "HUSARZ_CONFIG_DIR"

# Mapowanie: nazwa sekcji -> plik w katalogu config.
_SINGLE_FILES: dict[str, str] = {
    "platform": "husarz.yaml",
    "models": "models.yaml",
    "routing": "routing.yaml",
    "security": "security.yaml",
    "chat": "chat.yaml",
    "git": "git.yaml",
}
# Sekcje wieloplikowe: nazwa sekcji -> (podkatalog, pole-klucz).
_MULTI_DIRS: dict[str, tuple[str, str]] = {
    "agents": ("agents", "name"),
    "tools": ("tools", "name"),
    "plugins": ("plugins", "name"),
    "roe": ("roe", "engagement_id"),
}


# ---------------------------------------------------------------------------
# Pomocnicze: YAML + deep-merge
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    """Wczytuje plik YAML jako słownik. Pusty plik = pusty słownik."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - rzadka ścieżka I/O
        raise ConfigParseError(f"Nie można odczytać pliku: {path} ({exc}).") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigParseError(f"Błąd składni YAML w pliku {path}:\n{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigParseError(
            f"Plik {path} musi zawierać mapę (klucz: wartość) na najwyższym poziomie, "
            f"a nie {type(data).__name__}."
        )
    return data


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Rekurencyjnie scala ``override`` na ``base`` (zwraca nowy słownik).

    Słowniki są scalane rekurencyjnie; skalary i listy — nadpisywane w całości.
    """
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, Mapping):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Wczytywanie plików konfiguracji do surowego słownika
# ---------------------------------------------------------------------------


def _load_raw_from_dir(config_dir: Path) -> dict[str, Any]:
    """Buduje surowy słownik konfiguracji z plików w katalogu."""
    if not config_dir.is_dir():
        raise ConfigFileNotFoundError(f"Katalog konfiguracji nie istnieje: {config_dir.resolve()}.")

    raw: dict[str, Any] = {}

    # Sekcje jednoplikowe (wszystkie opcjonalne na tym etapie — wymóg 'models'
    # jest sprawdzany po scaleniu warstw, zgodnie z hierarchią nadpisań).
    for section, filename in _SINGLE_FILES.items():
        path = config_dir / filename
        if path.is_file():
            raw[section] = _read_yaml(path)

    # Sekcje wieloplikowe (agents/tools/roe).
    for section, (subdir, key_field) in _MULTI_DIRS.items():
        directory = config_dir / subdir
        if not directory.is_dir():
            continue
        collected: dict[str, Any] = {}
        for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
            entry = _read_yaml(path)
            # Normalizacja do str przed sprawdzeniem duplikatu i zapisem — inaczej
            # klucz liczbowy (np. engagement_id: 42) ominąłby detekcję duplikatów.
            key = str(entry.get(key_field) or path.stem)
            if key in collected:
                raise ConfigValidationError(
                    f"Zduplikowany klucz '{key}' w sekcji '{section}' "
                    f"(plik {path.name}). Klucze muszą być unikalne."
                )
            collected[key] = entry
        if collected:
            raw[section] = collected

    return raw


# ---------------------------------------------------------------------------
# Nadpisania ze zmiennych środowiskowych (HUSARZ_*)
# ---------------------------------------------------------------------------


# Znane sekcje najwyższego poziomu — tylko one są przyjmowane z ENV.
_KNOWN_SECTIONS: frozenset[str] = frozenset(_SINGLE_FILES) | frozenset(_MULTI_DIRS)
# Pola-kolekcje: segment BEZPOŚREDNIO po nich to klucz mapy (id modelu / nazwa
# agenta itd.) i zachowuje oryginalną wielkość liter zamiast być zapisany małymi.
_COLLECTION_FIELDS: frozenset[str] = frozenset(
    {"registry", "agents", "tools", "plugins", "roe", "agent_models"}
)


def _coerce_env_value(value: str) -> Any:
    """Parsuje wartość ENV będącą listą/obiektem JSON (prefiks ``[`` lub ``{``).

    Pozostałe wartości pozostają stringiem — typowanie skalarów (int/bool/enum)
    wykonuje Pydantic przy walidacji. Celowo NIE konwertujemy gołych liczb/bool,
    aby nie psuć pól tekstowych (np. ``cpu_limit: "2"``). Niepoprawny JSON w
    nawiasie jest zwracany jako surowy string (czytelny błąd da dopiero walidacja).
    """
    stripped = value.strip()
    if stripped[:1] in ("[", "{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_env_segments(parts: list[str]) -> list[str]:
    """Normalizuje segmenty ścieżki ENV.

    Nazwy pól schematu zapisujemy małymi literami; klucze map (segment po polu-
    kolekcji, np. po ``registry``) zachowują oryginalną wielkość liter, aby dało
    się nadpisać wpis o identyfikatorze zawierającym wielkie litery.
    """
    segments: list[str] = []
    prev: str | None = None
    for part in parts:
        if part == "":
            continue
        seg = part if prev in _COLLECTION_FIELDS else part.lower()
        segments.append(seg)
        prev = seg
    return segments


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Buduje słownik nadpisań ze zmiennych ENV o prefiksie ``HUSARZ_``.

    Ścieżkę zagnieżdżenia wyznacza delimiter ``__``. Przyjmowane są wyłącznie
    zmienne, których pierwszy segment jest znaną sekcją (patrz ``_KNOWN_SECTIONS``)
    — dzięki temu obca zmienna jak ``HUSARZ_HOME`` jest ignorowana, a nie wywraca
    startu. ``HUSARZ_CONFIG_DIR`` steruje lokalizacją, nie treścią, więc jest pomijane.
    """
    result: dict[str, Any] = {}
    for raw_key, raw_value in env.items():
        if not raw_key.startswith(ENV_PREFIX) or raw_key == CONFIG_DIR_ENV:
            continue
        parts = raw_key[len(ENV_PREFIX) :].split(NESTED_DELIMITER)
        segments = _normalize_env_segments(parts)
        if not segments or segments[0] not in _KNOWN_SECTIONS:
            continue
        cursor = result
        for seg in segments[:-1]:
            nxt = cursor.get(seg)
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[seg] = nxt
            cursor = nxt
        cursor[segments[-1]] = _coerce_env_value(raw_value)
    return result


# ---------------------------------------------------------------------------
# Publiczne API
# ---------------------------------------------------------------------------


def resolve_config_dir(
    config_dir: str | Path | None,
    env: Mapping[str, str],
) -> Path:
    """Ustala katalog konfiguracji: argument -> ENV -> domyślny ``./config``."""
    if config_dir is not None:
        return Path(config_dir)
    from_env = env.get(CONFIG_DIR_ENV)
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_DIR


def load_config(
    config_dir: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
    runtime_overrides: Mapping[str, Any] | None = None,
) -> HusarzConfig:
    """Wczytuje i waliduje kompletną konfigurację Husarza.

    Args:
        config_dir: Katalog z plikami YAML. Gdy ``None`` — z ENV lub ``./config``.
        env: Środowisko do odczytu (domyślnie ``os.environ``). Wstrzykiwalne w testach.
        runtime_overrides: Najwyższy priorytet — nadpisania z panelu/runtime.

    Returns:
        Zwalidowany obiekt ``HusarzConfig``.

    Raises:
        ConfigFileNotFoundError: Brak katalogu lub wymaganego pliku.
        ConfigParseError: Błąd składni YAML.
        ConfigValidationError: Konfiguracja nie przeszła walidacji (czytelny komunikat).
    """
    env = os.environ if env is None else env
    resolved_dir = resolve_config_dir(config_dir, env)

    # 1) defaults + config/*.yaml
    raw = _load_raw_from_dir(resolved_dir)

    # 2) ENV (HUSARZ_*)
    raw = _deep_merge(raw, _env_overrides(env))

    # 3) nadpisania runtime (panel) — najwyższy priorytet
    if runtime_overrides:
        raw = _deep_merge(raw, runtime_overrides)

    # Rejestr modeli jest wymagany, ale może pochodzić z DOWOLNEJ warstwy
    # (plik, ENV lub runtime) — dlatego sprawdzamy dopiero po scaleniu.
    if "models" not in raw:
        raise ConfigFileNotFoundError(
            f"Brak rejestru modeli. Utwórz {resolved_dir / 'models.yaml'} "
            f"albo dostarcz sekcję 'models' przez ENV/runtime."
        )

    # 4) walidacja schematem
    try:
        return HusarzConfig(**raw)
    except ValidationError as exc:
        raise ConfigValidationError(format_validation_error(exc, source=resolved_dir)) from exc
