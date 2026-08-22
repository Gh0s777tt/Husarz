"""Konfiguracja magazynu sekretów i jej sklejenie w launcherze.

**Skąd ten plik.** Adwersaryjny przegląd wykazał, że `SecretStoreConfig` oraz ścieżka
konfiguracja → magazyn → referencja `husarz:` w launcherze miały **zerowe pokrycie**: cały
walidator dało się usunąć, a zestaw testów zostawał zielony. Był to jedyny kod czyniący
kreator użytecznym i jednocześnie jedyny bez ani jednej asercji.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from husarz.config import load_config
from husarz.config.errors import ConfigError
from husarz.config.schema import SecretStoreConfig

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------- schemat


def test_domyslnie_wylaczony() -> None:
    """Deny-by-default: instalacja, która nie potrzebuje zapisu, nie ma jego powierzchni."""
    ustawienia = SecretStoreConfig()

    assert ustawienia.enabled is False
    assert ustawienia.key_ref is None
    assert ustawienia.path is None


def test_wlaczenie_bez_klucza_jest_odrzucane() -> None:
    """Nie ma trybu „zapisz jawnie" — magazyn bez klucza po prostu nie powstaje."""
    with pytest.raises(ValidationError) as exc:
        SecretStoreConfig(enabled=True)

    assert "key_ref" in str(exc.value)


def test_klucz_ze_schematu_husarz_jest_zabroniony() -> None:
    """Zamknięty krąg: magazyn nie może odblokowywać się własnym sekretem."""
    with pytest.raises(ValidationError) as exc:
        SecretStoreConfig(enabled=True, key_ref="husarz:klucz-glowny")

    assert "husarz:" in str(exc.value)


def test_surowy_material_klucza_jest_odrzucany() -> None:
    """Klucz w konfiguracji to referencja, nigdy materiał — jak wszędzie w projekcie."""
    with pytest.raises(ValidationError):
        SecretStoreConfig(enabled=True, key_ref="to-jest-golym-tekstem-klucz")


@pytest.mark.parametrize(
    "ref", ["env:HUSARZ_KEY", "file:klucz", "vault:secret/husarz#key", "sops:plik.yaml#klucz"]
)
def test_schematy_zewnetrzne_sa_dozwolone(ref: str) -> None:
    """Wszystkie cztery źródła zewnętrzne muszą działać jako klucz główny."""
    assert SecretStoreConfig(enabled=True, key_ref=ref).key_ref == ref


def test_klucz_bez_wlaczenia_jest_dozwolony() -> None:
    """Operator może przygotować konfigurację i włączyć ją później — to nie sprzeczność."""
    ustawienia = SecretStoreConfig(enabled=False, key_ref="env:K")

    assert ustawienia.enabled is False
    assert ustawienia.key_ref == "env:K"


def test_klucz_husarz_odrzucany_takze_przy_wylaczonym() -> None:
    """Walidacja schematu klucza nie może zależeć od tego, czy magazyn jest włączony."""
    with pytest.raises(ValidationError):
        SecretStoreConfig(enabled=False, key_ref="husarz:cos")


def test_wpiety_w_konfiguracje_globalna(repo_config_dir: Path) -> None:
    """Sekcja jest osiągalna z pełnej konfiguracji i domyślnie wyłączona."""
    config = load_config(repo_config_dir)

    assert config.security.secret_store.enabled is False


def test_wlaczenie_przez_nadpisanie_runtime(repo_config_dir: Path) -> None:
    """Ścieżka, którą realnie posłuży się operator i panel."""
    config = load_config(
        repo_config_dir,
        runtime_overrides={"security": {"secret_store": {"enabled": True, "key_ref": "env:K"}}},
    )

    assert config.security.secret_store.enabled is True
    assert config.security.secret_store.key_ref == "env:K"


def test_nadpisanie_bez_klucza_wywraca_wczytanie(repo_config_dir: Path) -> None:
    """Fail-closed także przez ENV/panel, nie tylko przy bezpośredniej konstrukcji."""
    with pytest.raises(ConfigError):
        load_config(
            repo_config_dir,
            runtime_overrides={"security": {"secret_store": {"enabled": True}}},
        )


# ------------------------------------------------------------------- launcher


def test_launcher_nie_buduje_magazynu_gdy_wylaczony(repo_config_dir: Path) -> None:
    """Wyłączony magazyn = brak instancji, a nie pusta instancja."""
    from husarz.launcher.cli import _zbuduj_magazyn_sekretow

    assert _zbuduj_magazyn_sekretow(load_config(repo_config_dir)) is None


def test_launcher_buduje_magazyn_i_uzywa_domyslnej_sciezki(
    repo_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``path: null`` → ``<data_dir>/secrets/store.json``; ścieżka nie jest zaszyta w kodzie."""
    from husarz.launcher.cli import _zbuduj_magazyn_sekretow

    monkeypatch.setenv("HUSARZ_TEST_KLUCZ", "materiał-klucza-głównego")
    config = load_config(
        repo_config_dir,
        runtime_overrides={
            "platform": {"data_dir": str(tmp_path / "dane")},
            "security": {"secret_store": {"enabled": True, "key_ref": "env:HUSARZ_TEST_KLUCZ"}},
        },
    )

    magazyn = _zbuduj_magazyn_sekretow(config)

    assert magazyn is not None
    magazyn.put("probny", "wartosc")
    assert (tmp_path / "dane" / "secrets" / "store.json").is_file()


def test_launcher_fail_closed_gdy_klucza_nie_da_sie_rozwiazac(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wolimy nie wystartować, niż wystartować z kreatorem, który odmówi przy pierwszym użyciu."""
    from husarz.launcher.cli import _zbuduj_magazyn_sekretow

    monkeypatch.delenv("HUSARZ_NIE_MA_TAKIEJ", raising=False)
    config = load_config(
        repo_config_dir,
        runtime_overrides={
            "security": {"secret_store": {"enabled": True, "key_ref": "env:HUSARZ_NIE_MA_TAKIEJ"}}
        },
    )

    with pytest.raises(ConfigError) as exc:
        _zbuduj_magazyn_sekretow(config)

    assert "magazyn" in str(exc.value).lower()


def test_scheme_secrets_rozwiazuje_referencje_husarz(
    repo_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sklejenie końcowe: `husarz:` w połączeniu Git rozwiązuje się przez magazyn launchera.

    To jedyna ścieżka, która czyni kreator UŻYTECZNYM — bez niej token byłby zapisany,
    ale serwis Gita nie potrafiłby go odczytać.
    """
    from husarz.launcher import cli

    monkeypatch.setenv("HUSARZ_TEST_KLUCZ", "materiał-klucza-głównego")
    config = load_config(
        repo_config_dir,
        runtime_overrides={
            "platform": {"data_dir": str(tmp_path / "dane")},
            "security": {"secret_store": {"enabled": True, "key_ref": "env:HUSARZ_TEST_KLUCZ"}},
        },
    )
    magazyn = cli._zbuduj_magazyn_sekretow(config)  # noqa: SLF001
    assert magazyn is not None
    magazyn.put("git/moj", "glpat-TOKEN-Z-KREATORA")
    monkeypatch.setattr(cli, "_SEKRETY", magazyn)

    assert (
        cli._SchemeSecrets().resolve("husarz:git/moj") == "glpat-TOKEN-Z-KREATORA"
    )  # noqa: SLF001


def test_scheme_secrets_bez_magazynu_zwraca_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wyłączony magazyn = referencja nierozwiązywalna, tak jak brak zmiennej środowiskowej."""
    from husarz.launcher import cli

    monkeypatch.setattr(cli, "_SEKRETY", None)

    assert cli._SchemeSecrets().resolve("husarz:git/moj") is None  # noqa: SLF001


def test_scheme_secrets_nadal_obsluguje_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nośność: dodanie schematu `husarz:` nie mogło zepsuć pozostałych."""
    from husarz.launcher import cli

    monkeypatch.setenv("HUSARZ_ZWYKLY", "wartosc-ze-srodowiska")

    assert (
        cli._SchemeSecrets().resolve("env:HUSARZ_ZWYKLY") == "wartosc-ze-srodowiska"
    )  # noqa: SLF001
