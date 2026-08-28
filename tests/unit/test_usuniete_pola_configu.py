"""Pola usunięte ze schematu — czytelna odmowa zamiast generycznego błędu.

`weights_path` żyło w `ModelSpec` i **nic go nie czytało**: jedyne wystąpienie w całym
repozytorium było w definicji pola. Wyglądało przy tym, jakby wskazywało silnikowi lokalne
wagi (nazwa, typ `Path`, komentarz „ścieżka do lokalnych wag"), więc operator mógł je ustawić
i uwierzyć, że coś z tego wynika. Martwe pole udające działające jest gorsze niż jego brak.

Usunięcie samo w sobie dałoby przy starcie „extra fields not permitted" — komunikat prawdziwy,
ale niczego nietłumaczący. Stąd osobny walidator z wyjaśnieniem.
"""

from __future__ import annotations

import pytest

from husarz.config.schema import ModelSpec

pytestmark = pytest.mark.unit


def test_weights_path_daje_KOMUNIKAT_a_nie_generyczny_blad() -> None:
    """Operator ma się dowiedzieć, CO się stało i dlaczego pole zniknęło."""
    with pytest.raises(ValueError) as exc:
        ModelSpec(backend="ollama", model="x", weights_path="./models/x")

    tresc = str(exc.value)
    assert "weights_path" in tresc
    assert "USUNIĘTE" in tresc
    assert "nie było przez nic czytane" in tresc
    # Bez tego asercje wyżej przechodziłyby także dla generycznego błędu Pydantica,
    # gdyby ten przypadkiem cytował nazwę pola.
    assert "extra_forbidden" not in tresc


def test_poprawna_specyfikacja_bez_tego_pola_przechodzi() -> None:
    """Nośność: walidator nie może odrzucać wszystkiego."""
    spec = ModelSpec(backend="ollama", model="x", endpoint="http://localhost:11434/v1")

    assert spec.model == "x"
    assert not hasattr(spec, "weights_path"), "pole miało zniknąć, nie zostać ukryte"


def test_dostarczona_konfiguracja_nie_uzywa_usunietego_pola(
    repo_config_dir,
) -> None:  # noqa: ANN001
    """Regresja: usuwając pole ze schematu, ZOSTAWIŁEM je w trzech miejscach configu repo.

    Wyłapał to dopiero `husarz validate` — grep po `src/` go nie widział, bo szukałem
    w kodzie, a nie w konfiguracji. Ten test pilnuje, żeby nie wróciło.
    """
    from husarz.config import load_config

    load_config(repo_config_dir)  # rzuciłoby, gdyby pole gdzieś zostało
    tresc = (repo_config_dir / "models.yaml").read_text(encoding="utf-8")
    assert "weights_path" not in tresc


# ------------------------------- ustawienia, które NIE ROBIĄ nic, są odrzucane


def test_niezaimplementowana_strategia_routingu_jest_ODRZUCANA() -> None:
    """`routing.strategy: cost` nie robiło NIC — router nie czyta tego pola ani razu.

    Operator ustawiał politykę doboru modelu po koszcie i dostawał po cichu zachowanie
    `tags`. Ta sama klasa co `weights_path`, tylko gorsza: nazwa obiecuje POLITYKĘ, a nie
    ścieżkę. Dokumentacja mówiła o tym uczciwie („placeholdery na kolejne etapy") — ale
    dokumentacja to najsłabsza z możliwych kontroli, bo nie czyta jej ten, kto edytuje YAML.
    """
    from husarz.config.schema import RoutingConfig

    for niedziałająca in ["cost", "latency"]:
        with pytest.raises(ValueError) as exc:
            RoutingConfig(strategy=niedziałająca)
        tresc = str(exc.value)
        assert "NIE jest jeszcze zaimplementowane" in tresc, niedziałająca
        assert "tags" in tresc, "komunikat musi podać wartość, która DZIAŁA"


def test_dzialajaca_strategia_przechodzi() -> None:
    """Nośność: walidator nie może odrzucać wszystkiego."""
    from husarz.config.schema import RoutingConfig, RoutingStrategy

    assert RoutingConfig(strategy="tags").strategy is RoutingStrategy.TAGS
    assert RoutingConfig().strategy is RoutingStrategy.TAGS


def test_kazda_wartosc_enuma_ma_ROZSTRZYGNIETY_status() -> None:
    """Dopisanie wartości do enuma MUSI wymusić decyzję: implementuję czy odrzucam.

    Bez tego nowa strategia weszłaby do schematu jako kolejny cichy placeholder — czyli
    wróciłaby dokładnie ta wada, którą ten walidator zamyka.
    """
    from husarz.config.schema import (
        _ZAIMPLEMENTOWANE_STRATEGIE,
        RoutingConfig,
        RoutingStrategy,
    )

    assert _ZAIMPLEMENTOWANE_STRATEGIE, "lista zaimplementowanych nie może być pusta"
    for wartosc in RoutingStrategy:
        if wartosc in _ZAIMPLEMENTOWANE_STRATEGIE:
            RoutingConfig(strategy=wartosc)  # musi przejść
        else:
            with pytest.raises(ValueError):
                RoutingConfig(strategy=wartosc)


def test_router_faktycznie_NIE_czyta_pola_strategy() -> None:
    """Kontrola ŹRÓDŁA potwierdzająca przesłankę całej tej odmowy.

    Gdyby ktoś kiedyś wpiął `strategy` w `selection.py`, ten test zaczerwieni się i zmusi
    do przemyślenia walidatora — zamiast zostawić odmowę, która stała się nieprawdziwa.
    Świadomie słabszy niż test skutku: sprawdza brak odwołania, nie brak zachowania.
    """
    from pathlib import Path

    zrodlo = Path("src/husarz/router/selection.py").read_text(encoding="utf-8")

    assert ".strategy" not in zrodlo, (
        "selection.py zaczął czytać `routing.strategy` — zweryfikuj walidator "
        "`_tylko_zaimplementowane_strategie`, bo jego uzasadnienie mogło przestać być prawdziwe"
    )


# ------------------- pola, które KŁAMAŁY o bezpieczeństwie (przeszukanie systematyczne)


def test_requires_sandbox_odrzucone_z_powodem_o_GRANICY_izolacji() -> None:
    """Najgorsze z siedemnastu: pole stawiało twierdzenie NIEPRAWDZIWE.

    `config/tools/web.yaml` i `file_edit.yaml` deklarowały `requires_sandbox: true`, a oba
    narzędzia działają W PROCESIE Husarza — sprawdzone: żaden z tych modułów nie woła
    executora ani razu. Operator czytający plik miał pełne prawo sądzić, że ruch wychodzący
    idzie z odizolowanego kontenera. Nie idzie.
    """
    from husarz.config.schema import ToolConfig

    with pytest.raises(ValueError) as exc:
        ToolConfig(name="x", kind="web", requires_sandbox=True)

    tresc = str(exc.value)
    assert "requires_sandbox" in tresc
    assert "W PROCESIE Husarza" in tresc, "komunikat musi prostować, gdzie te narzędzia biegną"
    assert "shell" in tresc and "run_tests" in tresc, "musi podać, co NAPRAWDĘ jest izolowane"


@pytest.mark.parametrize("pole", ["workspace_only", "path_allowlist"])
def test_martwe_pola_sandboxa_sa_odrzucane(pole: str) -> None:
    """Obiecywały konfigurowalność ograniczeń plikowych, której nie było.

    Kontener dostaje DOKŁADNIE jeden montaż (pilnuje tego osobny test skutku), a narzędzia
    plikowe przechodzą przez konfinację workspace. Nie było czego wyłączać ani poszerzać.
    """
    from husarz.config.schema import SandboxConfig

    with pytest.raises(ValueError) as exc:
        SandboxConfig(**{pole: True if pole == "workspace_only" else ["/etc"]})

    assert pole in str(exc.value)
    assert "USUNIĘTE" in str(exc.value)


def test_limit_kosztu_ktorego_nikt_nie_egzekwuje_jest_odrzucany() -> None:
    """Pomyłka szczególnie prawdopodobna: OBA sąsiednie limity w tym bloku DZIAŁAJĄ.

    Operator ustawiający trzy limity obok siebie miał wszelkie podstawy sądzić, że wszystkie
    obowiązują. `max_cost_per_task` nie obowiązywał — nic go nie czytało.
    """
    from husarz.config.schema import CostControls

    # Wartość DODATNIA jest tu istotna: `gt=0` odrzuciłoby zero i test przeszedłby także
    # bez nowego walidatora (pułapka nr 1 z CLAUDE.md — mutacja trafiająca nie tam, gdzie
    # się wydaje).
    with pytest.raises(ValueError) as exc:
        CostControls(max_cost_per_task=0.01)

    tresc = str(exc.value)
    assert "NIE JEST egzekwowane" in tresc
    assert "max_tokens_per_request" in tresc, "musi wskazać limity, które DZIAŁAJĄ"

    # Nośność: sąsiednie limity muszą nadal przechodzić.
    assert CostControls(max_tokens_per_request=100).max_tokens_per_request == 100


def test_wlaczenie_mtls_jest_odrzucane_bo_kanal_bylby_JAWNY() -> None:
    """To nie jest zwykłe „pole nic nie robi" — to fałszywe poczucie szyfrowania.

    Konfiguracja z `mtls.enabled: true` startowała, a API nasłuchiwało po zwykłym HTTP.
    Token Bearer szedł jawnym tekstem, podczas gdy plik konfiguracji mówił „mTLS włączony".
    """
    from husarz.config.schema import MtlsConfig

    with pytest.raises(ValueError) as exc:
        MtlsConfig(enabled=True)

    tresc = str(exc.value)
    assert "NIE JEST zaimplementowany" in tresc
    assert "reverse-proxy" in tresc, "komunikat musi podać wyjście, nie tylko odmowę"

    assert MtlsConfig().enabled is False  # nośność: wyłączony przechodzi


def test_wlaczenie_oidc_jest_odrzucane() -> None:
    """Ta sama klasa: konfiguracja twierdziła, że tożsamość weryfikuje dostawca OIDC."""
    from husarz.config.schema import AuthConfig

    with pytest.raises(ValueError) as exc:
        AuthConfig(oidc_enabled=True)

    assert "NIE JEST zaimplementowany" in str(exc.value)
    assert "api_token_ref" in str(exc.value)

    assert AuthConfig().oidc_enabled is False


def test_dostarczona_konfiguracja_nie_uzywa_zadnego_z_usunietych_pol(
    repo_config_dir,
) -> None:  # noqa: ANN001
    """Regresja: przy `weights_path` zostawiłem pole w trzech miejscach configu repo."""
    from husarz.config import load_config

    load_config(repo_config_dir)  # rzuciłoby, gdyby cokolwiek zostało
    for pole in ["requires_sandbox", "workspace_only", "path_allowlist", "max_cost_per_task"]:
        trafienia = [
            p
            for p in repo_config_dir.rglob("*.yaml")
            # Sidecary AppleDouble (`._*.yaml`) pasują do globa, a są binarne — bez tego
            # filtra test wywraca się na `UnicodeDecodeError` zamiast cokolwiek sprawdzić.
            # Ta sama poprawka co w `test_import_layering.py`.
            if not p.name.startswith("._") and pole in p.read_text(encoding="utf-8")
        ]
        assert not trafienia, f"{pole} nadal w: {[str(p.name) for p in trafienia]}"
