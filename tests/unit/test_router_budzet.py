"""Bramka budżetu okna kontekstu w routerze — pominięcie kandydata, nie przerwanie łańcucha.

Zaobserwowany problem, który to domyka: przy modelu 7B i pętli narzędziowej rozmowa rośnie
o wyniki narzędzi (JSON, gęsty tokenowo), przekracza okno modelu, backend zwraca błąd albo po
cichu ucina kontekst — a agent wypala limit iteracji, nie wiedząc dlaczego.

Kluczowa decyzja projektowa i główny przedmiot tych testów: niezmieszczenie się jest
POMINIĘCIEM KANDYDATA, nie błędem. Prompt za duży dla modelu 7B może wejść do fallbacku
o większym oknie, a wyjątek zamknąłby tę drogę.
"""

from __future__ import annotations

import pytest

from husarz.router import (
    AllModelsFailedError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelRouter,
)

pytestmark = pytest.mark.unit

# `maly` ma OKNO 1000 tokenów i fallback do `duzy` z oknem 100 000.
_REGISTRY = {
    "maly": {
        "backend": "mock",
        "model": "maly",
        "tags": ["code"],
        "context_length": 1000,
        "fallback": ["duzy"],
    },
    "duzy": {"backend": "mock", "model": "duzy", "tags": ["code"], "context_length": 100_000},
}


class FakeClient:
    """Klient testowy zapisujący, który model faktycznie dostał żądanie."""

    def __init__(self, model_id: str, wywolania: list[str]) -> None:
        self.model_id = model_id
        self._wywolania = wywolania

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Zapisuje wywołanie i zwraca odpowiedź."""
        self._wywolania.append(self.model_id)
        return ChatResponse(model=self.model_id, content="ok", finish_reason="stop")


def _router(make_config, wywolania: list[str]) -> ModelRouter:
    config = make_config(registry=_REGISTRY, default="maly")
    return ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid, wywolania))


def _dlugie(znakow: int) -> ChatRequest:
    return ChatRequest(messages=[ChatMessage("user", "x" * znakow)], max_tokens=64)


def test_krotki_prompt_trafia_do_pierwszego_kandydata(make_config) -> None:
    """Nośność: bramka nie może zmieniać zachowania dla zwykłych rozmów."""
    wywolania: list[str] = []

    odp = _router(make_config, wywolania).complete(_dlugie(50), model="maly")

    assert odp.model == "maly"
    assert wywolania == ["maly"]


def test_za_dlugi_prompt_omija_maly_model_i_trafia_do_fallbacku(make_config) -> None:
    """Sedno poprawki: niezmieszczenie się to POMINIĘCIE, nie błąd.

    Gdyby bramka rzucała wyjątkiem, model o większym oknie nigdy by nie dostał szansy —
    a to on jest właściwą odpowiedzią na „prompt za duży dla siedmiu miliardów parametrów".
    """
    wywolania: list[str] = []

    odp = _router(make_config, wywolania).complete(_dlugie(20_000), model="maly")

    assert odp.model == "duzy", "żądanie nie przeszło do modelu z większym oknem"
    assert wywolania == ["duzy"], "mały model NIE powinien dostać żądania"


def test_gdy_zaden_model_nie_ma_dosc_okna_blad_niesie_powod(make_config) -> None:
    """Komunikat musi tłumaczyć, CO się stało — inaczej zostaje „wszystkie modele zawiodły"."""
    wywolania: list[str] = []
    config = make_config(
        registry={
            "maly": {"backend": "mock", "model": "maly", "tags": ["code"], "context_length": 1000}
        },
        default="maly",
    )
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid, wywolania))

    with pytest.raises(AllModelsFailedError) as exc:
        router.complete(_dlugie(20_000), model="maly")

    tresc = str(exc.value)
    assert "okn" in tresc.lower(), tresc
    assert "1000" in tresc, tresc
    assert wywolania == [], "backend nie powinien zostać wywołany ani razu"


def test_rezerwa_na_odpowiedz_wchodzi_do_rachunku_routera(make_config) -> None:
    """Ten sam prompt przechodzi z małym `max_tokens`, a odpada z dużym.

    Bez wliczania rezerwy oba przypadki dałyby ten sam wynik — a model dostałby prompt,
    na który nie ma już czym odpowiedzieć.
    """
    config = make_config(
        registry={
            "jeden": {
                "backend": "mock",
                "model": "jeden",
                "tags": ["code"],
                "context_length": 2000,
            }
        },
        default="jeden",
    )
    wywolania: list[str] = []
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid, wywolania))
    tresc = [ChatMessage("user", "z" * 2_000)]

    assert (
        router.complete(ChatRequest(messages=tresc, max_tokens=16), model="jeden").model == "jeden"
    )

    with pytest.raises(AllModelsFailedError):
        router.complete(ChatRequest(messages=tresc, max_tokens=1_500), model="jeden")
