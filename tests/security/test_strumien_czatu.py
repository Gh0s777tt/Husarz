"""Strumieniowanie odpowiedzi czatu przez SSE (`POST /api/chat/stream`, Etap 18m).

**Dlaczego SSE, a nie WebSocket — wbrew własnemu planowi rozwoju.** Plan zapowiadał
WebSocket; przy realizacji okazał się złym narzędziem z trzech niezależnych powodów.
Strumień odpowiedzi jest JEDNOKIERUNKOWY, czyli dokładnie tym, do czego SSE służy.
WebSocket wymagałby SZÓSTEJ zależności runtime w rdzeniu, który ma ich świadomie pięć.
I najważniejsze: WebSocket **nie podlega CORS**, więc trzeba by osobno zaprojektować
kontrolę `Origin` i uwierzytelnianie — przeglądarka nie wyśle nagłówka `Authorization`
przy otwarciu gniazda. SSE idzie zwykłym POST-em, więc dziedziczy CAŁE uwierzytelnianie
i całą politykę pochodzenia, których nie trzeba wymyślać od nowa.

Najważniejszy test w tym pliku to `test_strumien_NIE_jest_ucinany_przez_middleware`.
Opisuje wadę, która kosztowała najwięcej czasu w tym etapie i która wróciłaby niezauważona:
`BodySizeLimitMiddleware` po odtworzeniu ciała żądania zwracał sfabrykowane
`http.disconnect`. Było to nieszkodliwe, dopóki każdy endpoint czytał ciało w całości —
i przestało być z pierwszą odpowiedzią strumieniową, którą Starlette przerywał, zanim
cokolwiek wyszło. Objaw: status 200, poprawne nagłówki, PUSTE ciało, żadnego błędu.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.api.app import BodySizeLimitMiddleware
from husarz.config.schema import HusarzConfig
from husarz.router.errors import AllModelsFailedError, NoModelAvailableError
from husarz.router.types import ChatResponse
from husarz.security import AuditLog

pytestmark = pytest.mark.security


def _config() -> HusarzConfig:
    return HusarzConfig.model_validate(
        {
            "models": {
                "default": "m",
                "chat": "m",
                "registry": {"m": {"backend": "mock", "model": "testowy"}},
            },
            # Agent-orkiestrator jest wymagany do złożenia aplikacji, choć te testy
            # dotyczą wyłącznie czatu bezpośredniego.
            "agents": {"husarz": {"name": "husarz", "prompt_file": "husarz.md"}},
        }
    )


class _Router:
    """Router testowy: strumieniuje ustalone fragmenty albo zawodzi."""

    def __init__(self, fragmenty: list[str] | None = None, blad: Exception | None = None) -> None:
        self._fragmenty = fragmenty or []
        self._blad = blad
        self.otrzymane_modele: list[str | None] = []

    def complete(self, request, **kwargs):  # noqa: ANN001, ANN201
        """Ścieżka nierostrumieniowa — tu nieużywana."""
        return ChatResponse(model="m", content="".join(self._fragmenty))

    def complete_stream(self, request, *, model=None, **kwargs) -> Iterator[str]:  # noqa: ANN001
        """Oddaje fragmenty albo zgłasza zaplanowany błąd."""
        self.otrzymane_modele.append(model)
        if self._blad is not None:
            raise self._blad
        yield from self._fragmenty


@pytest.fixture
def prompty(tmp_path: Path) -> Path:
    """Katalog promptów z jedynym potrzebnym plikiem."""
    (tmp_path / "husarz.md").write_text("Jesteś Husarz.", encoding="utf-8")
    return tmp_path


def _klient(router: _Router, prompty: Path, audit: AuditLog | None = None) -> TestClient:
    app = create_app(
        _config(),
        audit=audit or AuditLog(),
        prompts_dir=prompty,
        router_factory=lambda cfg: router,
    )
    return TestClient(app, raise_server_exceptions=True)


def _zdarzenia(tresc: str) -> list[dict]:
    """Parsuje odpowiedź SSE na listę ładunków."""
    return [
        json.loads(linia[len("data: ") :])
        for linia in tresc.splitlines()
        if linia.startswith("data: ")
    ]


# --------------------------------------------------------------------------------------
# Regresja middleware — najważniejszy test w tym pliku
# --------------------------------------------------------------------------------------


def test_strumien_NIE_jest_ucinany_przez_middleware() -> None:
    """Odpowiedź strumieniowa musi dojść w CAŁOŚCI mimo buforowania ciała żądania.

    `BodySizeLimitMiddleware` buforuje ciało i odtwarza je aplikacji. Po odtworzeniu
    zwracał sfabrykowane `http.disconnect` — a `StreamingResponse` nasłuchuje rozłączenia
    RÓWNOLEGLE z wysyłaniem treści i przerywa strumień, gdy je zobaczy. Skutkiem była
    odpowiedź pusta ze statusem 200 i poprawnymi nagłówkami, bez żadnego błędu.

    Test jest celowo minimalny (własna aplikacja, nie Husarz), bo bada middleware, a nie
    czat: gdyby korzystał z pełnej aplikacji, jego czerwień można by przypisać czemu innemu.
    """
    app = FastAPI()

    @app.post("/strumien")
    def strumien() -> StreamingResponse:
        def gen() -> Iterator[str]:
            for i in range(3):
                yield f"data: {i}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=1024)
    klient = TestClient(app, raise_server_exceptions=True)

    odpowiedz = klient.post("/strumien", json={"a": 1})

    assert odpowiedz.status_code == 200
    assert odpowiedz.text == "data: 0\n\ndata: 1\n\ndata: 2\n\n"


def _aplikacja_z_limitem(max_bytes: int) -> TestClient:
    """Minimalna aplikacja z kontrolą rozmiaru ciała."""
    app = FastAPI()

    @app.post("/echo")
    def echo(dane: dict) -> dict:
        return dane

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)
    return TestClient(app, raise_server_exceptions=True)


def test_middleware_NADAL_odrzuca_za_duze_ciało_z_naglowkiem() -> None:
    """Szybka ścieżka: zadeklarowany `Content-Length` ponad limit."""
    assert _aplikacja_z_limitem(64).post("/echo", json={"a": "x" * 500}).status_code == 413


def test_middleware_NADAL_odrzuca_ciało_CHUNKED_bez_Content_Length() -> None:
    """Druga ścieżka kontroli — ta, którą omija `Transfer-Encoding: chunked`.

    Pierwsza wersja tego pliku sprawdzała wyłącznie wariant z nagłówkiem, więc mutacja
    wyłączająca liczenie bajtów w pętli nie czerwieniła niczego: żądanie odpadało wcześniej,
    na szybkiej ścieżce. Wykryła to kontrola nośności. Bez tego testu poprawka strumieniowa
    mogłaby po cichu rozbroić obronę przed żądaniem bez `Content-Length`.
    """

    def kawalki() -> Iterator[bytes]:
        for _ in range(20):
            yield b'{"a": "' + b"x" * 100 + b'"}'

    # Ciało podane iteratorem = httpx wysyła chunked, BEZ nagłówka Content-Length.
    odpowiedz = _aplikacja_z_limitem(64).post("/echo", content=kawalki())

    assert odpowiedz.status_code == 413


# --------------------------------------------------------------------------------------
# Endpoint strumieniowy
# --------------------------------------------------------------------------------------


def test_fragmenty_docieraja_jako_zdarzenia_delta(prompty: Path) -> None:
    """Ścieżka pogodna: każdy fragment osobnym zdarzeniem, na końcu `done`."""
    odpowiedz = _klient(prompty=prompty, router=_Router(["Ala ", "ma ", "kota"])).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    zdarzenia = _zdarzenia(odpowiedz.text)
    assert [z["delta"] for z in zdarzenia if "delta" in z] == ["Ala ", "ma ", "kota"]
    assert zdarzenia[-1]["done"] is True


def test_nowa_linia_w_odpowiedzi_NIE_rozbija_zdarzenia(prompty: Path) -> None:
    """Znak nowej linii w treści rozbiłby ramkę SSE na dwie — stąd ucieczka na ASCII."""
    odpowiedz = _klient(prompty=prompty, router=_Router(["pierwsza\ndruga\n\ntrzecia"])).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    zdarzenia = _zdarzenia(odpowiedz.text)
    assert zdarzenia[0]["delta"] == "pierwsza\ndruga\n\ntrzecia"
    assert zdarzenia[-1]["done"] is True


def test_awaria_modelu_daje_zdarzenie_ERROR_a_nie_ciszę(prompty: Path) -> None:
    """Po rozpoczęciu strumienia statusu HTTP nie da się już zmienić — musi być zdarzenie."""
    router = _Router(blad=AllModelsFailedError([("m", "silnik nie odpowiada")]))

    odpowiedz = _klient(router, prompty).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    assert odpowiedz.status_code == 200
    zdarzenia = _zdarzenia(odpowiedz.text)
    assert "error" in zdarzenia[-1]
    assert "silnik nie odpowiada" in zdarzenia[-1]["error"]


def test_brak_modelu_jest_nazwany_wprost(prompty: Path) -> None:
    """Rozróżnienie przyczyn: „nie ma modelu" to co innego niż „model padł"."""
    odpowiedz = _klient(prompty=prompty, router=_Router(blad=NoModelAvailableError("brak"))).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    assert "niedostępny" in _zdarzenia(odpowiedz.text)[-1]["error"]


def test_bledne_zadanie_daje_kod_HTTP_a_nie_zdarzenie(prompty: Path) -> None:
    """Bramki działają PRZED strumieniem, więc błąd żądania nadal mapuje się na 400.

    To jest granica projektu: co da się sprawdzić przed wysłaniem statusu, sprawdzamy tam,
    bo kod HTTP niesie więcej niż zdarzenie w treści.
    """
    odpowiedz = _klient(prompty=prompty, router=_Router(["x"])).post(
        "/api/chat/stream",
        json={
            "messages": [{"role": "user", "content": "x"}],
            "images": [{"name": "a.png", "data": "AAAA"}],
        },
    )

    assert odpowiedz.status_code == 400
    assert "nie obsługuje obrazów" in odpowiedz.json()["detail"]


def test_strumien_jest_AUDYTOWANY(prompty: Path) -> None:
    """Nowy endpoint nie może być drogą do czatu z pominięciem dziennika."""
    audyt = AuditLog()

    _klient(prompty=prompty, router=_Router(["x"]), audit=audyt).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    akcje = [w.action for w in audyt.entries]
    assert "chat.stream" in akcje


def test_naglowki_wykluczaja_buforowanie(prompty: Path) -> None:
    """Pośrednik buforujący odpowiedź zniweczyłby cały sens strumienia."""
    odpowiedz = _klient(prompty=prompty, router=_Router(["x"])).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    assert odpowiedz.headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in odpowiedz.headers["cache-control"]


def test_zdarzenia_sa_ROZDZIELONE_pusta_linia(prompty: Path) -> None:
    """Format przewodowy SSE: pusta linia KOŃCZY zdarzenie.

    Mój parser testowy wyławia linie `data:` i jest przez to łagodniejszy niż prawdziwy
    klient — wykryła to kontrola nośności (usunięcie separatora niczego nie zaczerwieniło).
    Bez pustej linii `EventSource` w przeglądarce sklei kolejne linie `data:` w JEDNO
    zdarzenie o połączonej treści, więc odpowiedź dotrze zniekształcona, a nie wcale.
    """
    odpowiedz = _klient(prompty=prompty, router=_Router(["a", "b"])).post(
        "/api/chat/stream", json={"messages": [{"role": "user", "content": "x"}]}
    )

    # Każde zdarzenie to dokładnie jedna linia `data:` zakończona PUSTĄ linią.
    ramki = odpowiedz.text.split("\n\n")
    assert ramki[-1] == "", "ostatnie zdarzenie nie zostało zakończone separatorem"
    for ramka in ramki[:-1]:
        assert ramka.startswith("data: "), ramka
        assert "\n" not in ramka, "zdarzenie rozbite na wiele linii"
    assert len(ramki) - 1 == 3, "oczekiwano dwóch fragmentów i zdarzenia `done`"
