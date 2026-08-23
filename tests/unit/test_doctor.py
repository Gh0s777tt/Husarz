"""Diagnoza instalacji (`husarz doctor`).

Sonda jest wstrzykiwana, więc cały zestaw działa offline. Dwa testy są regresjami wad, które
narzędzie ujawniło na SWOIM PIERWSZYM uruchomieniu — obie polegały na tym, że diagnoza mówiła
nieprawdę o stanie, który miała opisać.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.launcher.doctor import (
    SondaSystemowa,
    Stan,
    Ustalenie,
    Waga,
    sformatuj,
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
    """Sonda testowa o sterowanym zachowaniu."""

    def __init__(
        self,
        *,
        modele: list[str] | None = None,
        zapisywalny: bool | None = True,
        odmowa: str | None = None,
    ) -> None:
        self._modele = modele
        self._zapisywalny = zapisywalny
        self._odmowa = odmowa
        self.zapytania: list[str] = []

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Zwraca ustawiony powód odmowy (albo ``None`` = wolno pytać)."""
        return self._odmowa

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zwraca ustawione modele i zapisuje, o co pytano."""
        self.zapytania.append(endpoint)
        return self._modele

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Zwraca ustawiony wynik."""
        return self._zapisywalny


def _ustalenie(ustalenia: list[Ustalenie], ident: str) -> Ustalenie:
    pasujace = [u for u in ustalenia if u.id == ident]
    assert pasujace, f"brak ustalenia {ident!r} w {[u.id for u in ustalenia]}"
    return pasujace[0]


# ----------------------------------------------------------------- model czatu


def test_wylaczony_model_czatu_jest_problemem_blokujacym(make_config) -> None:
    """Luka, której schemat NIE pilnuje — sprawdzone osobno.

    `models.chat` wskazujący model z `enabled: false` przechodzi walidację bez zastrzeżeń,
    a czat wywraca się dopiero przy pierwszym żądaniu. (Wskazanie modelu spoza rejestru
    schemat ŁAPIE, więc nie ma czego diagnozować.)
    """
    rejestr: dict[str, Any] = {
        "lokalny": _REGISTRY["lokalny"],
        "wylaczony": {**_REGISTRY["lokalny"], "enabled": False},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="wylaczony")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"]))

    u = _ustalenie(ustalenia, "model-wylaczony-wlaczony")
    assert u.stan is Stan.PROBLEM
    assert u.waga is Waga.BLOKUJACA
    assert "enabled" in u.naprawa


def test_model_czatu_bez_endpointu_jest_problemem_blokujacym(make_config) -> None:
    """Druga luka schematu: brak endpointu przy backendzie, który go potrzebuje."""
    rejestr: dict[str, Any] = {
        "lokalny": _REGISTRY["lokalny"],
        "bez": {"backend": "ollama", "model": "x", "tags": ["chat"]},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="bez")

    ustalenia = zdiagnozuj(config, sonda=_Sonda())

    u = _ustalenie(ustalenia, "model-bez-u-dostawcy")
    assert u.stan is Stan.PROBLEM
    assert "endpoint" in u.naprawa


def test_brak_odpowiedzi_silnika_to_NIEZNANY_a_nie_problem(make_config) -> None:
    """Twarda zasada projektu: „nie dało się sprawdzić" NIE jest tym samym co „źle".

    Silnik może nie odpowiadać, bo jeszcze wstaje. Nazwanie tego problemem byłoby zgadywaniem;
    nazwanie sukcesem — kłamstwem. Stan NIEZNANY istnieje właśnie po to.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=None))

    u = _ustalenie(ustalenia, "model-lokalny-u-dostawcy")
    assert u.stan is Stan.NIEZNANY
    assert "ollama" in u.naprawa.lower()


def test_silnik_bez_modelu_wymienia_to_co_ma(make_config) -> None:
    """Operator musi zobaczyć, CO jest dostępne — inaczej zgaduje nazwę."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["qwen2.5-coder:7b"]))

    u = _ustalenie(ustalenia, "model-lokalny-u-dostawcy")
    assert u.stan is Stan.PROBLEM
    assert u.waga is Waga.BLOKUJACA
    assert "qwen2.5-coder:7b" in u.opis


def test_model_z_etykieta_jest_rozpoznawany(make_config) -> None:
    """Regresja: Ollama zwraca `husarz:latest`, konfiguracja mówi `husarz` — to ten sam model.

    Pierwsza wersja narzędzia twierdziła „nie ma modelu", mając go przed sobą. Przyczyna:
    normalizacja etykiety siedziała w ekstraktorze JEDNEGO z dwóch endpointów, a odpowiedział
    ten drugi (OpenAI-compat). Wykryte na pierwszym uruchomieniu.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz:latest", "qwen2.5-coder:7b"]))

    assert _ustalenie(ustalenia, "model-lokalny-u-dostawcy").stan is Stan.OK


def test_model_bez_etykiety_u_dostawcy_tez_pasuje(make_config) -> None:
    """Symetria: konfiguracja z etykietą, dostawca bez niej."""
    rejestr: dict[str, Any] = {
        "lokalny": {**_REGISTRY["lokalny"], "model": "husarz:latest"},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"]))

    assert _ustalenie(ustalenia, "model-lokalny-u-dostawcy").stan is Stan.OK


# -------------------------------------------------------------------- katalogi


def test_niezapisywalny_katalog_jest_problemem(make_config) -> None:
    """Niezapisywalny katalog audytu objawia się dopiero jako 503 przy pierwszej akcji."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"], zapisywalny=False))

    problemy = [u for u in ustalenia if u.id.startswith("katalog-")]
    assert problemy, "brak ustaleń o katalogach"
    assert all(u.stan is Stan.PROBLEM for u in problemy)


def test_nierozstrzygniety_katalog_to_NIEZNANY(make_config) -> None:
    """Znów: brak rozstrzygnięcia nie może udawać sukcesu."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"], zapisywalny=None))

    katalogi = [u for u in ustalenia if u.id.startswith("katalog-")]
    assert katalogi
    assert all(u.stan is Stan.NIEZNANY for u in katalogi)


def test_zapisywalne_katalogi_nie_zasmiecaja_wyniku(make_config) -> None:
    """Nośność: diagnoza bez problemów nie może zasypywać operatora wpisami „OK"."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"], zapisywalny=True))

    assert not [u for u in ustalenia if u.id.startswith("katalog-")]


# ---------------------------------------------------------------- podsumowanie


def test_podsumowanie_nie_moze_przeczyc_liscie(make_config) -> None:
    """Regresja: przy problemie NIEBLOKUJĄCYM podsumowanie mówiło „Wszystkie kontrole przeszły".

    Miało wypisany problem dwie linie wyżej. Pierwsza wersja liczyła wyłącznie problemy
    blokujące i stany nieznane. Wykryte na pierwszym uruchomieniu narzędzia.
    """
    ustalenia = [
        Ustalenie(id="cos", stan=Stan.PROBLEM, waga=Waga.OSTRZEZENIE, opis="coś jest nie tak")
    ]

    linie = sformatuj(ustalenia)

    tresc = "\n".join(linie)
    assert "Wszystkie kontrole przeszły" not in tresc
    assert "ostrzeżeń: 1" in tresc


def test_podsumowanie_odroznia_nieznane_od_ok() -> None:
    """Stan NIEZNANY musi być widoczny w podsumowaniu, nie milczeć."""
    linie = sformatuj(
        [Ustalenie(id="x", stan=Stan.NIEZNANY, waga=Waga.OSTRZEZENIE, opis="nie wiadomo")]
    )

    tresc = "\n".join(linie)
    assert "NIE DAŁO SIĘ" in tresc
    assert "Wszystkie kontrole przeszły" not in tresc


def test_podsumowanie_bez_zastrzezen_mowi_wprost() -> None:
    """Nośność: przy samych OK komunikat ma być jednoznaczny."""
    linie = sformatuj([Ustalenie(id="x", stan=Stan.OK, waga=Waga.INFORMACJA, opis="działa")])

    assert "Wszystkie kontrole przeszły" in "\n".join(linie)


def test_problemy_sa_na_gorze_listy(make_config) -> None:
    """Operator czyta od góry — problem nie może być pod trzema wpisami „OK"."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["co-innego"], zapisywalny=True))

    assert ustalenia[0].stan is Stan.PROBLEM


# ------------------------------------------------------- egress w realnej sondzie


def test_sonda_NIE_odpytuje_endpointu_spoza_allowlisty(make_config, monkeypatch) -> None:
    """Diagnoza nie może być skanerem portów uruchamianym przez API.

    Endpoint spoza allowlisty egress NIE jest odpytywany — bez tego wystarczyłoby wpisać
    dowolny adres jako endpoint modelu i odczytać z diagnozy, czy odpowiada.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config)
    wywolania: list[str] = []
    monkeypatch.setattr(
        SondaSystemowa, "_zapytaj", lambda self, e: wywolania.append(e) or ["cokolwiek"]
    )

    wynik = sonda.modele_u_dostawcy("https://api.example.invalid/v1")

    assert wynik is None, "endpoint spoza allowlisty NIE może być sondowany"
    assert wywolania == [], "sonda wyszła do sieci mimo bramki egress"


def test_sonda_odpytuje_endpoint_dozwolony(make_config, monkeypatch) -> None:
    """Nośność: bramka nie może blokować lokalnego silnika, czyli przypadku typowego."""
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    sonda = SondaSystemowa(config)
    wywolania: list[str] = []
    monkeypatch.setattr(
        SondaSystemowa, "_zapytaj", lambda self, e: (wywolania.append(e), ["husarz"])[1]
    )

    wynik = sonda.modele_u_dostawcy("http://localhost:11434/v1")

    assert wynik == ["husarz"]
    assert wywolania == ["http://localhost:11434/v1"]


def test_zablokowany_egress_to_INNE_ustalenie_niz_milczacy_silnik(make_config) -> None:
    """Regresja: diagnoza kłamała o przyczynie.

    Przy zablokowanym egressie mówiła „silnik nie odpowiedział", choć nikt go nie pytał —
    a operator dostawał instrukcję „uruchom ollama serve" zamiast „dodaj host do allowlisty".
    Dwie różne przyczyny wymagają dwóch różnych napraw.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(odmowa="host spoza allowlisty"))

    u = _ustalenie(ustalenia, "model-lokalny-u-dostawcy")
    assert u.stan is Stan.NIEZNANY
    assert "egress" in u.opis.lower()
    assert "allowlist" in u.naprawa
    assert "ollama serve" not in u.naprawa, "to nie jest problem z uruchomieniem silnika"


def test_rozne_warianty_modelu_NIE_sa_uznawane_za_ten_sam(make_config) -> None:
    """Regresja: FAŁSZYWE OK. Obcinanie etykiety po obu stronach zrównywało `7b` z `1.5b`.

    Diagnoza meldowała „model jest", gdy u dostawcy stał zupełnie inny wariant. Fałszywe OK
    jest gorsze niż fałszywy alarm — operator przestaje szukać. Wskazane przy przeglądzie
    przestrzeni awarii, potwierdzone uruchomieniem.
    """
    rejestr: dict[str, Any] = {
        "lokalny": {**_REGISTRY["lokalny"], "model": "qwen2.5-coder:7b"},
    }
    config = make_config(registry=rejestr, default="lokalny", chat="lokalny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["qwen2.5-coder:1.5b"]))

    u = _ustalenie(ustalenia, "model-lokalny-u-dostawcy")
    assert u.stan is Stan.PROBLEM, "inny wariant modelu NIE jest tym samym modelem"
    assert "qwen2.5-coder:1.5b" in u.opis


# ------------------------------------------- łańcuch: czat + orkiestracja + agenci


def test_diagnoza_obejmuje_orkiestracje_i_agentow(make_config) -> None:
    """Regresja: pierwsza wersja sprawdzała WYŁĄCZNIE model trybu czatu.

    Na dostarczonej konfiguracji dawało to obraz mylący — czat działał na lokalnej Ollamie,
    więc diagnoza kończyła się „ostrzeżeń: 1", podczas gdy orkiestracja i wszystkich siedmiu
    agentów wskazywało na serwery vLLM, których nikt nie uruchomił. Sprawdzone na realnej
    konfiguracji repo.
    """
    rejestr: dict[str, Any] = {
        "lokalny": _REGISTRY["lokalny"],
        "zdalny": {**_REGISTRY["lokalny"], "model": "nie-ma", "endpoint": "http://localhost:9/v1"},
    }
    config = make_config(
        registry=rejestr,
        default="zdalny",
        chat="lokalny",
        agent_models={"husarz": "zdalny"},
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"]))

    zdalne = _ustalenie(ustalenia, "model-zdalny-u-dostawcy")
    assert zdalne.stan is Stan.PROBLEM, "model orkiestracji nie został sprawdzony"
    assert "orkiestracja" in zdalne.opis
    assert "agent husarz" in zdalne.opis
    assert _ustalenie(ustalenia, "model-lokalny-u-dostawcy").stan is Stan.OK


def test_jeden_model_w_wielu_rolach_daje_JEDNO_ustalenie(make_config) -> None:
    """Siedmiu agentów na tym samym modelu nie może dać siedmiu identycznych wpisów."""
    config = make_config(
        registry=_REGISTRY,
        default="lokalny",
        chat="lokalny",
        agent_models={f"agent{i}": "lokalny" for i in range(7)},
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(modele=["husarz"]))

    dotyczace = [u for u in ustalenia if u.id.startswith("model-lokalny")]
    assert len(dotyczace) == 1, [u.id for u in dotyczace]
    assert "agent agent0" in dotyczace[0].opis


def test_silnik_pytany_RAZ_na_endpoint(make_config) -> None:
    """Kilka modeli dzieli jeden silnik — diagnoza nie ma powodu pytać go wielokrotnie."""
    rejestr: dict[str, Any] = {
        "a": _REGISTRY["lokalny"],
        "b": {**_REGISTRY["lokalny"], "model": "inny"},
    }
    config = make_config(registry=rejestr, default="a", chat="b", agent_models={"x": "a"})
    sonda = _Sonda(modele=["husarz", "inny"])

    zdiagnozuj(config, sonda=sonda)

    assert sonda.zapytania == ["http://localhost:11434/v1"], sonda.zapytania


def test_model_spoza_rejestru_nie_wywraca_diagnozy(make_config) -> None:
    """Gałąź obronna: `zdiagnozuj` przyjmuje DOWOLNY obiekt konfiguracji.

    Sprawdziłem: schemat pilnuje wszystkich odwołań do modeli (`models.chat`,
    `models.default`, `routing.agent_models`, `routing.rules`), więc przez `load_config`
    ta gałąź jest nieosiągalna — pierwotny komentarz w kodzie twierdził inaczej i był
    nieprawdziwy. Gałąź zostaje jednak celowo: diagnoza, która sama wywala się na `None`,
    byłaby bezużyteczna dokładnie wtedy, gdy jest potrzebna.
    """
    config = make_config(registry=_REGISTRY, default="lokalny", chat="lokalny")
    okrojony = config.model_copy(
        update={"models": config.models.model_copy(update={"registry": {}})}
    )

    ustalenia = zdiagnozuj(okrojony, sonda=_Sonda(modele=["husarz"]))

    u = _ustalenie(ustalenia, "model-lokalny")
    assert u.stan is Stan.PROBLEM
    assert "nie istnieje w rejestrze" in u.opis
