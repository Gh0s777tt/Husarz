"""Dostarczona konfiguracja musi działać we WSZYSTKICH profilach — i po zmianach w schemacie.

**Skąd ten plik.** W jednej sesji usunięto ze schematu cztery pola (`weights_path`,
`requires_sandbox`, `workspace_only`, `path_allowlist`) i dołożono sześć odmów (`strategy`,
`max_cost_per_task`, mTLS, OIDC, `engine: none`, `engine` niezgodny z `runtime_class`).
Każda taka zmiana może po cichu unieruchomić dostarczoną konfigurację albo warstwę ENV —
a wtedy nowy operator dostaje projekt, który nie startuje.

Sprawdzałem to ręcznie po każdej zmianie. Ten plik zamienia rytuał w kontrolę: jeśli
kolejna zmiana schematu zepsuje `config/`, zestaw zaczerwieni się od razu, a nie po
pierwszym uruchomieniu u kogoś.

Kontrole są SKUTKOWE — czy konfiguracja daje się wczytać i czy wartości zgadzają się z tym,
co system naprawdę robi. Nie sprawdzamy tu, „co stoi w pliku".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.errors import ConfigError

pytestmark = pytest.mark.security


@pytest.mark.parametrize("profil", ["dev", "prod", "airgap"])
def test_konfiguracja_repo_wczytuje_sie_w_KAZDYM_profilu(
    repo_config_dir: Path, profil: str
) -> None:
    """Wszystkie trzy profile, nie tylko domyślny.

    `prod` i `airgap` mają dodatkowe wymagania (bazowa linia bezpieczeństwa, zakaz egressu),
    więc dostarczona konfiguracja mogła przestać się w nich mieścić po dowolnej zmianie
    walidacji — bez żadnego sygnału w profilu `dev`, w którym pracujemy na co dzień.
    """
    config = load_config(repo_config_dir, runtime_overrides={"platform": {"profile": profil}})

    assert config.platform.profile.value == profil
    assert config.models.registry, "rejestr modeli nie może być pusty"
    assert config.agents, "Chorągiew nie może być pusta"


@pytest.mark.parametrize(
    ("zmienna", "wartosc"),
    [
        ("HUSARZ_SECURITY__SANDBOX__ENGINE", "none"),
        ("HUSARZ_ROUTING__STRATEGY", "cost"),
        ("HUSARZ_SECURITY__MTLS__ENABLED", "true"),
        ("HUSARZ_SECURITY__AUTH__OIDC_ENABLED", "true"),
    ],
)
def test_odmowy_obowiazuja_takze_w_warstwie_ENV(
    repo_config_dir: Path, monkeypatch, zmienna: str, wartosc: str
) -> None:
    """Walidacja nie może pilnować wyłącznie plików YAML.

    ENV jest udokumentowaną warstwą hierarchii konfiguracji (`defaults -> yaml -> ENV ->
    sekrety -> runtime`), więc ustawienie zmiennej to ta sama droga co edycja pliku.
    Odmowa działająca tylko dla YAML-a zostawiałaby operatorowi obejście — i to takie,
    które w kontenerze jest DROGĄ DOMYŚLNĄ, bo tam konfigurację nadpisuje się właśnie ENV-em
    (patrz `deploy/k8s/configmap.yaml`).
    """
    monkeypatch.setenv(zmienna, wartosc)

    with pytest.raises(ConfigError):
        load_config(repo_config_dir)


def test_deklarowany_silnik_zgadza_sie_z_tym_co_widzi_operator(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """`GET /api/config/summary` i linia startowa pokazują `engine` — musi być prawdziwy.

    Sprawdzamy SKUTEK: to, co trafi do `docker run`, ma odpowiadać nazwie silnika, którą
    system o sobie mówi.
    """
    from husarz.tools.sandbox import build_docker_argv, spec_from_config

    sandbox = load_config(repo_config_dir).security.sandbox
    argv = build_docker_argv(spec_from_config(["ls"], sandbox, workspace_host_path=str(tmp_path)))

    deklaruje_gvisora = sandbox.engine.value == "docker+gvisor"
    uzywa_runtime = "--runtime" in argv
    assert deklaruje_gvisora == uzywa_runtime, (
        f"rozjazd: engine={sandbox.engine.value}, a argv {'ma' if uzywa_runtime else 'NIE MA'} "
        f"--runtime"
    )


def test_pliki_wdrozeniowe_nie_uzywaja_usunietych_pol() -> None:
    """Regresja: przy `weights_path` zostawiłem pole w trzech miejscach `config/`.

    Ten test patrzy szerzej — na `deploy/` i `docker/`, czyli tam, gdzie konfiguracja jest
    powielana dla kontenerów i klastra. Rozjazd tam jest niewidoczny lokalnie i ujawnia się
    dopiero na wdrożeniu.
    """
    korzen = Path(__file__).resolve().parents[2]
    usuniete = [
        "weights_path",
        "requires_sandbox",
        "workspace_only",
        "path_allowlist",
        "max_cost_per_task",
    ]
    trafienia: list[str] = []
    for katalog in ("deploy", "docker", "config"):
        for plik in (korzen / katalog).rglob("*"):
            if not plik.is_file() or plik.name.startswith("._"):
                continue
            try:
                tresc = plik.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            trafienia += [f"{plik.relative_to(korzen)}: {p}" for p in usuniete if p in tresc]

    assert not trafienia, f"usunięte pola wróciły do plików wdrożeniowych: {trafienia}"


def test_zaden_plik_konfiguracji_nie_jest_OSIEROCONY(repo_config_dir: Path) -> None:
    """Plik w `config/`, którego loader nie mapuje, jest cichym no-opem.

    Wykryte kontrolą nośności: usunięcie jednego wpisu z `_SINGLE_FILES` NIE psuło niczego
    widocznego — konfiguracja ładowała się dalej, tyle że z wartościami domyślnymi, a plik
    stawał się dekoracją. Operator mógłby ustawić w nim `enabled: true` i czekać na skutek,
    którego nie ma.

    To ta sama klasa co pola schematu bez czytelnika (Etap 17m), tylko o piętro wyżej:
    tam martwe było POLE, tu martwy bywa CAŁY PLIK. Kontrola jest strukturalna i świadomie
    słabsza od testu skutku — pilnuje istnienia mapowania, nie tego, że sekcja coś zmienia.
    """
    from husarz.config.loader import _MULTI_DIRS, _SINGLE_FILES

    mapowane = set(_SINGLE_FILES.values())
    obecne = {p.name for p in repo_config_dir.glob("*.yaml") if not p.name.startswith("._")}

    osierocone = obecne - mapowane
    assert not osierocone, (
        f"pliki w config/, których loader NIE czyta: {sorted(osierocone)}. "
        f"Albo dopisz je do `_SINGLE_FILES`, albo usuń — plik, który niczego nie zmienia, "
        f"wprowadza w błąd."
    )

    # Druga strona: mapowanie na plik, którego nie ma, jest dopuszczalne (sekcje opcjonalne),
    # ale katalogi wielo-plikowe muszą istnieć, bo bez nich znika CAŁA sekcja.
    for sekcja, (podkatalog, _) in _MULTI_DIRS.items():
        assert (
            repo_config_dir / podkatalog
        ).is_dir(), f"sekcja '{sekcja}' mapuje na katalog '{podkatalog}', którego nie ma"
