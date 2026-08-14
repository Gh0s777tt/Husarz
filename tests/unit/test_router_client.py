"""Testy klientów modeli (OpenAI-compat + mock) i fabryki build_client.

Wszystkie testy używają wstrzykniętego transportu — brak połączeń sieciowych.
"""

from __future__ import annotations

from typing import Any

import pytest

from husarz.config.schema import ModelSpec
from husarz.config.secrets import EnvSecretsProvider
from husarz.router import (
    ChatMessage,
    ChatRequest,
    MockClient,
    ModelBackendError,
    OpenAICompatClient,
    TransportError,
    build_client,
)

pytestmark = pytest.mark.unit

_OPENAI_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": "Cześć"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


class CapturingTransport:
    """Transport testowy: zapisuje wywołania, zwraca kanoniczną odpowiedź lub rzuca błąd."""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self._response = response if response is not None else _OPENAI_RESPONSE
        self._error = error

    def __call__(self, target, headers, payload, timeout):  # noqa: ANN001 - sygnatura protokołu
        self.calls.append(
            {
                "url": target.connect_url,
                "target": target,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        if self._error is not None:
            raise self._error
        return self._response


def _spec(**overrides: Any) -> ModelSpec:
    base: dict[str, Any] = {
        "backend": "openai_compat",
        "model": "glm-5.2",
        "endpoint": "http://localhost:8000/v1",
        "params": {"temperature": 0.3},
    }
    base.update(overrides)
    return ModelSpec(**base)


def test_openai_client_builds_payload_and_parses() -> None:
    transport = CapturingTransport()
    client = OpenAICompatClient(_spec(), "glm-main", api_key=None, transport=transport)
    resp = client.chat(ChatRequest(messages=[ChatMessage("user", "Cześć")], max_tokens=128))

    call = transport.calls[0]
    assert call["url"] == "http://localhost:8000/v1/chat/completions"
    assert call["payload"]["model"] == "glm-5.2"
    assert call["payload"]["messages"] == [{"role": "user", "content": "Cześć"}]
    assert call["payload"]["temperature"] == 0.3  # z params modelu
    assert call["payload"]["max_tokens"] == 128  # z żądania
    assert "Authorization" not in call["headers"]

    assert resp.model == "glm-main"
    assert resp.content == "Cześć"
    assert resp.finish_reason == "stop"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 7


def test_openai_client_transport_error_becomes_backend_error() -> None:
    transport = CapturingTransport(error=TransportError("sieć padła"))
    client = OpenAICompatClient(_spec(), "glm-main", api_key=None, transport=transport)
    with pytest.raises(ModelBackendError, match="glm-main"):
        client.chat(ChatRequest(messages=[ChatMessage("user", "hej")]))


def test_openai_client_malformed_response() -> None:
    transport = CapturingTransport(response={"nonsens": True})
    client = OpenAICompatClient(_spec(), "glm-main", api_key=None, transport=transport)
    with pytest.raises(ModelBackendError, match="Nieprawidłowa odpowiedź"):
        client.chat(ChatRequest(messages=[ChatMessage("user", "hej")]))


def test_openai_client_requires_endpoint() -> None:
    transport = CapturingTransport()
    client = OpenAICompatClient(_spec(endpoint=None), "glm-main", api_key=None, transport=transport)
    with pytest.raises(ModelBackendError, match="endpointu"):
        client.chat(ChatRequest(messages=[ChatMessage("user", "hej")]))


def test_mock_client_is_deterministic_and_offline() -> None:
    client = MockClient("m1", ModelSpec(backend="mock", model="test-model"))
    resp = client.chat(ChatRequest(messages=[ChatMessage("user", "pytanie")]))
    assert resp.model == "m1"
    assert resp.content.startswith("[mock:test-model]")


def test_build_client_mock_backend() -> None:
    client = build_client(ModelSpec(backend="mock", model="x"), "m1")
    assert isinstance(client, MockClient)


def test_build_client_resolves_api_key_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLM_KEY", "secret123")
    transport = CapturingTransport()
    spec = _spec(api_key_ref="env:GLM_KEY")
    client = build_client(spec, "glm-main", secrets=EnvSecretsProvider(), transport=transport)
    client.chat(ChatRequest(messages=[ChatMessage("user", "hej")]))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret123"


def test_build_client_without_api_key_ref_has_no_auth_header() -> None:
    transport = CapturingTransport()
    client = build_client(_spec(), "glm-main", transport=transport)
    client.chat(ChatRequest(messages=[ChatMessage("user", "hej")]))
    assert "Authorization" not in transport.calls[0]["headers"]
