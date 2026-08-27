"""Sonda głęboka (`husarz doctor --probe`) — kontrola SKUTKU zamiast deklaracji.

**Po co ta sonda istnieje.** Pozostałe kontrole diagnozy pytają silnik, czy WYMIENIA model
w katalogu. To deklaracja. Odtworzone na realnej instalacji: endpoint bez `/v1` wymienia model
(`/api/tags` odpowiada), więc kontrola katalogu kończy się „[ok] model jest dostępny", a czat
i tak zwraca 502 — bo `POST /chat/completions` daje 404. Sonda głęboka zadaje modelowi
prawdziwe pytanie i to wyłapuje.

Sondy są wstrzykiwane, więc cały ten zestaw działa offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config.schema import ModelSpec
from husarz.launcher.doctor import (
    OdpowiedzModelu,
    PowodOdmowy,
    SondaSystemowa,
    Stan,
    Ustalenie,
    Waga,
    zdiagnozuj,
)

pytestmark = pytest.mark.unit

_REGISTRY = {
    "lokalny": {
        "backend": "ollama",
        "model": "husarz",
        "endpoint": "http://localhost:11434/v1",
        "tags": ["chat"],
    }
}


class _Sonda:
    """Sonda katalogu o sterowanym zachowaniu.

    ``modele`` NIE ma wartości domyślnej celowo. Pierwsza wersja atrapy traktowała ``None``
    jako „użyj domyślnej listy", przez co przypadek „silnik milczy" (też ``None``) był
    nieosiągalny — test miał sprawdzać, że milczący silnik nie jest pytany głębiej, a wchodził
    w ścieżkę sukcesu. Wieloznaczność w atrapie to gotowy test przechodzący z niewłaściwego
    powodu; wykryte, gdy test zaczerwienił się „bez powodu".
    """

    def __init__(self, modele: list[str] | None, *, odmowa: str | None = None) -> None:
        self._modele = modele
        self._odmowa = odmowa

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Zwraca ustawiony powód odmowy."""
        return self._odmowa

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zwraca ustawione modele."""
        return self._modele

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Katalogi w porządku — nie są przedmiotem tych testów."""
        return True


class _Gleboka:
    """Sonda głęboka o sterowanym wyniku; zapisuje, o co i ile razy pytano."""

    def __init__(self, wynik: OdpowiedzModelu) -> None:
        self._wynik = wynik
        self.zapytania: list[str] = []

    def zapytaj_model(self, model_id: str, spec: ModelSpec) -> OdpowiedzModelu:
        """Zapisuje wywołanie i zwraca ustawiony wynik."""
        self.zapytania.append(model_id)
        return self._wynik


def _ustalenie(ustalenia: list[Ustalenie], ident: str) -> Ustalenie:
    pasujace = [u for u in ustalenia if u.id == ident]
    assert pasujace, f"brak ustalenia {ident!r} w {[u.id for u in ustalenia]}"
    return pasujace[0]


def _bez(ustalenia: list[Ustalenie], ident: str) -> None:
    assert not [u for u in ustalenia if u.id == ident], f"ustalenie {ident!r} NIE powinno istnieć"


# --------------------------------------------------------------- opt-in strukturalny


def test_bez_sondy_glebokiej_model_NIE_jest_pytany(make_config) -> None:
    """Domyślnie diagnoza nie wysyła żądania do modelu — kontrola ma skutki uboczne.

    Opt-in jest strukturalny: bez obiektu sondy nie ma czym zapytać. Flaga logiczna dawałaby
    ten sam efekt tylko dopóty, dopóki nikt jej nie przeoczy.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    _bez(ustalenia, "model-lokalny-odpowiada")
    assert _ustalenie(ustalenia, "model-lokalny-u-dostawcy").stan is Stan.OK


def test_z_sonda_gleboka_model_odpowiada(make_config) -> None:
    """Nośność powyższego: z sondą ustalenie MUSI się pojawić, inaczej test jest pusty."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="Pong!", sekundy=0.4))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.OK
    assert "Pong!" in u.opis
    assert "0.4 s" in u.opis
    assert gleboka.zapytania == ["lokalny"]


# ------------------------------------------------- kiedy sondy NIE wolno uruchamiać


def test_model_nieobecny_w_katalogu_NIE_jest_pytany(make_config) -> None:
    """Żądanie skazane na porażkę i drugie ustalenie mówiące to samo innymi słowami."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="x"))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["co-innego"]), sonda_gleboka=gleboka)

    assert gleboka.zapytania == [], "pytano model, którego silnik nie wymienia"
    _bez(ustalenia, "model-lokalny-odpowiada")


def test_milczacy_silnik_NIE_jest_pytany_glebiej(make_config) -> None:
    """Skoro katalog nie odpowiedział, żądanie uzupełnienia tym bardziej nie ma sensu."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="x"))

    zdiagnozuj(config, sonda=_Sonda(None), sonda_gleboka=gleboka)

    assert gleboka.zapytania == []


def test_zablokowany_egress_NIE_dopuszcza_do_zapytania(make_config) -> None:
    """Sonda głęboka jest drogą WYCHODZĄCĄ — bramka obowiązuje ją tak samo jak katalog."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="x"))

    sonda = _Sonda(["husarz"], odmowa="host spoza allowlisty")
    zdiagnozuj(config, sonda=sonda, sonda_gleboka=gleboka)

    assert gleboka.zapytania == [], "sonda wyszła do modelu mimo blokady egress"


# ------------------------------------------------------- rozróżnianie wyników


def test_PUSTA_odpowiedz_to_problem_a_nie_sukces(make_config) -> None:
    """Fałszywe OK jest w diagnozie gorsze niż fałszywy alarm.

    HTTP 200 z pustą treścią znaczy, że model wstał, ale nie generuje — czat dostanie to samo.
    Uznanie tego za sukces odesłałoby operatora od jedynego miejsca, gdzie jest przyczyna.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="   \n  "))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.PROBLEM
    assert u.waga is Waga.BLOKUJACA
    assert "PUSTĄ" in u.opis


def test_timeout_to_NIEZNANY_a_nie_problem(make_config) -> None:
    """Model bywa właśnie ładowany do pamięci — to nie jest dowód awarii.

    Zmierzone na realnej instalacji: to samo pytanie do tego samego modelu zajęło 18,9 s przy
    zimnym starcie i 0,9 s zaraz potem. Nazwanie pierwszego przypadku awarią byłoby zgadywaniem.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.TIMEOUT))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.NIEZNANY, "timeout nie jest dowodem awarii"
    assert "--probe-timeout" in u.naprawa


@pytest.mark.parametrize(
    ("powod", "fragment_naprawy"),
    [
        (PowodOdmowy.UWIERZYTELNIENIE, "api_key_ref"),
        (PowodOdmowy.NIE_ZNALEZIONO, "/v1"),
        (PowodOdmowy.BLAD_SILNIKA, "pamięci"),
        (PowodOdmowy.ZLA_ODPOWIEDZ, "OpenAI"),
        (PowodOdmowy.BRAK_SEKRETU, "NIE"),
        (PowodOdmowy.EGRESS, "allowlist"),
    ],
)
def test_kazda_kategoria_niesie_WLASCIWA_naprawe(
    make_config, powod: PowodOdmowy, fragment_naprawy: str
) -> None:
    """Regresja klasy „diagnoza kłamie o przyczynie".

    Odrzucony klucz API i brak pamięci w silniku dają ten sam generyczny komunikat transportu.
    Gdyby diagnoza podawała jedną radę na wszystko, operator z błędnym `api_key_ref` dostałby
    „uruchom ollama serve" — instrukcję do problemu, którego nie ma.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=False, powod=powod))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.PROBLEM, f"{powod} to nie jest stan nieznany"
    assert fragment_naprawy in u.naprawa, u.naprawa
    assert powod.value in u.opis


def test_odpowiedz_po_czasie_dluzszym_niz_limit_produkcyjny_to_PROBLEM(make_config) -> None:
    """Druga pułapka fałszywego OK — tym razem ukryta w samej konstrukcji sondy.

    Sonda daje modelowi WIĘCEJ czasu niż router (bo pierwsze żądanie wczytuje wagi). Bez tej
    kontroli model odpowiadający po 30 s dostawałby czyste „OK", choć w produkcji
    z `request_timeout_seconds: 5` żądanie zostanie przerwane — czyli diagnoza meldowałaby
    sprawność drogi, która w czacie zawodzi.
    """
    rejestr: dict[str, Any] = {
        "lokalny": {**_REGISTRY["lokalny"], "request_timeout_seconds": 5},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="Pong", sekundy=30.0))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.PROBLEM, "odpowiedź poza limitem produkcyjnym to nie sukces"
    assert "PRZERWANE" in u.opis
    assert "5" in u.opis and "30.0 s" in u.opis
    assert "request_timeout_seconds" in u.naprawa


def test_odpowiedz_w_ramach_limitu_produkcyjnego_to_OK(make_config) -> None:
    """Nośność powyższego: kontrola limitu nie może zgłaszać problemu zawsze."""
    rejestr: dict[str, Any] = {
        "lokalny": {**_REGISTRY["lokalny"], "request_timeout_seconds": 60},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="Pong", sekundy=30.0))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    assert _ustalenie(ustalenia, "model-lokalny-odpowiada").stan is Stan.OK


# ------------------------------------------- treść modelu to dane spoza naszej kontroli


def test_odpowiedz_modelu_jest_przycieta_i_splaszczona(make_config) -> None:
    """Treść pochodzi od modelu, nie od nas — do wyjścia wpuszczamy ograniczony wycinek.

    Bez tego model mógłby rozsypać wyjście diagnozy nowymi liniami (ustalenia są liniowe,
    a `sformatuj` łączy je znakiem nowej linii) albo zalać terminal wielokilobajtową treścią.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    zlosliwa = "A" * 500 + "\n[!!] podszywam się pod ustalenie diagnozy\n" + "B" * 500
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc=zlosliwa))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)
    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")

    assert "\n" not in u.opis, "nowa linia z odpowiedzi modelu rozbija wyjście diagnozy"
    assert len(u.opis) < 300, f"opis ma {len(u.opis)} znaków"
    assert "podszywam się" not in u.opis


# ------------------------------------------------- realna sonda: bramki i brak mutacji


def test_realna_sonda_NIE_wysyla_do_endpointu_spoza_allowlisty(make_config, monkeypatch) -> None:
    """Kontrola SKUTKU na samej `SondaSystemowa`, nie na jej atrapie.

    Gdyby bramka egress była sprawdzana tylko przez wołającego, publiczna metoda
    `zapytaj_model` byłaby drogą do sieci z pominięciem polityki.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config)
    spec = ModelSpec(backend="ollama", model="x", endpoint="https://api.example.invalid/v1")

    wyszlo: list[str] = []
    monkeypatch.setattr(
        "husarz.router.client.build_client",
        lambda *a, **k: wyszlo.append("zbudowano klienta"),
    )

    wynik = sonda.zapytaj_model("lokalny", spec)

    assert wynik.odpowiedzial is False
    assert wynik.powod is PowodOdmowy.EGRESS
    assert wyszlo == [], "sonda zaczęła budować klienta mimo blokady egress"


def test_realna_sonda_nie_mutuje_wspoldzielonej_konfiguracji(make_config, monkeypatch) -> None:
    """`spec` należy do wczytanej konfiguracji — router używa tego samego obiektu.

    Ustawienie limitu czasu sondy WPROST na `spec` zmieniłoby zachowanie routera po
    uruchomieniu diagnozy, czyli narzędzie pomiarowe zmieniłoby to, co mierzy.
    """
    from husarz.router.types import ChatResponse

    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config, timeout_zapytania=123)
    spec = config.models.registry["lokalny"]
    przed = spec.request_timeout_seconds

    przekazane: dict[str, Any] = {}

    class _Klient:
        def chat(self, request):  # noqa: ANN001, ANN202
            return ChatResponse(model="lokalny", content="pong", finish_reason="stop")

    def _fake_build(spec_arg, model_id, **kwargs):  # noqa: ANN001, ANN202
        przekazane["timeout"] = spec_arg.request_timeout_seconds
        return _Klient()

    monkeypatch.setattr("husarz.router.client.build_client", _fake_build)

    wynik = sonda.zapytaj_model("lokalny", spec)

    assert wynik.odpowiedzial is True
    assert przekazane["timeout"] == 123, "limit sondy nie dotarł do klienta"
    assert spec.request_timeout_seconds == przed, "sonda ZMUTOWAŁA współdzieloną konfigurację"
    assert przed != 123, "test byłby pusty, gdyby wartości były równe"


def test_limit_sondy_NIGDY_nie_jest_nizszy_niz_produkcyjny(make_config, monkeypatch) -> None:
    """Sonda ma być co najmniej tak cierpliwa jak router — inaczej zmyśla timeout.

    Pierwsza wersja narzucała `--probe-timeout` bezwarunkowo, więc model z
    `request_timeout_seconds: 120` sondowany domyślnymi 60 s dostawał „timeout", choć
    router by na niego poczekał. Fałszywy alarm z myląca radą („powtórz z dłuższym limitem"),
    gdy problemu nie ma.
    """
    from husarz.router.types import ChatResponse

    rejestr: dict[str, Any] = {
        "lokalny": {**_REGISTRY["lokalny"], "request_timeout_seconds": 120},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config, timeout_zapytania=5)
    przekazane: dict[str, Any] = {}

    class _Klient:
        def chat(self, request):  # noqa: ANN001, ANN202
            return ChatResponse(model="lokalny", content="pong", finish_reason="stop")

    def _fake_build(spec_arg, model_id, **kwargs):  # noqa: ANN001, ANN202
        przekazane["timeout"] = spec_arg.request_timeout_seconds
        return _Klient()

    monkeypatch.setattr("husarz.router.client.build_client", _fake_build)

    sonda.zapytaj_model("lokalny", config.models.registry["lokalny"])

    assert przekazane["timeout"] == 120, "sonda obcięła limit PONIŻEJ produkcyjnego"


def test_nieoczekiwany_blad_budowy_klienta_NIE_wywraca_diagnozy(make_config, monkeypatch) -> None:
    """Fabryka klienta woła kod dostawcy sekretów, a ten może zgłosić cokolwiek.

    Pierwsza wersja łapała tu wyłącznie `ModelBackendError`, opierając się na komentarzu
    „jedyny powód, dla którego build_client zawodzi". Twierdzenie było nieuprawnione —
    wyjątek z Vaulta albo błąd wejścia-wyjścia wywracał CAŁĄ diagnozę, czyli narzędzie
    padało dokładnie wtedy, gdy było potrzebne.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config)

    def _wybuch(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("dostawca sekretów padł")

    monkeypatch.setattr("husarz.router.client.build_client", _wybuch)

    wynik = sonda.zapytaj_model("lokalny", config.models.registry["lokalny"])

    assert wynik.odpowiedzial is False
    assert wynik.powod is PowodOdmowy.INNY


def test_kazda_kategoria_ma_instrukcje_naprawy() -> None:
    """Dopisanie kategorii BEZ instrukcji wywróciłoby diagnozę na `KeyError`.

    Narzędzie, które samo się przewraca, jest bezużyteczne dokładnie wtedy, gdy jest
    potrzebne. Mapa jest jawna właśnie po to, żeby nowa przyczyna wymagała świadomego
    dopisania rady — ten test pilnuje, żeby nikt nie dopisał samej przyczyny.
    """
    from husarz.launcher.doctor import _NAPRAWA_ODMOWY

    bez_naprawy = [p.value for p in PowodOdmowy if p not in _NAPRAWA_ODMOWY]
    assert not bez_naprawy, f"kategorie bez instrukcji naprawy: {bez_naprawy}"
    puste = [p.value for p, tekst in _NAPRAWA_ODMOWY.items() if not tekst.strip()]
    assert not puste, f"kategorie z pustą instrukcją: {puste}"


# --------------------------------- wady wykryte przeglądem adwersaryjnym (regresje)


def test_limit_EFEKTYWNY_gdy_pole_nie_jest_ustawione(make_config) -> None:
    """Najdroższa wada tej zmiany: kontrola limitu była martwa dla KAŻDEGO modelu repo.

    `request_timeout_seconds: None` NIE znaczy „bez limitu" — klient podstawia wtedy
    `DEFAULT_TIMEOUT_SECONDS`. Żaden model w dostarczonej konfiguracji tego pola nie ustawia,
    więc warunek `if limit is not None` nie odpalał się nigdy. Co gorsza, droga do fałszywego
    OK prowadziła przez radę samego narzędzia: po pierwszym „timeout" operator podnosił
    `--probe-timeout`, a wtedy model odpowiadający po 200 s dostawał czyste „OK" — mimo że
    czat przerywa go po 60.
    """
    from husarz.router.client import DEFAULT_TIMEOUT_SECONDS

    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    assert config.models.registry["lokalny"].request_timeout_seconds is None, "założenie testu"
    gleboka = _Gleboka(
        OdpowiedzModelu(odpowiedzial=True, tresc="Pong", sekundy=DEFAULT_TIMEOUT_SECONDS + 30)
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-lokalny-odpowiada")
    assert u.stan is Stan.PROBLEM
    assert str(DEFAULT_TIMEOUT_SECONDS) in u.opis
    assert "NIE jest ustawione" in u.opis, "operator musi wiedzieć, skąd wziął się limit"
    assert "Dopisz" in u.naprawa, "rada nie może kazać PODNOSIĆ pola, którego w pliku nie ma"


def test_szybka_odpowiedz_bez_ustawionego_pola_to_OK(make_config) -> None:
    """Nośność powyższego: kontrola nie może zgłaszać problemu zawsze."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="Pong", sekundy=1.0))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    assert _ustalenie(ustalenia, "model-lokalny-odpowiada").stan is Stan.OK


def test_backend_mock_NIE_jest_dowodem_niczego(make_config) -> None:
    """`MockClient` odpowiada z pamięci — „ODPOWIEDZIAŁ" byłoby fałszywym OK.

    I to fałszywym OK w mechanizmie, który powstał, żeby fałszywe OK wykrywać.
    """
    rejestr: dict[str, Any] = {
        "udawany": {
            "backend": "mock",
            "model": "udawany",
            "endpoint": "http://x/v1",
            "tags": ["chat"],
        }
    }
    config = make_config(registry=rejestr, default="udawany", chat="udawany")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="[mock] ping"))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["udawany"]), sonda_gleboka=gleboka)

    u = _ustalenie(ustalenia, "model-udawany-odpowiada")
    assert u.stan is Stan.NIEZNANY, "mock nie może dawać stanu OK"
    assert gleboka.zapytania == [], "mock nie powinien być w ogóle pytany"
    assert "mock" in u.opis


def test_model_WYLACZONY_nie_dostaje_zadania(make_config) -> None:
    """Wysyłanie żądania do modelu, o którym linijkę wyżej piszemy „jest WYŁĄCZONY",
    byłoby wewnętrznie sprzeczne — a ustalenie „[ok] ODPOWIEDZIAŁ" wprost mylące."""
    rejestr: dict[str, Any] = {
        "lokalny": _REGISTRY["lokalny"],
        "wylaczony": {**_REGISTRY["lokalny"], "enabled": False},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="wylaczony")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="Pong"))

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka)

    assert "wylaczony" not in gleboka.zapytania, "pytano model wyłączony"
    _bez(ustalenia, "model-wylaczony-odpowiada")
    assert _ustalenie(ustalenia, "model-wylaczony-wlaczony").stan is Stan.PROBLEM


def test_blokada_pinu_IP_to_INNA_kategoria_niz_zly_format(make_config) -> None:
    """Regresja klasy „diagnoza kłamie o przyczynie" — tym razem dla anty-SSRF.

    `EgressError` z pinowania IP (ADR-0020) nie jest wyjątkiem httpx, więc wpadał do
    „zła odpowiedź" i operator dostawał radę o formacie OpenAI, choć żądanie w ogóle nie
    opuściło maszyny (nazwa wskazała zakres zabroniony albo DNS nie odpowiedział).
    """
    from husarz.core.errors import EgressError
    from husarz.launcher.doctor import _kategoria_bledu
    from husarz.router.errors import ModelBackendError

    try:
        try:
            raise EgressError("nazwa rozwiązała się na zakres metadanych chmury")
        except EgressError as exc:
            raise ModelBackendError("lokalny", str(exc)) from exc
    except ModelBackendError as exc:
        assert _kategoria_bledu(exc) is PowodOdmowy.ROZWIAZANIE_NAZWY

    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=False, powod=PowodOdmowy.ROZWIAZANIE_NAZWY))
    u = _ustalenie(
        zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka),
        "model-lokalny-odpowiada",
    )
    assert "DNS" in u.naprawa
    assert "OpenAI" not in u.naprawa, "to nie jest problem z formatem odpowiedzi"
    assert "NIE wysłano" in u.opis, "żądanie nie opuściło maszyny — opis musi to mówić"


@pytest.mark.parametrize(
    "powod", [PowodOdmowy.EGRESS, PowodOdmowy.BRAK_SEKRETU, PowodOdmowy.BRAK_ENDPOINTU]
)
def test_gdy_NIC_nie_wyslano_opis_nie_moze_mowic_o_braku_odpowiedzi(
    make_config, powod: PowodOdmowy
) -> None:
    """„Silnik nie odpowiedział" przy niewysłanym żądaniu to nieprawda o przyczynie.

    Dokładnie ten błąd naprawiono wcześniej dla kontroli katalogu; sonda głęboka powtórzyła go
    dla swoich trzech kategorii, w których nic nie leci do sieci.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=False, powod=powod))

    u = _ustalenie(
        zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka),
        "model-lokalny-odpowiada",
    )

    assert "NIE wysłano" in u.opis
    assert "NIE odpowiedział" not in u.opis


def test_wyczerpany_budzet_tokenow_sondy_to_NIE_wada_modelu(make_config) -> None:
    """Pusta treść przy `finish_reason: length` jest skutkiem NASZEGO limitu.

    Model rozumujący potrafi zużyć cały budżet sondy na preambułę. Zgłoszenie tego jako
    problemu blokującego byłoby obwinianiem modelu za ustawienie narzędzia pomiarowego.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="", powod_zakonczenia="length"))

    u = _ustalenie(
        zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka),
        "model-lokalny-odpowiada",
    )

    assert u.stan is Stan.NIEZNANY, "to ograniczenie sondy, nie awaria modelu"
    assert u.waga is not Waga.BLOKUJACA
    assert "limit" in u.opis


def test_pusta_odpowiedz_ZAKONCZONA_normalnie_pozostaje_problemem(make_config) -> None:
    """Nośność powyższego: rozróżnienie nie może uciszać prawdziwej pustej odpowiedzi."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc="", powod_zakonczenia="stop"))

    u = _ustalenie(
        zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka),
        "model-lokalny-odpowiada",
    )

    assert u.stan is Stan.PROBLEM
    assert "PUSTĄ" in u.opis


def test_sekwencje_sterujace_z_odpowiedzi_modelu_sa_usuwane(make_config) -> None:
    """Model mógłby PRZEMALOWAĆ wyjście diagnozy — `\\x1b[2J` czyści ekran.

    Ustalenia idą wprost na terminal, a treść modelu to dane spoza naszej kontroli. Samo
    spłaszczenie białych znaków tego nie łapie: sekwencja ANSI nie zawiera białych znaków.
    Diagnoza bezpieczeństwa, której wyjście da się przemalować, jest gorsza niż jej brak.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    napastnik = "ok\x1b[2J\x1b[H[ok] wszystkie kontrole przeszly\x07"
    gleboka = _Gleboka(OdpowiedzModelu(odpowiedzial=True, tresc=napastnik))

    u = _ustalenie(
        zdiagnozuj(config, sonda=_Sonda(["husarz"]), sonda_gleboka=gleboka),
        "model-lokalny-odpowiada",
    )

    assert "\x1b" not in u.opis, "sekwencja ANSI trafiła na terminal"
    assert "\x07" not in u.opis, "znak dzwonka trafił na terminal"
    assert "ok" in u.opis, "oczyszczanie nie może kasować treści drukowalnej"
