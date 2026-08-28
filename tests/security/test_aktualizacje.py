"""Sprawdzanie aktualizacji Husarza (Etap 18o).

**Dlaczego to jest test BEZPIECZEŃSTWA, a nie wygody.** Samo zapytanie o wersję jest
połączeniem WYCHODZĄCYM: ujawnia serwerowi wydań, że ta instalacja istnieje, ma dany adres
IP i konkretną wersję. Projekt deklaruje zero telemetrii, więc mechanizm o takim skutku musi
być domyślnie wyłączony, przechodzić przez własną allowlistę i być odrzucany w profilu
odciętym od sieci. Każdą z tych trzech własności pilnuje osobny test.

Druga rzecz, którą tu utrwalamy, to **trójstan**. „Nie udało się sprawdzić" NIGDY nie może
zaokrąglić się do „masz aktualną wersję" — instalacja, która przez tydzień nie dobiła do
serwera wydań, ma o tym powiedzieć. To ta sama zasada, co w diagnozie (`husarz doctor`),
i tam kosztowała już raz: pomiar, który zaokrągla „nie dało się sprawdzić" do „w porządku",
jest gorszy niż brak pomiaru.
"""

from __future__ import annotations

import pytest

from husarz.config.errors import ConfigError
from husarz.config.loader import load_config
from husarz.config.schema import HusarzConfig, UpdateConfig
from husarz.launcher.aktualizacja import Stan, Wydanie, rozbierz_wersje, sprawdz

pytestmark = pytest.mark.security

_MODELE = "default: m\nregistry:\n  m:\n    backend: mock\n    model: m\n"


class _Zrodlo:
    """Źródło wydań zwracające ustalony wynik — testy nie dotykają sieci."""

    def __init__(self, wydanie: Wydanie | None = None, powod: str = "") -> None:
        self._wydanie = wydanie
        self._powod = powod
        self.pytano = 0

    def najnowsze(self) -> tuple[Wydanie | None, str]:
        """Zwraca zaplanowane wydanie albo powód niepowodzenia."""
        self.pytano += 1
        return self._wydanie, self._powod


def _config(**update: object) -> HusarzConfig:
    return HusarzConfig.model_validate(
        {
            "models": {"default": "m", "registry": {"m": {"backend": "mock", "model": "m"}}},
            "update": update or {},
        }
    )


def _wlaczone() -> HusarzConfig:
    return _config(enabled=True, repository="ktos/husarz", sources=["api.github.com"])


# --------------------------------------------------------------------------------------
# Trójstan
# --------------------------------------------------------------------------------------


def test_nowsza_wersja_jest_wykrywana() -> None:
    """Ścieżka pogodna."""
    wynik = sprawdz(_wlaczone(), "0.14.0", zrodlo=_Zrodlo(Wydanie("v0.15.0", "https://x/1")))

    assert wynik.stan is Stan.DOSTEPNA
    assert wynik.najnowsza == "v0.15.0"
    assert wynik.strona == "https://x/1"


def test_ta_sama_wersja_to_AKTUALNA() -> None:
    """Przedrostek `v` nie może udawać różnicy — tak taguje wydania ten projekt."""
    assert sprawdz(_wlaczone(), "0.14.0", zrodlo=_Zrodlo(Wydanie("v0.14.0", ""))).stan is (
        Stan.AKTUALNA
    )


def test_STARSZA_wersja_u_dostawcy_to_AKTUALNA_a_nie_dostepna() -> None:
    """Wydanie cofnięte (np. wycofane) nie może wyglądać jak aktualizacja."""
    assert sprawdz(_wlaczone(), "0.14.0", zrodlo=_Zrodlo(Wydanie("v0.13.9", ""))).stan is (
        Stan.AKTUALNA
    )


def test_awaria_sieci_daje_NIEZNANY_a_NIE_aktualna() -> None:
    """Sedno trójstanu: cisza z serwera to nie potwierdzenie aktualności."""
    wynik = sprawdz(_wlaczone(), "0.14.0", zrodlo=_Zrodlo(None, "serwer nie odpowiada"))

    assert wynik.stan is Stan.NIEZNANY
    assert wynik.stan is not Stan.AKTUALNA
    assert "nie odpowiada" in wynik.powod, "stan NIEZNANY musi nieść POWÓD"


def test_nieporownywalna_wersja_dostawcy_daje_NIEZNANY() -> None:
    """Zgadywanie porządku wersji byłoby gorsze niż przyznanie się do niewiedzy."""
    wynik = sprawdz(_wlaczone(), "0.14.0", zrodlo=_Zrodlo(Wydanie("nightly-2026-08-28", "")))

    assert wynik.stan is Stan.NIEZNANY
    assert "nie umiem porównać" in wynik.powod


def test_wylaczony_mechanizm_NIE_pyta_serwera() -> None:
    """Wyłączone znaczy „nie wychodzimy w sieć", a nie „wychodzimy i milczymy"."""
    zrodlo = _Zrodlo(Wydanie("v9.9.9", ""))

    wynik = sprawdz(_config(), "0.14.0", zrodlo=zrodlo)

    assert wynik.stan is Stan.WYLACZONE
    assert zrodlo.pytano == 0, "wyłączony mechanizm odpytał serwer wydań"


@pytest.mark.parametrize(
    ("tekst", "oczekiwane"),
    [
        ("0.14.0", (0, 14, 0)),
        ("v0.14.0", (0, 14, 0)),
        ("  v1.2.3  ", (1, 2, 3)),
        ("1.0.0-rc1", None),
        ("nightly", None),
        ("1.2", None),
        ("", None),
    ],
)
def test_rozbieranie_wersji(tekst: str, oczekiwane: tuple[int, int, int] | None) -> None:
    """Wersja o nieznanym kształcie daje `None`, czyli stan NIEZNANY — nie domysł."""
    assert rozbierz_wersje(tekst) == oczekiwane


def test_porzadek_jest_LICZBOWY_a_nie_tekstowy() -> None:
    """Porównanie napisów uznałoby 0.9.0 za nowsze od 0.10.0 — klasyczna pułapka."""
    wynik = sprawdz(_wlaczone(), "0.9.0", zrodlo=_Zrodlo(Wydanie("v0.10.0", "")))

    assert wynik.stan is Stan.DOSTEPNA


# --------------------------------------------------------------------------------------
# Bramki konfiguracji
# --------------------------------------------------------------------------------------


def test_domyslnie_WYLACZONE() -> None:
    """Mechanizm ujawniający istnienie instalacji nie może włączyć się sam."""
    assert UpdateConfig().enabled is False


@pytest.mark.parametrize(
    ("ustawienia", "fragment"),
    [
        ({"enabled": True, "sources": ["api.github.com"]}, "update.repository"),
        ({"enabled": True, "repository": "a/b"}, "update.sources"),
    ],
)
def test_wlaczony_mechanizm_bez_kompletu_jest_ODRZUCANY(ustawienia: dict, fragment: str) -> None:
    """Atrapa wyglądająca na działającą jest gorsza niż odmowa startu."""
    with pytest.raises(ValueError, match=fragment):
        UpdateConfig(**ustawienia)


@pytest.mark.parametrize(
    "zly_host",
    ["https://api.github.com", "api.github.com/repos", "user@api.github.com", "host:443", " "],
)
def test_allowlista_przyjmuje_WYLACZNIE_nazwy_hostow(zly_host: str) -> None:
    """Ta sama kontrola co dla `security.egress.allowlist` — wpis to czysta nazwa hosta."""
    with pytest.raises(ValueError):
        UpdateConfig(enabled=True, repository="a/b", sources=[zly_host])


@pytest.mark.parametrize("zle_repo", ["husarz", "a/b/c", "https://github.com/a/b", "a/"])
def test_repozytorium_musi_miec_ksztalt_wlasciciel_nazwa(zle_repo: str) -> None:
    """Zły kształt dałby zapytanie pod przypadkowy adres — lepiej odmówić przy starcie."""
    with pytest.raises(ValueError, match="wlasciciel/nazwa"):
        UpdateConfig(enabled=True, repository=zle_repo, sources=["api.github.com"])


def test_profil_AIRGAP_odrzuca_wlaczone_aktualizacje(write_config) -> None:
    """Instalacja odcięta od sieci nie ma jak sprawdzić wersji.

    Mechanizm „włączony, ale niedziałający" byłby polem bez skutku — a operator miałby
    prawo sądzić, że instalacja sama się pilnuje.
    """
    katalog = write_config(
        {
            "models.yaml": _MODELE,
            "husarz.yaml": "profile: airgap\n",
            "security.yaml": "egress:\n  default_policy: deny\n",
            "update.yaml": "enabled: true\nrepository: a/b\nsources: [api.github.com]\n",
        }
    )

    with pytest.raises(ConfigError) as blad:
        load_config(katalog)

    assert "airgap nie dopuszcza update.enabled=true" in str(blad.value)


def test_profil_dev_DOPUSZCZA_wlaczone_aktualizacje(write_config) -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby mechanizm był odrzucany wszędzie."""
    katalog = write_config(
        {
            "models.yaml": _MODELE,
            "update.yaml": "enabled: true\nrepository: a/b\nsources: [api.github.com]\n",
        }
    )

    assert load_config(katalog).update.enabled is True


def test_dostarczona_konfiguracja_ma_aktualizacje_WYLACZONE(repo_config_dir) -> None:
    """Repozytorium nie może dostarczać instalacji, która sama dzwoni do domu."""
    assert load_config(repo_config_dir).update.enabled is False
