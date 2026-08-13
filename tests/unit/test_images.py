"""Testy obrazów w czacie: sniff typu, limity, multimodal payload klienta, bramka vision."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.attachments import AttachmentError, sanitize_images
from husarz.attachments import _sniff_image_mime as sniff
from husarz.config import load_config
from husarz.config.schema import ImagesConfig, ModelSpec
from husarz.router import ChatResponse
from husarz.router.client import OpenAICompatClient, _message_payload
from husarz.router.types import ChatMessage, ChatRequest, ImagePart
from husarz.security import AuditLog

pytestmark = pytest.mark.unit

# 1x1 przezroczysty PNG (poprawne magic-bytes) w base64.
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="  # noqa: E501


# --- Sniff typu z magic-bytes -----------------------------------------------


def test_sniff_recognizes_formats() -> None:
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert sniff(b"GIF89a....") == "image/gif"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"not an image at all") is None


# --- Sanityzacja obrazów ----------------------------------------------------


def test_sanitize_valid_png() -> None:
    imgs = sanitize_images([("a.png", PNG_B64)], ImagesConfig())
    assert imgs[0].mime == "image/png"
    assert imgs[0].name == "a.png"


def test_reject_non_image() -> None:
    import base64

    text_b64 = base64.b64encode(b"to nie obraz").decode()
    with pytest.raises(AttachmentError):
        sanitize_images([("x.txt", text_b64)], ImagesConfig())


def test_reject_bad_base64() -> None:
    with pytest.raises(AttachmentError):
        sanitize_images([("x", "!!!nie-base64!!!")], ImagesConfig())


def test_reject_too_many_images() -> None:
    with pytest.raises(AttachmentError):
        sanitize_images([("a", PNG_B64), ("b", PNG_B64)], ImagesConfig(max_images=1))


def test_reject_too_large_image() -> None:
    with pytest.raises(AttachmentError):
        sanitize_images([("a", PNG_B64)], ImagesConfig(max_bytes_per_image=10))


def test_reject_when_disabled() -> None:
    with pytest.raises(AttachmentError):
        sanitize_images([("a", PNG_B64)], ImagesConfig(enabled=False))


# --- Multimodal payload klienta ---------------------------------------------


def test_message_payload_plain_text() -> None:
    msg = ChatMessage(role="user", content="cześć")
    assert _message_payload(msg) == {"role": "user", "content": "cześć"}


def test_message_payload_with_images_is_multimodal() -> None:
    msg = ChatMessage(
        role="user", content="co to?", images=[ImagePart(mime="image/png", data_b64="AAA")]
    )
    payload = _message_payload(msg)
    content = payload["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "co to?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAA"


def test_client_sends_multimodal_payload() -> None:
    captured: dict[str, object] = {}

    def transport(url, headers, payload, timeout):  # noqa: ANN001
        captured.update(payload)
        return {"choices": [{"message": {"content": "widzę"}}]}

    spec = ModelSpec(
        backend="ollama", model="llava", endpoint="http://localhost:11434/v1", vision=True
    )
    client = OpenAICompatClient(spec, "husarz-vision", api_key=None, transport=transport)
    req = ChatRequest(
        messages=[ChatMessage(role="user", content="opisz", images=[ImagePart("image/png", "AAA")])]
    )
    client.chat(req)
    assert isinstance(captured["messages"][0]["content"], list)  # type: ignore[index]


# --- API: bramka vision -----------------------------------------------------


class CapturingRouter:
    def __init__(self) -> None:
        self.last: ChatRequest | None = None

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        self.last = request
        return ChatResponse(model="husarz-vision", content="widzę obraz")


def _vision_client(repo_config_dir: Path, router: CapturingRouter) -> TestClient:
    config = load_config(repo_config_dir, runtime_overrides={"models": {"chat": "husarz-vision"}})
    app = create_app(config, config_dir=repo_config_dir, audit=AuditLog(), router=router)
    return TestClient(app)


def test_chat_with_image_on_vision_model(repo_config_dir: Path) -> None:
    router = CapturingRouter()
    client = _vision_client(repo_config_dir, router)
    body = {
        "messages": [{"role": "user", "content": "co to?"}],
        "images": [{"name": "a.png", "data": PNG_B64}],
    }
    assert client.post("/api/chat", json=body).status_code == 200
    assert router.last is not None
    assert router.last.messages[-1].images[0].mime == "image/png"  # obraz dotarł do routera


def test_chat_image_rejected_on_non_vision_model(repo_config_dir: Path) -> None:
    # Domyślny model czatu (husarz-local, vision:false) → obraz odrzucony 400.
    config = load_config(repo_config_dir)
    client = TestClient(create_app(config, audit=AuditLog(), router=CapturingRouter()))
    body = {
        "messages": [{"role": "user", "content": "x"}],
        "images": [{"name": "a.png", "data": PNG_B64}],
    }
    assert client.post("/api/chat", json=body).status_code == 400


def test_chat_rejects_non_image_bytes(repo_config_dir: Path) -> None:
    import base64

    router = CapturingRouter()
    client = _vision_client(repo_config_dir, router)
    bad = base64.b64encode(b"definitely not an image").decode()
    body = {
        "messages": [{"role": "user", "content": "x"}],
        "images": [{"name": "a.png", "data": bad}],
    }
    assert client.post("/api/chat", json=body).status_code == 400
