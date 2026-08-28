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


@pytest.mark.parametrize("profil", ["dev", "prod", "airgap"])
def test_engine_none_jest_odrzucany_w_KAZDYM_profilu(write_config, profil: str) -> None:
    """Silniejsze niż bramka profilowa — i dlatego zastąpiło ją jako droga główna.

    Bazowa linia odrzucała `engine: none` wyłącznie w prod/airgap. W `dev` wartość
    przechodziła i NIC nie robiła: `build_tools` zawsze buduje executor Dockera, więc
    narzędzie i tak szło do kontenera. Operator miał prawo sądzić, że wyłączył izolację —
    nie wyłączył, ale też się o tym nie dowiedział.

    Wyłączenia izolacji świadomie NIE dodajemy: byłoby poszerzeniem powierzchni ataku.
    Usuwamy więc wartość, która je obiecuje.
    """
    katalog = _katalog(
        write_config, profil, "egress:\n  default_policy: deny\nsandbox:\n  engine: none\n"
    )

    with pytest.raises(ConfigError) as exc:
        load_config(katalog)

    assert "NIE MA drogi wykonania narzędzia poza kontenerem" in str(exc.value)


# --------------- deklarowany silnik musi odpowiadać temu, co naprawdę robi kontener


def test_gvisor_bez_runtime_class_jest_ODRZUCANY() -> None:
    """Fałszywe zapewnienie o SILE izolacji — najgroźniejszy z rozjazdów w tej parze.

    O gVisorze decyduje wyłącznie `runtime_class` (trafia do `docker run --runtime`).
    `engine` nie steruje niczym, a jest POKAZYWANY operatorowi: w linii startowej CLI
    i w `GET /api/config/summary`. Konfiguracja `engine: docker+gvisor` z pustym
    `runtime_class` dawała więc zwykły runc, a operator czytał „docker+gvisor".
    """
    from husarz.config.schema import SandboxConfig

    with pytest.raises(ValueError, match="wymaga `runtime_class`"):
        SandboxConfig(engine="docker+gvisor", runtime_class=None)

    # Nośność: poprawna para MUSI przechodzić, inaczej walidator blokuje dostarczoną
    # konfigurację repo (która używa właśnie gVisora z `runsc`).
    assert SandboxConfig(engine="docker+gvisor", runtime_class="runsc").runtime_class == "runsc"


def test_docker_z_runtime_class_tez_jest_ODRZUCANY() -> None:
    """Rozjazd w drugą stronę: kontener użyłby runtime'u, o którym nazwa silnika milczy."""
    from husarz.config.schema import SandboxConfig

    with pytest.raises(ValueError, match="engine='docker'"):
        SandboxConfig(engine="docker", runtime_class="runsc")

    assert SandboxConfig(engine="docker").runtime_class is None


def test_domyslny_silnik_ZGADZA_sie_z_domyslnym_runtime() -> None:
    """Regresja: walidator zgodności odrzucił WŁASNĄ wartość domyślną.

    Domyślnie było `engine: docker+gvisor` przy `runtime_class: None`, czyli deklaracja
    niespójna z zachowaniem (zwykły runc) od samego początku. Wyszło dopiero wtedy, gdy
    kontrola zgodności powstała — i to jest najuczciwszy możliwy sygnał.
    """
    from husarz.config.schema import SandboxConfig

    domyslny = SandboxConfig()  # nie może rzucić

    assert domyslny.engine.value == "docker"
    assert domyslny.runtime_class is None


def test_dostarczona_konfiguracja_deklaruje_gvisora_I_go_ustawia(
    repo_config_dir,
) -> None:  # noqa: ANN001
    """Konfiguracja repo ma mówić prawdę o sile izolacji, którą reklamuje."""
    sandbox = load_config(repo_config_dir).security.sandbox

    assert sandbox.engine.value == "docker+gvisor"
    assert sandbox.runtime_class == "runsc", "deklaracja gVisora bez runtime'u byłaby pozorem"
