"""Diagnoza obejmuje modele ZAPASOWE — te, które przejmują ruch, gdy główny padnie.

**Skąd ta luka.** `_role_modeli` mapowała czat, orkiestrację i `routing.agent_models`,
a łańcuch `fallback` pomijała — czyli diagnoza milczała dokładnie o ratunku. Dokumentacja
twierdziła przy tym, że sprawdzany jest „CAŁY łańcuch". Wskazane przy przeglądzie sondy
głębokiej, zapisane do ROADMAP-u i domknięte tutaj.

Łańcuch przechodzimy tak samo jak `husarz.router.selection._expand`: rekurencyjnie,
z ochroną przed cyklem, i tylko przy `routing.fallbacks_enabled`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.launcher.doctor import Stan, Ustalenie, Waga, zdiagnozuj

pytestmark = pytest.mark.unit


class _Sonda:
    """Sonda katalogu: silnik zna wyłącznie wymienione modele."""

    def __init__(self, modele: list[str]) -> None:
        self._modele = modele

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Wolno pytać."""
        return None

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zwraca ustawione modele."""
        return self._modele

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Katalogi nie są przedmiotem tych testów."""
        return True


def _ustalenie(ustalenia: list[Ustalenie], ident: str) -> Ustalenie:
    pasujace = [u for u in ustalenia if u.id == ident]
    assert pasujace, f"brak ustalenia {ident!r} w {[u.id for u in ustalenia]}"
    return pasujace[0]


def _rejestr(**nadpisania: Any) -> dict[str, Any]:
    """Łańcuch: glowny → zapasowy → ostatnia-deska (wszystkie na tym samym silniku)."""
    baza: dict[str, Any] = {
        "glowny": {
            "backend": "ollama",
            "model": "husarz",
            "endpoint": "http://localhost:11434/v1",
            "tags": ["chat"],
            "fallback": ["zapasowy"],
        },
        "zapasowy": {
            "backend": "ollama",
            "model": "nie-ma-takiego",
            "endpoint": "http://localhost:11434/v1",
            "tags": ["chat"],
            "fallback": ["ostatnia-deska"],
        },
        "ostatnia-deska": {
            "backend": "ollama",
            "model": "tez-nie-ma",
            "endpoint": "http://localhost:11434/v1",
            "tags": ["chat"],
        },
    }
    baza.update(nadpisania)
    return baza


def test_model_zapasowy_jest_diagnozowany(make_config) -> None:
    """Sedno luki: model, który przejmuje ruch po awarii głównego, był niewidoczny."""
    config = make_config(registry=_rejestr(), default="glowny", chat="glowny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    u = _ustalenie(ustalenia, "model-zapasowy-u-dostawcy")
    assert u.stan is Stan.PROBLEM
    assert "zapasowy dla 'glowny'" in u.opis


def test_lancuch_zapasowych_przechodzimy_REKURENCYJNIE(make_config) -> None:
    """Router sięga po fallback fallbacku (`_expand` woła się rekurencyjnie), więc
    diagnoza zatrzymująca się na pierwszym poziomie znów milczałaby o ostatniej desce."""
    config = make_config(registry=_rejestr(), default="glowny", chat="glowny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    u = _ustalenie(ustalenia, "model-ostatnia-deska-u-dostawcy")
    assert "zapasowy dla 'zapasowy'" in u.opis


def test_awaria_modelu_zapasowego_to_OSTRZEZENIE_a_nie_blokada(make_config) -> None:
    """Niedziałający zapas nie blokuje niczego DZIŚ — blokuje ratunek.

    Ma to skutek praktyczny: `husarz doctor` kończy się kodem 1 tylko przy problemie
    blokującym, a komenda nadaje się do skryptu startowego. Zrównanie zepsutego zapasu
    z martwym modelem czatu zatrzymywałoby uruchomienie DZIAŁAJĄCEJ instalacji.
    """
    config = make_config(registry=_rejestr(), default="glowny", chat="glowny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    zapasowy = _ustalenie(ustalenia, "model-zapasowy-u-dostawcy")
    glowny = _ustalenie(ustalenia, "model-glowny-u-dostawcy")
    assert zapasowy.waga is Waga.OSTRZEZENIE
    assert glowny.stan is Stan.OK
    assert not [u for u in ustalenia if u.waga is Waga.BLOKUJACA and u.stan is Stan.PROBLEM]


def test_model_w_roli_glownej_I_zapasowej_pozostaje_blokujacy(make_config) -> None:
    """Nośność powyższego: złagodzenie wagi dotyczy WYŁĄCZNIE modeli czysto zapasowych.

    Gdyby wystarczyło samo wystąpienie w roli zapasowej, martwy model czatu, wskazany
    gdzieś jako fallback, przestałby blokować — czyli najgroźniejszy przypadek zostałby
    zdegradowany do ostrzeżenia.
    """
    rejestr = _rejestr()
    rejestr["glowny"]["fallback"] = ["zapasowy"]
    rejestr["zapasowy"]["fallback"] = []
    config = make_config(registry=rejestr, default="glowny", chat="zapasowy")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    u = _ustalenie(ustalenia, "model-zapasowy-u-dostawcy")
    assert "tryb czatu" in u.opis and "zapasowy dla 'glowny'" in u.opis
    assert u.waga is Waga.BLOKUJACA, "model obsługujący czat nie może być ostrzeżeniem"


def test_wylaczone_fallbacki_nie_sa_diagnozowane(make_config) -> None:
    """`routing.fallbacks_enabled: false` znaczy, że router NIGDY po nie nie sięgnie —
    diagnozowanie ich byłoby zgłaszaniem problemów w drodze, której nie ma."""
    config = make_config(
        registry=_rejestr(), default="glowny", chat="glowny", fallbacks_enabled=False
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    assert not [u for u in ustalenia if u.id.startswith("model-zapasowy")]
    assert not [u for u in ustalenia if u.id.startswith("model-ostatnia-deska")]


def test_cykl_w_lancuchu_zapasowych_nie_zawiesza_diagnozy(make_config) -> None:
    """Konfiguracja może wskazywać fallbacki w kółko — narzędzie ma zawsze odpowiedzieć.

    Schemat tego nie zabrania, a router radzi sobie zbiorem odwiedzonych; diagnoza musi
    zachować się tak samo.
    """
    rejestr = _rejestr()
    rejestr["ostatnia-deska"]["fallback"] = ["glowny"]
    config = make_config(registry=rejestr, default="glowny", chat="glowny")

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    dotyczace = {u.id for u in ustalenia if u.id.startswith("model-")}
    assert "model-ostatnia-deska-u-dostawcy" in dotyczace
    # `glowny` ma zostać przy swojej roli głównej, a nie dostać etykiety zapasowej po cyklu.
    assert "zapasowy dla" not in _ustalenie(ustalenia, "model-glowny-u-dostawcy").opis


def test_modele_z_regul_routingu_tez_sa_diagnozowane(make_config) -> None:
    """`routing.rules[].prefer` to jawne przypisanie modelu przez operatora."""
    rejestr = _rejestr()
    rejestr["z-reguly"] = {
        "backend": "ollama",
        "model": "nieznany-modelowi",
        "endpoint": "http://localhost:11434/v1",
        "tags": ["code"],
    }
    config = make_config(
        registry=rejestr,
        default="glowny",
        chat="glowny",
        rules=[{"match_tags": ["code"], "prefer": ["z-reguly"]}],
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    u = _ustalenie(ustalenia, "model-z-reguly-u-dostawcy")
    assert "reguła routingu [code]" in u.opis


def test_agent_ustawiony_na_auto_nie_tworzy_ustalenia_o_modelu_auto(make_config) -> None:
    """`auto` to nie identyfikator modelu, tylko „wybierz sam".

    Bez tego diagnoza zgłaszałaby „model 'auto' nie istnieje w rejestrze" — problem
    zmyślony, którego operator nie miałby jak naprawić.
    """
    config = make_config(
        registry=_rejestr(), default="glowny", chat="glowny", agent_models={"husarz": "auto"}
    )

    ustalenia = zdiagnozuj(config, sonda=_Sonda(["husarz"]))

    assert not [u for u in ustalenia if u.id.startswith("model-auto")]
