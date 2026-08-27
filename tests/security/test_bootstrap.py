"""`husarz bootstrap` — zgoda, rozmiar przed pobraniem i dwie osobne allowlisty.

**Dlaczego w `tests/security/`.** To pierwsza droga, którą Husarz z własnej inicjatywy sięga
do sieci po treść. Dotyka trzech niezmienników naraz: profilu (airgap zabrania ruchu),
polityki egress (dwie listy, nie jedna) i zgody operatora (gigabajty nie mogą polecieć
z rozpędu). Źródło jest wstrzykiwane, więc żaden test nie pobiera niczego.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from husarz.launcher.bootstrap import (
    OdmowaBootstrapu,
    RejestrISilnik,
    RozmiarModelu,
    sformatuj_plan,
    sprawdz_dopuszczalnosc,
    wykonaj,
    zbuduj_plan,
)
from husarz.launcher.doctor import BrakujacyModel

pytestmark = pytest.mark.security


def _brak(model_id: str = "m", nazwa: str = "model:7b") -> BrakujacyModel:
    return BrakujacyModel(
        model_id=model_id,
        nazwa=nazwa,
        endpoint="http://localhost:11434/v1",
        role=("tryb czatu",),
        dostepne=("co-innego",),
    )


class _Zrodlo:
    """Źródło testowe: sterowane rozmiary, zapisane żądania pobrania."""

    def __init__(self, rozmiary: dict[str, RozmiarModelu | None], blad: str = "") -> None:
        self._rozmiary = rozmiary
        self._blad = blad
        self.pobrane: list[str] = []

    def rozmiar(self, nazwa: str) -> tuple[RozmiarModelu | None, str]:
        """Zwraca ustawiony rozmiar albo powód jego braku."""
        r = self._rozmiary.get(nazwa)
        return (r, "") if r is not None else (None, "rejestr nie zna modelu")

    def pobierz(self, endpoint: str, nazwa: str, postep: Callable[[str], None]) -> str:
        """Zapisuje żądanie pobrania i zwraca ustawiony błąd."""
        self.pobrane.append(nazwa)
        return self._blad


# ------------------------------------------------------------ dopuszczalność


def test_profil_airgap_odmawia_nawet_przy_wlaczonym_bootstrapie(make_config) -> None:
    """Airgap znaczy brak ruchu wychodzącego — i żadna flaga tego nie zmienia.

    Kolejność kontroli jest częścią komunikatu: operator ma usłyszeć, że zabrania PROFIL,
    a nie że „wystarczy włączyć bootstrap". Ta druga odpowiedź sugerowałaby, że politykę
    da się obejść ustawieniem.
    """
    from husarz.config.schema import Profile

    bazowy = make_config(registry={"m": {"backend": "mock", "model": "x"}}, default="m")
    # `model_copy(update=...)` OMIJA walidację, więc podstawiamy WARTOŚĆ ENUMA, nie łańcuch:
    # z łańcuchem `profile` zostałby napisem, porównanie `is Profile.AIRGAP` byłoby fałszywe
    # i test przechodziłby z niewłaściwego powodu. Ta sama pułapka co przy `--probe-timeout`.
    config = bazowy.model_copy(
        update={
            "platform": bazowy.platform.model_copy(update={"profile": Profile.AIRGAP}),
            "bootstrap": _bootstrap_wlaczony(),
        }
    )
    assert config.platform.profile is Profile.AIRGAP, "podstawienie profilu nie zadziałało"

    with pytest.raises(OdmowaBootstrapu, match="airgap"):
        sprawdz_dopuszczalnosc(config)


def _bootstrap_wlaczony() -> Any:
    from husarz.config.schema import BootstrapConfig

    return BootstrapConfig(
        enabled=True, registry="https://rejestr.example", sources=["rejestr.example"]
    )


def test_domyslnie_wylaczone_znaczy_odmowa(make_config) -> None:
    """Stan domyślny platformy to „nic nie wychodzi"."""
    config = make_config(registry={"m": {"backend": "mock", "model": "x"}}, default="m")

    with pytest.raises(OdmowaBootstrapu, match="wyłączone"):
        sprawdz_dopuszczalnosc(config)


def test_wlaczony_w_profilu_dev_przechodzi(make_config) -> None:
    """Nośność obu odmów: przy poprawnej konfiguracji kontrola MUSI przepuścić."""
    config = make_config(registry={"m": {"backend": "mock", "model": "x"}}, default="m").model_copy(
        update={"bootstrap": _bootstrap_wlaczony()}
    )

    sprawdz_dopuszczalnosc(config)


def test_wlaczony_bootstrap_bez_rejestru_jest_bledem_konfiguracji() -> None:
    """Zgoda bez rozmiaru nie jest zgodą, więc atrapa ma paść przy starcie, nie przy użyciu."""
    from husarz.config.schema import BootstrapConfig

    with pytest.raises(ValueError, match="bootstrap.registry"):
        BootstrapConfig(enabled=True, sources=["x.example"])
    with pytest.raises(ValueError, match="bootstrap.sources"):
        BootstrapConfig(enabled=True, registry="https://x.example")


# --------------------------------------------------- rozmiar PRZED pobraniem


def test_pozycja_bez_rozmiaru_NIE_jest_pobierana() -> None:
    """„Ekran zgody podający liczbę GB" byłby fikcją, gdyby pozwalał zgodzić się w ciemno."""
    zrodlo = _Zrodlo({"znany:7b": RozmiarModelu(bajty=2_000_000_000, warstw=4)})
    plan = zbuduj_plan([_brak("a", "znany:7b"), _brak("b", "nieznany:7b")], zrodlo)

    assert [p.pobieralna for p in plan] == [True, False]

    bledy = wykonaj(plan, zrodlo, lambda s: None)

    assert zrodlo.pobrane == ["znany:7b"], "pobrano pozycję o nieznanym rozmiarze"
    assert bledy == []


def test_ekran_zgody_podaje_rozmiar_kazdej_pozycji_i_sume() -> None:
    """Operator ma zobaczyć liczbę PRZED decyzją, i to zarówno per model, jak i łącznie."""
    zrodlo = _Zrodlo(
        {
            "a:7b": RozmiarModelu(bajty=4_680_000_000, warstw=4),
            "b:1b": RozmiarModelu(bajty=986_000_000, warstw=4),
        }
    )
    plan = zbuduj_plan([_brak("a", "a:7b"), _brak("b", "b:1b")], zrodlo)

    tekst = "\n".join(sformatuj_plan(plan))

    assert "4.68 GB" in tekst
    assert "0.99 GB" in tekst
    assert "5.67 GB" in tekst, "brak sumy — operator nie wie, na ile miejsca się godzi"
    assert "SILNIK" in tekst, "musi być jasne, KTO pobiera"


def test_pozycja_niepobieralna_jest_POKAZANA_z_powodem() -> None:
    """Ciche pominięcie wyglądałoby jak „nie ma czego pobierać" — a jest co, tylko inaczej."""
    zrodlo = _Zrodlo({})
    plan = zbuduj_plan([_brak("a", "z-modelfile")], zrodlo)

    tekst = "\n".join(sformatuj_plan(plan))

    assert "z-modelfile" in tekst
    assert "NIE DO POBRANIA" in tekst
    assert "rejestr nie zna modelu" in tekst


def test_blad_pobierania_jest_raportowany_a_nie_polykany() -> None:
    """Nośność: `wykonaj` nie może zawsze zwracać pustej listy błędów."""
    zrodlo = _Zrodlo({"a:7b": RozmiarModelu(bajty=1, warstw=1)}, blad="brak miejsca na dysku")
    plan = zbuduj_plan([_brak("a", "a:7b")], zrodlo)

    bledy = wykonaj(plan, zrodlo, lambda s: None)

    assert bledy == ["a:7b: brak miejsca na dysku"]


# --------------------------------------------- dwie allowlisty, nie jedna


def _config_z_bootstrapem(make_config, *, sources: list[str], egress_allow: list[str]) -> Any:
    from husarz.config.schema import BootstrapConfig

    bazowy = make_config(registry={"m": {"backend": "mock", "model": "x"}}, default="m")
    return bazowy.model_copy(
        update={
            "bootstrap": BootstrapConfig(
                enabled=True, registry="https://rejestr.example", sources=sources
            ),
            "security": bazowy.security.model_copy(
                update={
                    "egress": bazowy.security.egress.model_copy(update={"allowlist": egress_allow})
                }
            ),
        }
    )


def test_host_spoza_bootstrap_sources_NIE_jest_odpytywany(make_config) -> None:
    """Sedno rozdzielenia list: obecność na `security.egress.allowlist` NIE WYSTARCZA.

    Gdyby wystarczała, każda domena otwarta dla narzędzia `web` stawałaby się źródłem,
    z którego Husarz gotów jest pobierać wagi — a to zupełnie inna decyzja operatora.
    """
    config = _config_z_bootstrapem(
        make_config, sources=["inny.example"], egress_allow=["rejestr.example"]
    )

    rozmiar, powod = RejestrISilnik(config).rozmiar("model:7b")

    assert rozmiar is None
    assert "bootstrap.sources" in powod


def test_bootstrap_sources_NIE_otwiera_domeny_narzedziu_web(make_config) -> None:
    """Odwrotny kierunek: zgoda na rejestr modeli nie może rozszczelniać deny-all.

    Kontrola strukturalna na poziomie polityki — `check_endpoint_allowed` (używane przez
    router, narzędzie `web` i wtyczki) czyta WYŁĄCZNIE `security.egress`, więc wpis
    w `bootstrap.sources` nie ma jak do niej dotrzeć.
    """
    from husarz.router.egress import EgressError, check_endpoint_allowed

    config = _config_z_bootstrapem(make_config, sources=["rejestr.example"], egress_allow=[])

    with pytest.raises(EgressError):
        check_endpoint_allowed("https://rejestr.example/v1", config.security.egress)


def test_modele_oficjalne_dostaja_przestrzen_library(make_config) -> None:
    """Rejestr Ollamy trzyma modele oficjalne pod `library/`; bez tego manifest daje 404."""
    config = _config_z_bootstrapem(make_config, sources=["rejestr.example"], egress_allow=[])
    zrodlo = RejestrISilnik(config)

    url, powod = zrodlo._url_manifestu("https://rejestr.example", "qwen2.5-coder:1.5b")

    assert powod == ""
    assert url == "https://rejestr.example/v2/library/qwen2.5-coder/manifests/1.5b"


def test_model_z_przestrzenia_uzytkownika_nie_dostaje_library(make_config) -> None:
    """Nośność powyższego: `SpeakLeash/bielik` ma już swoją przestrzeń."""
    config = _config_z_bootstrapem(make_config, sources=["rejestr.example"], egress_allow=[])

    url, _ = RejestrISilnik(config)._url_manifestu("https://rejestr.example", "SpeakLeash/bielik")

    assert url == "https://rejestr.example/v2/SpeakLeash/bielik/manifests/latest"


# ----------------------------------------------------- zgoda operatora (CLI)


def test_domyslna_odpowiedz_to_ODMOWA(monkeypatch) -> None:  # noqa: ANN001
    """Enter naciśnięty odruchowo NIE MOŻE znaczyć „tak" — pytanie dotyczy gigabajtów.

    Sprawdzamy wprost odpowiedzi, które operator poda najłatwiej: puste wejście i „n".
    """
    from husarz.launcher import cli

    for wejscie in ["", "n", "nie", "NIE", " ", "cokolwiek"]:
        monkeypatch.setattr("builtins.input", lambda _prompt, w=wejscie: w)
        assert cli._zgoda_operatora() is False, f'„{wejscie}" zostało wzięte za zgodę'


def test_zgoda_wymaga_jawnego_TAK(monkeypatch) -> None:  # noqa: ANN001
    """Nośność powyższego: gdyby funkcja zawsze zwracała False, komenda byłaby martwa."""
    from husarz.launcher import cli

    for wejscie in ["t", "T", "tak", " Tak ", "y", "yes"]:
        monkeypatch.setattr("builtins.input", lambda _prompt, w=wejscie: w)
        assert cli._zgoda_operatora() is True, f'„{wejscie}" nie zostało uznane za zgodę'


def test_brak_terminala_znaczy_ODMOWA(monkeypatch) -> None:  # noqa: ANN001
    """Uruchomienie z potoku albo jako usługa: nie ma komu wyrazić zgody.

    Bez tego `husarz bootstrap` w skrypcie startowym wywróciłby się na `EOFError` albo —
    gorzej — potraktował wyjątek jak zgodę.
    """
    from husarz.launcher import cli

    def _eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert cli._zgoda_operatora() is False


def test_przerwanie_ctrl_c_znaczy_ODMOWA(monkeypatch) -> None:  # noqa: ANN001
    """Ctrl+C w pytaniu o gigabajty to odmowa, nie wyjątek lecący przez pół programu."""
    from husarz.launcher import cli

    def _przerwij(_prompt: str) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _przerwij)
    assert cli._zgoda_operatora() is False
