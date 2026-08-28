"""Strumieniowanie odpowiedzi modelu — transport, klient, router (Etap 18l).

Najważniejsze rozstrzygnięcie tej warstwy nie dotyczy wydajności, lecz FALLBACKU.
Router ma łańcuch modeli zapasowych i przy zwykłym wywołaniu przechodzi do kolejnego, gdy
poprzedni zawiedzie. Przy strumieniu to przestaje być bezpieczne w połowie odpowiedzi:
przełączenie modelu po wysłaniu pierwszego fragmentu SKLEIŁOBY dwie różne odpowiedzi
w jedną, a użytkownik zobaczyłby początek jednej myśli i dalszy ciąg innej — bez żadnego
sygnału, że coś się stało. Milcząca niespójność jest gorsza od widocznego błędu.

Stąd reguła: **fallback działa tylko do pierwszego fragmentu.** Pilnują jej trzy testy,
po jednym na każdą możliwą sytuację, bo każdy z osobna dałby się spełnić błędną
implementacją.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from husarz.config.schema import HusarzConfig, ModelSpec
from husarz.router.client import MockClient, OpenAICompatClient, _ladunek_sse
from husarz.router.errors import AllModelsFailedError, ModelBackendError
from husarz.router.router import ModelRouter
from husarz.router.types import ChatMessage, ChatRequest

pytestmark = pytest.mark.unit

_REJESTR = {
    "glowny": {"backend": "mock", "model": "g", "tags": ["chat"], "fallback": ["zapasowy"]},
    "zapasowy": {"backend": "mock", "model": "z", "tags": ["chat"]},
}


def _config() -> HusarzConfig:
    return HusarzConfig.model_validate({"models": {"default": "glowny", "registry": _REJESTR}})


def _zadanie() -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="pytanie")])


class _Klient:
    """Klient strumieniowy, który zawodzi w wybranym momencie."""

    def __init__(self, model_id: str, tryby: dict[str, str]) -> None:
        self.model_id = model_id
        self._tryb = tryby.get(model_id)

    def chat_stream(self, request: ChatRequest) -> Iterator[str]:
        """Strumieniuje dwa fragmenty albo zawodzi przed/po pierwszym."""
        if self._tryb == "przed":
            raise ModelBackendError(self.model_id, "silnik nie odpowiada")
        yield f"[{self.model_id}] "
        if self._tryb == "po":
            raise ModelBackendError(self.model_id, "połączenie zerwane w połowie")
        yield "koniec"


def _router(tryby: dict[str, str]) -> ModelRouter:
    return ModelRouter(_config(), client_factory=lambda spec, mid: _Klient(mid, tryby))


# --------------------------------------------------------------------------------------
# Reguła fallbacku
# --------------------------------------------------------------------------------------


def test_awaria_PRZED_pierwszym_fragmentem_przechodzi_na_zapasowy() -> None:
    """Dopóki nic nie wyszło do wywołującego, awaria jest zwyczajnym powodem przejścia dalej."""
    wynik = list(_router({"glowny": "przed"}).complete_stream(_zadanie(), tags=["chat"]))

    assert wynik == ["[zapasowy] ", "koniec"]


def test_awaria_PO_pierwszym_fragmencie_KONCZY_strumien() -> None:
    """Sedno: nie wolno skleić dwóch odpowiedzi w jedną.

    Wywołujący dostaje to, co zdążyło wyjść, i błąd — zamiast dalszego ciągu z INNEGO
    modelu, którego nie da się odróżnić od kontynuacji pierwszego.
    """
    strumien = _router({"glowny": "po"}).complete_stream(_zadanie(), tags=["chat"])
    zebrane: list[str] = []

    with pytest.raises(ModelBackendError):
        for fragment in strumien:
            zebrane.append(fragment)

    assert zebrane == ["[glowny] "], "wywołujący zobaczył fragment z modelu zapasowego"


def test_awaria_WSZYSTKICH_przed_fragmentem_daje_AllModelsFailed() -> None:
    """Bez tej asercji test pierwszy przechodziłby też, gdyby fallback nigdy się nie kończył."""
    router = _router({"glowny": "przed", "zapasowy": "przed"})

    with pytest.raises(AllModelsFailedError) as blad:
        list(router.complete_stream(_zadanie(), tags=["chat"]))

    assert len(blad.value.failures) == 2


def test_klient_bez_strumieniowania_jest_POMIJANY_nie_wywraca() -> None:
    """Backend bez strumieniowania nie może zablokować całego łańcucha."""

    class _BezStrumienia:
        model_id = "glowny"

        def chat(self, request: ChatRequest):  # noqa: ANN202 - atrapa
            raise AssertionError("nieużywane")

    def fabryka(spec: ModelSpec, mid: str):  # noqa: ANN202
        return _BezStrumienia() if mid == "glowny" else _Klient(mid, {})

    router = ModelRouter(_config(), client_factory=fabryka)

    assert list(router.complete_stream(_zadanie(), tags=["chat"])) == ["[zapasowy] ", "koniec"]


# --------------------------------------------------------------------------------------
# Parsowanie SSE i klient
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("linia", "oczekiwane"),
    [
        ('data: {"a": 1}', '{"a": 1}'),
        ("data:{}", "{}"),
        ("", None),
        ("   ", None),
        (": ping utrzymujący połączenie", None),
        ("data: [DONE]", None),
        ("event: message", None),
        ("id: 42", None),
        ('data:  {"a": 1}  ', '{"a": 1}'),
    ],
)
def test_parsowanie_linii_SSE(linia: str, oczekiwane: str | None) -> None:
    """Linie sterujące i znacznik końca nie mogą trafić do treści odpowiedzi.

    Wszystkie przypadki „nie-danych" odpadają na JEDNYM warunku: przepuszczamy wyłącznie
    linie ``data:``. Osobne sprawdzanie komentarzy SSE było zbędne — wykryła to kontrola
    nośności (mutacja usuwająca je nie zaczerwieniła niczego, bo nie było czego czerwienić).
    """
    assert _ladunek_sse(linia) == oczekiwane


class _TransportStrumieniowy:
    """Atrapa transportu: oddaje przygotowane zdarzenia SSE i zapamiętuje ładunek."""

    def __init__(self, zdarzenia: list[str]) -> None:
        self._zdarzenia = zdarzenia
        self.wyslany_ladunek: dict | None = None

    def __call__(self, target, headers, payload, timeout):  # noqa: ANN001, ANN204
        raise AssertionError("ścieżka nierostrumieniowa nie powinna być użyta")

    def stream(self, target, headers, payload, timeout):  # noqa: ANN001, ANN202
        """Zwraca zaplanowane ładunki zdarzeń."""
        self.wyslany_ladunek = dict(payload)
        yield from self._zdarzenia


def _klient(transport: object) -> OpenAICompatClient:
    spec = ModelSpec(backend="openai_compat", model="m", endpoint="http://127.0.0.1:11434/v1")
    return OpenAICompatClient(
        spec, "test", api_key=None, transport=transport, resolve=lambda h: ["127.0.0.1"]
    )


def test_klient_prosi_backend_o_STRUMIEN() -> None:
    """Bez `stream: true` backend odesłałby całość naraz — i nikt by tego nie zauważył."""
    transport = _TransportStrumieniowy([json.dumps({"choices": [{"delta": {"content": "x"}}]})])

    list(_klient(transport).chat_stream(_zadanie()))

    assert transport.wyslany_ladunek is not None
    assert transport.wyslany_ladunek["stream"] is True


def test_klient_sklada_fragmenty_i_pomija_zdarzenia_bez_tresci() -> None:
    """Zdarzenie bez treści (sama rola, sam `finish_reason`) jest normalne, nie błędne."""
    zdarzenia = [
        json.dumps({"choices": [{"delta": {"role": "assistant"}}]}),
        json.dumps({"choices": [{"delta": {"content": "Ala "}}]}),
        json.dumps({"choices": [{"delta": {"content": "ma kota"}}]}),
        json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ]

    assert list(_klient(_TransportStrumieniowy(zdarzenia)).chat_stream(_zadanie())) == [
        "Ala ",
        "ma kota",
    ]


def test_uszkodzone_zdarzenie_NIE_wywraca_calej_odpowiedzi() -> None:
    """Pojedynczego zdarzenia i tak nie da się odzyskać — reszta odpowiedzi ma dojść."""
    zdarzenia = [
        json.dumps({"choices": [{"delta": {"content": "przed "}}]}),
        "{to nie jest json",
        json.dumps({"choices": [{"delta": {"content": "po"}}]}),
    ]

    assert list(_klient(_TransportStrumieniowy(zdarzenia)).chat_stream(_zadanie())) == [
        "przed ",
        "po",
    ]


def test_transport_bez_strumieniowania_daje_CZYTELNY_blad() -> None:
    """Atrapy transportu z istniejących testów nie mają metody `stream` — komunikat ma to nazwać."""

    class _Zwykly:
        def __call__(self, target, headers, payload, timeout):  # noqa: ANN001, ANN204
            return {}

    with pytest.raises(ModelBackendError, match="nie obsługuje strumieniowania"):
        list(_klient(_Zwykly()).chat_stream(_zadanie()))


def test_atrapa_modelu_strumieniuje_TE_SAMA_tresc_co_zwykle() -> None:
    """Atrapa nie może po cichu rozjechać się z wersją nierostrumieniową."""
    klient = MockClient("m", ModelSpec(backend="mock", model="testowy"))
    zadanie = _zadanie()

    assert "".join(klient.chat_stream(zadanie)) == klient.chat(zadanie).content
