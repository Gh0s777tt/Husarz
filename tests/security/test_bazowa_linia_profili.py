"""Bazowa linia bezpieczeństwa profili `prod` i `airgap` — twardych wymagań nie wolno wyłączyć.

**Skąd ten plik.** `docs/ARCHITEKTURA.md` obiecuje: „sandbox włączony, audyt włączony
i niemodyfikowalny, szyfrowanie at-rest — **nie można ich cicho wyłączyć**". Walidacja
krzyżowa faktycznie tego pilnuje. Nie pilnował tego natomiast ŻADEN test — sprawdzone
przeszukaniem: ani jeden przypadek nie dotykał tego bloku.

Konsekwencja praktyczna: gdyby ktoś przy refaktorze usunął ten fragment `_cross_validate`,
cały zestaw pozostałby zielony, a obietnica z dokumentacji zamieniłaby się w nieprawdę
bez żadnego sygnału. Wykryte przy przeglądzie testów asercjonujących WARTOŚĆ pola configu
zamiast skutku (Etap 17m).

Każdy test sprawdza SKUTEK — czy konfiguracja daje się wczytać — a nie to, jaka wartość
stoi w pliku.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.errors import ConfigError

pytestmark = pytest.mark.security

_MODELE = """\
default: m
registry:
  m:
    backend: mock
    model: testowy
    tags: [chat]
"""


def _katalog(write_config, profil: str, security: str = "") -> Path:
    """Buduje minimalną konfigurację w danym profilu."""
    return write_config(
        {
            "models.yaml": _MODELE,
            "husarz.yaml": f"profile: {profil}\n",
            "security.yaml": security or "egress:\n  default_policy: deny\n",
        }
    )


@pytest.mark.parametrize("profil", ["prod", "airgap"])
@pytest.mark.parametrize(
    ("security", "fragment"),
    [
        ("sandbox:\n  engine: none\n", "wymaga sandboxa"),
        ("audit:\n  enabled: false\n", "wymaga włączonego audytu"),
        ("audit:\n  immutable: false\n", "wymaga niemodyfikowalnego audytu"),
        ("encryption:\n  at_rest: false\n", "wymaga szyfrowania at-rest"),
    ],
)
def test_profil_nieodwolalny_ODRZUCA_oslabienie(
    write_config, profil: str, security: str, fragment: str
) -> None:
    """Cztery twarde wymagania, każde sprawdzone osobno w obu profilach.

    Parametryzacja jest tu istotna: jeden test na „bazową linię" przechodziłby dalej,
    gdyby ktoś usunął TRZY z czterech warunków, a zostawił jeden.
    """
    katalog = _katalog(write_config, profil, "egress:\n  default_policy: deny\n" + security)

    with pytest.raises(ConfigError) as exc:
        load_config(katalog)

    tresc = str(exc.value)
    assert fragment in tresc, tresc
    assert profil in tresc, "komunikat musi nazwać profil, który stawia to wymaganie"


@pytest.mark.parametrize(
    "security",
    [
        "sandbox:\n  engine: none\n",
        "audit:\n  enabled: false\n",
        "audit:\n  immutable: false\n",
        "encryption:\n  at_rest: false\n",
    ],
)
def test_profil_dev_zostawia_elastycznosc(write_config, security: str) -> None:
    """Nośność: bazowa linia dotyczy profili NIEODWOŁALNYCH, nie każdego.

    Bez tego testu walidator odrzucający wszystko wszędzie przeszedłby powyższe cztery
    przypadki i wyglądał na poprawny — a uniemożliwiłby pracę w dev.
    """
    katalog = _katalog(write_config, "dev", "egress:\n  default_policy: deny\n" + security)

    config = load_config(katalog)

    assert config.platform.profile.value == "dev"


def test_airgap_odrzuca_allowliste_egressu(write_config) -> None:
    """Airgap znaczy brak ruchu wychodzącego — allowlista przeczyłaby samej nazwie profilu."""
    katalog = _katalog(
        write_config, "airgap", "egress:\n  default_policy: deny\n  allowlist: [example.com]\n"
    )

    with pytest.raises(ConfigError) as exc:
        load_config(katalog)

    assert "airgap" in str(exc.value)


def test_dostarczona_konfiguracja_przechodzi_baze_prod(repo_config_dir: Path) -> None:
    """Konfiguracja repo musi dać się podnieść do profilu `prod` bez zmian w plikach.

    Inaczej „profil produkcyjny" byłby czymś, czego operator nie może włączyć bez ręcznego
    dostrajania — a dostarczona konfiguracja przestałaby być punktem wyjścia.
    """
    config = load_config(repo_config_dir, runtime_overrides={"platform": {"profile": "prod"}})

    assert config.platform.profile.value == "prod"
    assert config.security.audit.enabled is True
