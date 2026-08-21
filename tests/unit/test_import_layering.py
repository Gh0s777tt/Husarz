"""Warstwy importów — moduły niskopoziomowe nie mogą zależeć od wyższych.

`husarz.ssrf` (pinowanie IP, ADR-0020) jest warstwą NIŻSZĄ niż router modeli: korzystają
z niego narzędzia, wtyczki MCP, klient Gita i embedder pamięci. Zgłaszał jednak `EgressError`
importowany z `husarz.router.egress`, a import podmodułu pociąga import pakietu nadrzędnego —
powstawał cykl `ssrf → router → client → ssrf`.

Działało to WYŁĄCZNIE dlatego, że router bywał w praktyce importowany pierwszy. Każdy nowy
moduł sięgający do `husarz.ssrf` przed routerem wywracał się na `ImportError`; zdarzyło się
to czterokrotnie, zanim przyczynę usunięto. Ten test pilnuje, żeby nie wróciła.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

# Moduły, które MUSZĄ dać się zaimportować jako pierwszy moduł Husarza w świeżym procesie.
# Świeży proces jest tu istotny: w obrębie jednej sesji pytest inny test mógł już wciągnąć
# router i zamaskować cykl.
_NISKOPOZIOMOWE = [
    "husarz.ssrf",
    "husarz.core.errors",
    "husarz.config.net",
    "husarz.config.evals",
    "husarz.launcher.diagnostics",
    "husarz.runs",
]


@pytest.mark.parametrize("modul", _NISKOPOZIOMOWE)
def test_low_level_module_imports_standalone(modul: str) -> None:
    """Import w ŚWIEŻYM procesie, bez wcześniejszego wciągnięcia routera."""
    wynik = subprocess.run(  # noqa: S603 - interpreter bieżącego środowiska, argumenty stałe
        [sys.executable, "-c", f"import {modul}"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert wynik.returncode == 0, f"{modul}: {wynik.stderr.strip()[-400:]}"


def test_ssrf_does_not_import_router() -> None:
    """Zależność w tę stronę odtworzyłaby cykl — pilnujemy jej na poziomie ŹRÓDŁA."""
    from pathlib import Path

    zrodlo = Path("src/husarz/ssrf.py").read_text(encoding="utf-8")
    linie_importu = [
        line for line in zrodlo.splitlines() if line.startswith(("from husarz", "import husarz"))
    ]
    assert linie_importu, "test byłby pusty, gdyby ssrf nie importował niczego z husarza"
    assert not any("husarz.router" in line for line in linie_importu), linie_importu


def test_egress_error_identity_is_preserved() -> None:
    """Re-eksport nie może stworzyć DRUGIEJ klasy — `except`/`isinstance` przestałyby działać."""
    from husarz.core.errors import EgressError as z_core
    from husarz.core.errors import RouterError
    from husarz.router.egress import EgressError as z_routera

    assert z_routera is z_core
    assert issubclass(z_core, RouterError)
    # `husarz.api.app` łapie RouterError i mapuje go na kod HTTP — blokada egress musi się łapać.
    assert isinstance(z_core("test"), RouterError)


# --- Hierarchia warstw (niezmiennik architektoniczny) -----------------------

# Warstwy wg `docs/ARCHITEKTURA.md`, sekcja „Warstwy importów". Moduł NIŻSZEJ warstwy
# nie może importować z WYŻSZEJ — złamanie tej reguły tworzy cykl, który działa dopóty,
# dopóki ktoś importuje moduły we właściwej kolejności.
_WARSTWY: dict[str, int] = {
    "core": 0,
    "config": 1,
    "ssrf": 2,
    "fencing": 2,
    "textjson": 2,
    # Sanityzacja załączników czatu: zależy od `config` i `fencing`, konsumuje ją wyłącznie API.
    "attachments": 2,
    "router": 3,
    "tools": 3,
    "security": 3,
    "plugins": 3,
    "git": 3,
    "memory": 3,
    "accounts": 3,
    "runs": 3,
    "agents": 4,
    "orchestrator": 4,
    "eval": 4,
    "api": 5,
    "launcher": 5,
}


def _warstwa(modul: str) -> int | None:
    """Numer warstwy dla modułu ``husarz.X...``; ``None`` dla obcych."""
    czesci = modul.split(".")
    return _WARSTWY.get(czesci[1]) if len(czesci) > 1 and czesci[0] == "husarz" else None


def test_no_module_imports_from_a_higher_layer() -> None:
    """Automatyczny odpowiednik tabeli warstw z dokumentacji.

    Poprzedni test pilnował sześciu WYBRANYCH modułów. Ten sprawdza CAŁE drzewo, więc
    złapie kolejny cykl, zanim ktoś na niego wpadnie — a nie po czwartym razie, jak
    było z `husarz.ssrf`.
    """
    import ast
    from pathlib import Path as _Path

    naruszenia: list[str] = []
    for plik in _Path("src/husarz").rglob("*.py"):
        if plik.name.startswith("._"):  # sidecary AppleDouble na wolumenach bez xattr
            continue
        wlasny = "husarz." + str(plik.relative_to("src/husarz")).replace("/", ".").removesuffix(
            ".py"
        )
        moja = _warstwa(wlasny)
        if moja is None:
            continue
        for wezel in ast.walk(ast.parse(plik.read_text(encoding="utf-8"))):
            cele: list[str] = []
            if isinstance(wezel, ast.ImportFrom) and wezel.module and wezel.level == 0:
                cele = [wezel.module]
            elif isinstance(wezel, ast.Import):
                cele = [a.name for a in wezel.names if a.name.startswith("husarz.")]
            for cel in cele:
                ich = _warstwa(cel)
                if ich is not None and ich > moja:
                    naruszenia.append(f"{wlasny} (warstwa {moja}) → {cel} (warstwa {ich})")
    assert not naruszenia, "import z wyższej warstwy:\n  " + "\n  ".join(naruszenia)


def test_layer_table_covers_every_package() -> None:
    """Nowy pakiet MUSI dostać warstwę — inaczej powyższy test cicho go pomija."""
    from pathlib import Path as _Path

    pakiety = {
        p.name
        for p in _Path("src/husarz").iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    }
    moduly = {p.stem for p in _Path("src/husarz").glob("*.py") if not p.name.startswith(("_", "."))}
    brakujace = (pakiety | moduly) - set(_WARSTWY)
    assert not brakujace, f"dopisz warstwę w _WARSTWY i w docs/ARCHITEKTURA.md: {sorted(brakujace)}"
