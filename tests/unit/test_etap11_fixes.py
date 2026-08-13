"""Regresje z adwersaryjnego przeglądu Etapu 11 (obrazy w czacie):

- bramka ``vision`` egzekwowana na KAŻDYM kandydacie routera (obraz nie trafia do
  modelu tekstowego przez łańcuch fallbacków) — patrz ADR-0013;
- limit rozmiaru ciała odporny na ``Transfer-Encoding: chunked`` (bez ``Content-Length``);
- obrazy wiązane z ostatnią wiadomością ``user`` (nie ślepo z ``messages[-1]``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.api.app import BodySizeLimitMiddleware
from husarz.config import load_config
from husarz.config.schema import ModelSpec
from husarz.router import (
    AllModelsFailedError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    ModelBackendError,
    ModelRouter,
)
from husarz.security import AuditLog

pytestmark = pytest.mark.unit

# 1x1 przezroczysty PNG (poprawne magic-bytes) w base64.
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="  # noqa: E501


# --- Bramka vision na łańcuchu fallbacków (findingi #1/#3/#5) ----------------

_VISION_REGISTRY = {
    "vis": {
        "backend": "mock",
        "model": "llava",
        "tags": ["vision"],
        "vision": True,
        "fallback": ["txt"],
    },
    "txt": {"backend": "mock", "model": "txt", "tags": ["chat"]},
}


class RecordingClient:
    """Klient testowy zapisujący wywołania ``chat`` (do weryfikacji, kto realnie dostał żądanie)."""

    def __init__(self, model_id: str, calls: list[str], *, error: Exception | None = None) -> None:
        self.model_id = model_id
        self._calls = calls
        self._error = error

    def chat(self, request: ChatRequest) -> ChatResponse:
        self._calls.append(self.model_id)
        if self._error is not None:
            raise self._error
        return ChatResponse(model=self.model_id, content=f"from {self.model_id}")


def _image_request() -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage("user", "co to?", images=[ImagePart("image/png", "AAA")])]
    )


def test_images_skip_nonvision_fallback(make_config) -> None:  # noqa: ANN001
    """Awaria modelu wizyjnego NIE może przekierować obrazu do modelu tekstowego."""
    config = make_config(registry=_VISION_REGISTRY, default="vis")
    calls: list[str] = []

    def factory(spec: ModelSpec, model_id: str) -> RecordingClient:
        error = ModelBackendError(model_id, "llava padło") if model_id == "vis" else None
        return RecordingClient(model_id, calls, error=error)

    router = ModelRouter(config, client_factory=factory)
    with pytest.raises(AllModelsFailedError):
        router.complete(_image_request())
    assert calls == [
        "vis"
    ]  # 'txt' (vision:false) pominięty — obraz nie dotarł do modelu tekstowego


def test_no_images_still_falls_back_to_text(make_config) -> None:  # noqa: ANN001
    """Brak obrazów → łańcuch fallbacków działa normalnie (bramka vision nie dotyczy)."""
    config = make_config(registry=_VISION_REGISTRY, default="vis")
    calls: list[str] = []

    def factory(spec: ModelSpec, model_id: str) -> RecordingClient:
        error = ModelBackendError(model_id, "padło") if model_id == "vis" else None
        return RecordingClient(model_id, calls, error=error)

    router = ModelRouter(config, client_factory=factory)
    resp = router.complete(ChatRequest(messages=[ChatMessage("user", "cześć")]))
    assert resp.model == "txt"
    assert calls == ["vis", "txt"]


def test_images_use_vision_fallback(make_config) -> None:  # noqa: ANN001
    """Obraz może przejść na fallback, o ile fallback też jest wizyjny (vision:true)."""
    registry = {
        "vis1": {
            "backend": "mock",
            "model": "llava",
            "tags": ["vision"],
            "vision": True,
            "fallback": ["vis2"],
        },
        "vis2": {
            "backend": "mock",
            "model": "qwen2-vl",
            "tags": ["vision"],
            "vision": True,
        },
    }
    config = make_config(registry=registry, default="vis1")
    calls: list[str] = []

    def factory(spec: ModelSpec, model_id: str) -> RecordingClient:
        error = ModelBackendError(model_id, "padło") if model_id == "vis1" else None
        return RecordingClient(model_id, calls, error=error)

    router = ModelRouter(config, client_factory=factory)
    resp = router.complete(_image_request())
    assert resp.model == "vis2"  # oba wizyjne → fallback dozwolony
    assert calls == ["vis1", "vis2"]


# --- Obrazy wiązane z ostatnią wiadomością user (finding #4) -----------------


class CapturingRouter:
    """Router zapisujący ostatnie żądanie (do inspekcji, gdzie trafiły obrazy)."""

    def __init__(self) -> None:
        self.last: ChatRequest | None = None

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001, ANN201
        self.last = request
        return ChatResponse(model="husarz-vision", content="widzę")


def _vision_client(repo_config_dir: Path, router: CapturingRouter) -> TestClient:
    config = load_config(repo_config_dir, runtime_overrides={"models": {"chat": "husarz-vision"}})
    app = create_app(config, config_dir=repo_config_dir, audit=AuditLog(), router=router)
    return TestClient(app)


def test_image_binds_to_last_user_not_assistant(repo_config_dir: Path) -> None:
    """Gdy ostatnią wiadomością jest 'assistant', obraz trafia do wiadomości 'user'."""
    router = CapturingRouter()
    client = _vision_client(repo_config_dir, router)
    body = {
        "messages": [
            {"role": "user", "content": "co to?"},
            {"role": "assistant", "content": "chwila"},
        ],
        "images": [{"name": "a.png", "data": PNG_B64}],
    }
    assert client.post("/api/chat", json=body).status_code == 200
    assert router.last is not None
    user_msgs = [m for m in router.last.messages if m.role == "user"]
    assert user_msgs[0].images and user_msgs[0].images[0].mime == "image/png"
    assert not router.last.messages[-1].images  # 'assistant' (ostatnia) bez obrazu


def test_image_rejected_without_user_message(repo_config_dir: Path) -> None:
    """Konwersacja bez wiadomości 'user' + obraz → 400 (obraz nie ma gdzie osiąść)."""
    router = CapturingRouter()
    client = _vision_client(repo_config_dir, router)
    body = {
        "messages": [{"role": "system", "content": "jesteś asystentem"}],
        "images": [{"name": "a.png", "data": PNG_B64}],
    }
    r = client.post("/api/chat", json=body)
    assert r.status_code == 400
    assert "user" in r.json()["detail"]


# --- Limit ciała odporny na chunked (finding #2) -----------------------------


async def _echo_app(scope, receive, send):  # noqa: ANN001, ANN202
    """Minimalna aplikacja ASGI: czyta CAŁE ciało, potem odpowiada 200."""
    while True:
        message = await receive()
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _drive(middleware: BodySizeLimitMiddleware, scope: dict, chunks: list[bytes]) -> list[dict]:
    """Uruchamia middleware z fałszywym ``receive`` (chunki ciała) i zbiera wysłane wiadomości."""
    sent: list[dict] = []
    remaining = list(chunks)

    async def receive() -> dict:
        if remaining:
            return {"type": "http.request", "body": remaining.pop(0), "more_body": bool(remaining)}
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    return sent


def test_body_limit_blocks_chunked_over_limit() -> None:
    """Żądanie bez Content-Length (chunked) przekraczające limit → 413 (nie omija kontroli)."""
    mw = BodySizeLimitMiddleware(_echo_app, max_bytes=10)
    scope = {"type": "http", "headers": []}  # brak content-length
    sent = _drive(mw, scope, [b"x" * 6, b"x" * 6])  # 12 B > 10 B
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 413


def test_body_limit_allows_under_limit() -> None:
    """Ciało chunked poniżej limitu → przechodzi do aplikacji (200)."""
    mw = BodySizeLimitMiddleware(_echo_app, max_bytes=100)
    scope = {"type": "http", "headers": []}
    sent = _drive(mw, scope, [b"x" * 6, b"x" * 6])  # 12 B < 100 B
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 200


def test_body_limit_fast_path_content_length() -> None:
    """Zadeklarowany Content-Length ponad limit → 413 bez czytania ciała."""
    mw = BodySizeLimitMiddleware(_echo_app, max_bytes=10)
    scope = {"type": "http", "headers": [(b"content-length", b"999")]}
    sent = _drive(mw, scope, [b""])
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 413


def test_body_limit_single_huge_chunk_not_buffered() -> None:
    """Pojedynczy chunk większy niż limit → 413, bez doklejania go do bufora (sufit pamięci)."""
    captured: list[int] = []

    async def spy_app(scope, receive, send):  # noqa: ANN001, ANN202
        message = await receive()
        captured.append(len(message.get("body", b"")))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BodySizeLimitMiddleware(spy_app, max_bytes=10)
    scope = {"type": "http", "headers": []}
    sent = _drive(mw, scope, [b"x" * 1000])  # jeden ogromny chunk
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert starts and starts[0]["status"] == 413
    assert captured == []  # aplikacja nie została wywołana — chunk nie wszedł do pamięci


def test_chunked_body_over_limit_returns_413_end_to_end(repo_config_dir: Path) -> None:
    """Realny stos FastAPI: żądanie chunked (bez Content-Length) ponad limit → czyste 413."""
    config = load_config(repo_config_dir, runtime_overrides={"chat": {"max_request_bytes": 1024}})
    client = TestClient(create_app(config, audit=AuditLog()))

    def gen() -> object:
        for _ in range(50):
            yield b"x" * 100  # ~5 KB, httpx wyśle Transfer-Encoding: chunked

    r = client.post("/api/chat", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413  # nie 400 „error parsing body", nie OOM
    assert "limit" in r.json()["detail"].lower()
