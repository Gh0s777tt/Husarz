"""Klienci modeli — warstwa OpenAI-compat (vLLM/Ollama/SGLang) + mock.

Transport (HTTP) jest oddzielony od logiki klienta, dzięki czemu testy wstrzykują
własny transport i nie wykonują żadnych połączeń sieciowych (zgodnie z deny-all
egress). Klucz API pochodzi z referencji do sekretu (``api_key_ref``) rozwiązywanej
przez dostawcę sekretów — nigdy nie jest wpisywany na stałe.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from husarz.config.schema import ModelBackend, ModelSpec
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.router.errors import ModelBackendError, TransportError
from husarz.router.types import ChatRequest, ChatResponse, Usage

DEFAULT_TIMEOUT_SECONDS = 60


@runtime_checkable
class Transport(Protocol):
    """Warstwa transportu HTTP. Zwraca sparsowany JSON lub rzuca ``TransportError``."""

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> dict[str, Any]: ...


@runtime_checkable
class ModelClient(Protocol):
    """Klient pojedynczego modelu."""

    model_id: str

    def chat(self, request: ChatRequest) -> ChatResponse: ...


class HttpxTransport:
    """Transport oparty o httpx. Import leniwy — biblioteka wymagana dopiero przy wywołaniu."""

    def __init__(self, timeout: int | None = None) -> None:
        self._timeout = timeout

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - zależność deklarowana w pyproject
            raise TransportError(
                "Pakiet 'httpx' nie jest zainstalowany — wymagany przez HttpxTransport."
            ) from exc
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return data
        except httpx.HTTPError as exc:
            raise TransportError(f"Błąd HTTP przy {url}: {exc}") from exc


def _parse_openai_response(data: dict[str, Any], model_id: str) -> ChatResponse:
    """Parsuje odpowiedź w formacie OpenAI chat/completions."""
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelBackendError(model_id, f"Nieprawidłowa odpowiedź backendu: {exc}") from exc

    usage: Usage | None = None
    raw_usage = data.get("usage")
    if isinstance(raw_usage, dict):
        usage = Usage(
            prompt_tokens=raw_usage.get("prompt_tokens"),
            completion_tokens=raw_usage.get("completion_tokens"),
            total_tokens=raw_usage.get("total_tokens"),
        )
    return ChatResponse(
        model=model_id,
        content=content,
        finish_reason=choice.get("finish_reason"),
        usage=usage,
        raw=data,
    )


class OpenAICompatClient:
    """Klient dowolnego backendu zgodnego z OpenAI (vLLM/Ollama/SGLang)."""

    def __init__(
        self,
        spec: ModelSpec,
        model_id: str,
        *,
        api_key: str | None,
        transport: Transport,
    ) -> None:
        self.spec = spec
        self.model_id = model_id
        self._api_key = api_key
        self._transport = transport
        self._timeout = spec.request_timeout_seconds or DEFAULT_TIMEOUT_SECONDS

    def chat(self, request: ChatRequest) -> ChatResponse:
        if self.spec.endpoint is None:
            raise ModelBackendError(self.model_id, "Model nie ma skonfigurowanego endpointu.")

        url = self.spec.endpoint.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self.spec.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        # Parametry: domyślne z modelu, potem nadpisania z żądania.
        merged: dict[str, Any] = dict(self.spec.params)
        if request.temperature is not None:
            merged["temperature"] = request.temperature
        if request.max_tokens is not None:
            merged["max_tokens"] = request.max_tokens
        if request.stop:
            merged["stop"] = request.stop
        merged.update(request.extra)
        payload.update(merged)

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            data = self._transport(url, headers, payload, self._timeout)
        except TransportError as exc:
            raise ModelBackendError(self.model_id, str(exc)) from exc
        return _parse_openai_response(data, self.model_id)


class MockClient:
    """Klient testowy/deweloperski — nie łączy się z siecią, zwraca deterministyczną odpowiedź."""

    def __init__(self, model_id: str, spec: ModelSpec) -> None:
        self.model_id = model_id
        self.spec = spec

    def chat(self, request: ChatRequest) -> ChatResponse:
        last = request.messages[-1].content if request.messages else ""
        return ChatResponse(
            model=self.model_id,
            content=f"[mock:{self.spec.model}] {last}",
            finish_reason="stop",
        )


def build_client(
    spec: ModelSpec,
    model_id: str,
    *,
    secrets: SecretsProvider | None = None,
    transport: Transport | None = None,
) -> ModelClient:
    """Buduje klienta dla modelu.

    Backend ``mock`` daje ``MockClient`` (bez sieci). Pozostałe backendy dostają
    ``OpenAICompatClient``; klucz API rozwiązywany jest z ``spec.api_key_ref``
    przez dostawcę sekretów.
    """
    if spec.backend is ModelBackend.MOCK:
        return MockClient(model_id, spec)

    api_key: str | None = None
    if spec.api_key_ref:
        provider = secrets or NullSecretsProvider()
        api_key = provider.resolve(spec.api_key_ref)

    active_transport = transport or HttpxTransport(timeout=spec.request_timeout_seconds)
    return OpenAICompatClient(spec, model_id, api_key=api_key, transport=active_transport)
