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
