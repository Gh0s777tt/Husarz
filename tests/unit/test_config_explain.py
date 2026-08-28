"""`husarz config explain` — z której warstwy pochodzi wartość.

**Skąd to polecenie.** Hierarchia nadpisań to `defaults (kod) → config/*.yaml → ENV
(HUSARZ_*) → sekrety → runtime (panel)`. Na stanowisku deweloperskim odpowiedź „dlaczego ta
wartość jest taka" jest oczywista, bo warstwa jest jedna. We wdrożeniu kontenerowym już nie:
`deploy/k8s/configmap.yaml` nadpisuje konfigurację zmiennymi środowiskowymi, więc plik
w repozytorium mówi jedno, a działająca instancja robi drugie — a operator patrzy na plik.

Najważniejszy test to `test_ENV_nadpisujace_plik_jest_WIDOCZNE`: pokazanie samej wartości
obowiązującej nie rozwiązywałoby problemu, dla którego polecenie powstało. Operator musi
zobaczyć, że plik mówi co innego, i KTÓRA warstwa go przebiła.

Drugi co do wagi to `test_referencja_do_sekretu_NIE_jest_rozwiazywana`: narzędzie
diagnostyczne rozwijające referencje byłoby wygodnym sposobem odczytania sekretu przez kogoś
z dostępem do powłoki, ale nie do magazynu.
"""

from __future__ import annotations

import pytest

from husarz.config.errors import ConfigError
from husarz.config.wyjasnienie import BRAK, wyjasnij
from husarz.launcher.cli import main

_MODELE = "default: a\nregistry:\n  a:\n    backend: mock\n    model: a\n"


@pytest.fixture
def katalog(write_config):
    """Konfiguracja z jawnie ustawioną wartością w pliku."""
    return write_config(
        {
            "models.yaml": _MODELE,
            "security.yaml": "audit:\n  integrity: blocking\nroe:\n  key_ref: env:KLUCZ_ROE\n",
        }
    )


def test_wartosc_z_PLIKU_jest_nazwana(katalog) -> None:
    """Ścieżka pogodna: plik ustawia, nikt nie nadpisuje."""
    wynik = wyjasnij("security.audit.integrity", config_dir=katalog, env={})

    assert wynik.wartosc == "blocking"
    assert wynik.obowiazujaca == "config/*.yaml"
    assert [w.nazwa for w in wynik.warstwy if w.ustawia] == ["config/*.yaml"]


def test_ENV_nadpisujace_plik_jest_WIDOCZNE(katalog) -> None:
    """Sedno: obie wartości widać naraz, więc rozjazd przestaje być niewidzialny."""
    wynik = wyjasnij(
        "security.audit.integrity",
        config_dir=katalog,
        env={"HUSARZ_SECURITY__AUDIT__INTEGRITY": "warn"},
    )

    assert wynik.wartosc == "warn"
    assert wynik.obowiazujaca == "ENV (HUSARZ_*)"
    # Warstwa niższa NADAL jest pokazana ze swoją wartością — bez tego operator nie
    # wiedziałby, że plik mówi co innego, czyli nie dowiedziałby się tego, po co pytał.
    z_pliku = next(w for w in wynik.warstwy if w.nazwa == "config/*.yaml")
    assert z_pliku.wartosc == "blocking"


def test_runtime_bije_ENV(katalog) -> None:
    """Najwyższa warstwa wygrywa — bez tego testu kolejność byłaby tylko deklaracją."""
    wynik = wyjasnij(
        "security.audit.integrity",
        config_dir=katalog,
        env={"HUSARZ_SECURITY__AUDIT__INTEGRITY": "warn"},
        runtime_overrides={"security": {"audit": {"integrity": "blocking"}}},
    )

    assert wynik.obowiazujaca == "runtime (panel)"
    assert wynik.wartosc == "blocking"


def test_sciezka_nieustawiona_nigdzie_wskazuje_DOMYSLNA(katalog) -> None:
    """„Nikt tego nie ustawia" to inna odpowiedź niż „tego nie ma"."""
    wynik = wyjasnij("security.audit.hmac_key_id", config_dir=katalog, env={})

    assert wynik.wartosc is BRAK
    assert wynik.obowiazujaca == "defaults (kod)"


def test_wartosc_null_to_NIE_to_samo_co_brak(write_config) -> None:
    """`null` w YAML-u jest wartością znaczącą (np. wyłączeniem limitu), nie brakiem wpisu.

    Bez wartownika `BRAK` obie sytuacje byłyby nierozróżnialne, a operator dostałby
    „nikt tego nie ustawia" na polu, które ustawił świadomie na `null`.
    """
    katalog = write_config(
        {"models.yaml": _MODELE, "security.yaml": "diagnostics:\n  max_requests_per_minute: null\n"}
    )

    wynik = wyjasnij("security.diagnostics.max_requests_per_minute", config_dir=katalog, env={})

    assert wynik.wartosc is None
    assert wynik.wartosc is not BRAK
    assert wynik.obowiazujaca == "config/*.yaml"


def test_referencja_do_sekretu_NIE_jest_rozwiazywana(katalog, monkeypatch) -> None:
    """Polecenie diagnostyczne nie może stać się czytnikiem sekretów."""
    monkeypatch.setenv("KLUCZ_ROE", "TAJNY-MATERIAL-KLUCZA")

    wynik = wyjasnij("security.roe.key_ref", config_dir=katalog, env={})

    assert wynik.wartosc == "env:KLUCZ_ROE"
    assert wynik.jest_referencja is True
    assert "TAJNY-MATERIAL-KLUCZA" not in repr(wynik)


def test_pusta_sciezka_jest_bledem(katalog) -> None:
    """Milcząca odpowiedź na puste pytanie byłaby gorsza od błędu."""
    with pytest.raises(ConfigError, match="ścieżkę kropkową"):
        wyjasnij("   ", config_dir=katalog, env={})


# --------------------------------------------------------------------------------------
# Wiersz poleceń
# --------------------------------------------------------------------------------------


def test_cli_pokazuje_wszystkie_warstwy(katalog, capsys, monkeypatch) -> None:
    """Raport ma wymienić WSZYSTKIE warstwy, także te, które nic nie wnoszą.

    Warstwa pominięta w wydruku byłaby dla operatora warstwą nieistniejącą — a to ona bywa
    miejscem, w którym trzeba coś ustawić.
    """
    monkeypatch.setenv("HUSARZ_SECURITY__AUDIT__INTEGRITY", "warn")

    kod = main(["config", "explain", "security.audit.integrity", "--config", str(katalog)])

    wyjscie = capsys.readouterr().out
    assert kod == 0
    for warstwa in ("defaults (kod)", "config/*.yaml", "ENV (HUSARZ_*)", "runtime (panel)"):
        assert warstwa in wyjscie, warstwa
    assert "blocking" in wyjscie and "warn" in wyjscie
    assert "obowiązuje" in wyjscie


def test_cli_podpowiada_nazwe_zmiennej_srodowiskowej(katalog, capsys) -> None:
    """Podpowiedź działa także wtedy, gdy warstwa ENV nic dziś nie wnosi."""
    main(["config", "explain", "security.audit.integrity", "--config", str(katalog)])

    assert "HUSARZ_SECURITY__AUDIT__INTEGRITY" in capsys.readouterr().out


def test_cli_ostrzega_przy_referencji_do_sekretu(katalog, capsys) -> None:
    """Operator ma wiedzieć, że widzi wskazanie, a nie materiał."""
    main(["config", "explain", "security.roe.key_ref", "--config", str(katalog)])

    wyjscie = capsys.readouterr().out
    assert "REFERENCJA do sekretu" in wyjscie
    assert "env:KLUCZ_ROE" in wyjscie


def test_cli_zla_sciezka_daje_kod_2(katalog, capsys) -> None:
    """Kod wyjścia odróżnia „nie umiem odpowiedzieć" od „odpowiedź brzmi: domyślna"."""
    assert main(["config", "explain", "", "--config", str(katalog)]) == 2
