"""Wyłącznik bezpiecznikowy routera — odsuwanie modeli, które właśnie zawiodły.

**Problem.** Model, który przed sekundą przekroczył limit czasu, przy następnym żądaniu
NADAL był pierwszym kandydatem. Każde kolejne żądanie płaciło pełny limit czasu, zanim
spadło na fallback — a limity bywają liczone w dziesiątkach sekund. Przy padniętym modelu
głównym cała platforma zwalniała przy każdym zapytaniu, i to niewytłumaczalnie dla
użytkownika: odpowiedzi przychodziły, tylko bardzo wolno.

Trzy testy niosą tu sedno i każdy pilnuje innej granicy:

* `test_odsuniety_model_NIE_znika_z_listy` — odsunięcie, nie wykluczenie. Gdyby padło
  wszystko, wykluczanie zostawiłoby pustą listę kandydatów i twardą odmowę zamiast próby.
* `test_pominiecie_z_powodu_ZADANIA_nie_jest_awaria` — model pominięty, bo prompt był za
  długi, jest w pełni zdrowy. Karanie go zdegradowałoby go za cudzy błąd.
* `test_sukces_ZERUJE_licznik` — licznik jest KOLEJNYCH awarii, nie sumy. Model działający
  z przerwami nie ma się dogrywać do wyłączenia przez tydzień drobnych potknięć.
"""

from __future__ import annotations

import pytest

from husarz.config.schema import HusarzConfig
from husarz.router.errors import AllModelsFailedError, ModelBackendError
from husarz.router.router import ModelRouter
from husarz.router.types import ChatMessage, ChatRequest, ChatResponse
from husarz.router.zdrowie import RejestrZdrowia


class _Zegar:
    """Wstrzykiwalny zegar — testy odsunięcia w czasie muszą być deterministyczne."""

    def __init__(self) -> None:
        self.teraz = 0.0

    def __call__(self) -> float:
        return self.teraz


def _rejestr(zegar: _Zegar, *, prog: int = 3, odsuniecie: float = 30.0) -> RejestrZdrowia:
    return RejestrZdrowia(awarii_do_otwarcia=prog, odsuniecie_sekund=odsuniecie, zegar=zegar)


# --------------------------------------------------------------------------------------
# Sam rejestr
# --------------------------------------------------------------------------------------


def test_wylacznik_otwiera_sie_dopiero_na_PROGU() -> None:
    """Jedna awaria to nie awaria — próg chroni przed odsuwaniem na pojedynczym błędzie."""
    zegar = _Zegar()
    rejestr = _rejestr(zegar, prog=3)

    for _ in range(2):
        rejestr.odnotuj_awarie("model")
    assert rejestr.odsuniety("model") is False

    rejestr.odnotuj_awarie("model")
    assert rejestr.odsuniety("model") is True


def test_odsuniecie_WYGASA_po_czasie() -> None:
    """Wyłącznik ma spowalniać ruch do padniętego modelu, a nie skreślać go na zawsze."""
    zegar = _Zegar()
    rejestr = _rejestr(zegar, prog=1, odsuniecie=30.0)
    rejestr.odnotuj_awarie("model")
    assert rejestr.odsuniety("model") is True, "założenie testu"

    zegar.teraz += 30.0

    assert rejestr.odsuniety("model") is False


def test_sukces_ZERUJE_licznik() -> None:
    """Licznik jest KOLEJNYCH awarii, nie sumy."""
    zegar = _Zegar()
    rejestr = _rejestr(zegar, prog=2)
    rejestr.odnotuj_awarie("model")

    rejestr.odnotuj_sukces("model")
    rejestr.odnotuj_awarie("model")

    assert rejestr.odsuniety("model") is False, "sukces nie wyzerował licznika"


def test_uporzadkowanie_jest_STABILNE() -> None:
    """Wyłącznik odsuwa niedziałające modele, a nie przestawia polityki routingu."""
    zegar = _Zegar()
    rejestr = _rejestr(zegar, prog=1)
    rejestr.odnotuj_awarie("b")

    wynik = rejestr.uporzadkuj(["a", "b", "c", "d"])

    assert wynik == ["a", "c", "d", "b"]


def test_odsuniety_model_NIE_znika_z_listy() -> None:
    """Gdy padło WSZYSTKO, lista nie może się opróżnić.

    Wykluczanie zostawiłoby `NoModelAvailableError` — twardą odmowę zamiast próby, która
    mogłaby się powieść. Odsunięcie zachowuje własność „spróbuj mimo wszystko, na końcu".
    """
    zegar = _Zegar()
    rejestr = _rejestr(zegar, prog=1)
    for model in ("a", "b"):
        rejestr.odnotuj_awarie(model)

    assert rejestr.uporzadkuj(["a", "b"]) == ["a", "b"]


# --------------------------------------------------------------------------------------
# Router: zachowanie widoczne z zewnątrz
# --------------------------------------------------------------------------------------

_REJESTR_MODELI = {
    "glowny": {"backend": "mock", "model": "g", "tags": ["chat"], "fallback": ["zapasowy"]},
    "zapasowy": {"backend": "mock", "model": "z", "tags": ["chat"]},
}


def _config(**health: object) -> HusarzConfig:
    return HusarzConfig.model_validate(
        {
            "models": {"default": "glowny", "registry": _REJESTR_MODELI},
            "routing": {"health": health} if health else {},
        }
    )


class _Klient:
    """Klient, który zawodzi dla wskazanych modeli i liczy próby."""

    def __init__(self, model_id: str, padniete: set[str], proby: list[str]) -> None:
        self._model_id = model_id
        self._padniete = padniete
        self._proby = proby

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Zwraca odpowiedź albo zgłasza awarię backendu."""
        self._proby.append(self._model_id)
        if self._model_id in self._padniete:
            raise ModelBackendError(self._model_id, "limit czasu")
        return ChatResponse(content="ok", model=self._model_id)


def _router(config: HusarzConfig, padniete: set[str], proby: list[str]) -> ModelRouter:
    return ModelRouter(
        config, client_factory=lambda spec, model_id: _Klient(model_id, padniete, proby)
    )


def _zadanie() -> ChatRequest:
    return ChatRequest(messages=[ChatMessage(role="user", content="test")])


def test_padniety_model_przestaje_byc_PIERWSZY_po_progu() -> None:
    """Sedno całej zmiany: kolejne żądania nie płacą już limitu czasu na martwym modelu."""
    proby: list[str] = []
    router = _router(_config(failures_to_open=2, cooldown_seconds=30), {"glowny"}, proby)

    for _ in range(3):
        router.complete(_zadanie(), tags=["chat"])

    # Pierwsze dwa żądania próbują `glowny` (i spadają na `zapasowy`), trzecie już nie.
    assert proby == ["glowny", "zapasowy", "glowny", "zapasowy", "zapasowy"]


def test_wylaczony_mechanizm_zachowuje_stare_zachowanie() -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby `cooldown_seconds` nic nie robiło."""
    proby: list[str] = []
    router = _router(_config(cooldown_seconds=None), {"glowny"}, proby)

    for _ in range(3):
        router.complete(_zadanie(), tags=["chat"])

    assert proby == ["glowny", "zapasowy"] * 3


def test_pominiecie_z_powodu_ZADANIA_nie_jest_awaria() -> None:
    """Model pominięty przez bramkę wizyjną jest zdrowy — nie wolno go za to degradować.

    Żądanie z obrazem pomija model bez `vision`, ale to właściwość ŻĄDANIA, nie modelu.
    Gdyby liczyło się jako awaria, model tekstowy zostałby odsunięty i przy następnym,
    zwykłym żądaniu ruch poszedłby w gorsze miejsce.
    """
    config = HusarzConfig.model_validate(
        {
            "models": {
                "default": "tekstowy",
                "registry": {
                    "tekstowy": {"backend": "mock", "model": "t", "tags": ["chat"]},
                    "wizyjny": {"backend": "mock", "model": "w", "tags": ["chat"], "vision": True},
                },
            },
            "routing": {"health": {"failures_to_open": 1, "cooldown_seconds": 30}},
        }
    )
    proby: list[str] = []
    router = _router(config, set(), proby)

    z_obrazem = ChatRequest(
        messages=[ChatMessage(role="user", content="co to?", images=["ZGFuZQ=="])]
    )
    router.complete(z_obrazem, tags=["chat"])
    proby.clear()

    router.complete(_zadanie(), tags=["chat"])

    assert proby == ["tekstowy"], "model pominięty przez bramkę wizyjną został odsunięty"


def test_gdy_padlo_WSZYSTKO_nadal_probujemy() -> None:
    """Wyłącznik nie może zamienić awarii silników w odmowę bez próby."""
    proby: list[str] = []
    router = _router(
        _config(failures_to_open=1, cooldown_seconds=30), {"glowny", "zapasowy"}, proby
    )

    for _ in range(2):
        with pytest.raises(AllModelsFailedError):
            router.complete(_zadanie(), tags=["chat"])

    # Drugie żądanie też PRÓBUJE obu modeli, tylko w kolejności ustalonej wyłącznikiem.
    assert sorted(proby[2:]) == ["glowny", "zapasowy"]


def test_ROUTER_zeruje_licznik_po_udanej_odpowiedzi() -> None:
    """Zerowanie musi działać na ścieżce routera, nie tylko w samym rejestrze.

    Wykryła to kontrola nośności: test zerowania badał `RejestrZdrowia` wprost, więc usunięcie
    wywołania `odnotuj_sukces` z routera niczego nie czerwieniło. Skutek takiej wady byłby
    dotkliwy właśnie dla modelu DZIAŁAJĄCEGO z przerwami: pojedyncze potknięcia sumowałyby
    się przez cały czas życia procesu, aż model sprawny zostałby odsunięty.

    Scenariusz: awaria, sukces, awaria — przy progu 2 model NIE może zostać odsunięty, bo
    sukces w środku wyzerował licznik.
    """
    proby: list[str] = []
    padniete: set[str] = {"glowny"}
    config = _config(failures_to_open=2, cooldown_seconds=30)
    router = _router(config, padniete, proby)

    router.complete(_zadanie(), tags=["chat"])  # awaria -> licznik 1
    padniete.clear()
    router.complete(_zadanie(), tags=["chat"])  # sukces -> licznik 0
    padniete.add("glowny")
    router.complete(_zadanie(), tags=["chat"])  # awaria -> licznik 1 (nie 2)
    proby.clear()

    router.complete(_zadanie(), tags=["chat"])

    assert proby[0] == "glowny", "model został odsunięty mimo sukcesu w międzyczasie"
