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
from husarz.router.egress import EgressError
from husarz.router.errors import ModelBackendError, TransportError
from husarz.router.types import ChatMessage, ChatRequest, ChatResponse, Usage
from husarz.ssrf import HostResolver, PinnedTarget, build_pinned_target, default_resolve

DEFAULT_TIMEOUT_SECONDS = 60


@runtime_checkable
class Transport(Protocol):
    """Warstwa transportu HTTP. Zwraca sparsowany JSON lub rzuca ``TransportError``.

    Przyjmuje ``PinnedTarget`` (a NIE goły URL) celowo: pin jest częścią kontraktu, więc
    implementacja nie może go pominąć i rozwiązać nazwy ponownie (okno TOCTOU — ADR-0020).
    """

    def __call__(
        self,
        target: PinnedTarget,
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
    """Transport oparty o httpx. Import leniwy — biblioteka wymagana dopiero przy wywołaniu.

    Łączy się z ``target.connect_url`` (literał IP dla nazw domenowych), a nagłówek ``Host``
    i SNI/weryfikacja certyfikatu idą po ORYGINALNEJ nazwie — pin nie degraduje TLS.
    ``trust_env=False``: zmienne ``HTTP(S)_PROXY`` ze środowiska nie mogą przekierować
    przypiętego połączenia do modelu (a więc i klucza API) przez cudzy serwer.
    ``follow_redirects=False`` — przekierowanie omijałoby walidację i pin.

    Komunikat błędu jest GENERYCZNY (bez URL-a i wnętrzności httpx): trafia do
    ``ModelBackendError`` i dalej do odpowiedzi API/audytu.
    """

    def __init__(self, timeout: int | None = None) -> None:
        self._timeout = timeout

    def __call__(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int | None = None,
    ) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - zależność deklarowana w pyproject
            raise TransportError(
                "Pakiet 'httpx' nie jest zainstalowany — wymagany przez HttpxTransport."
            ) from exc
        effective_timeout = (
            timeout if timeout is not None else (self._timeout or DEFAULT_TIMEOUT_SECONDS)
        )
        sent_headers = dict(headers)
        if target.host_header is not None:
            sent_headers["Host"] = target.host_header
        extensions: dict[str, Any] = {}
        if target.sni_hostname is not None:
            extensions["sni_hostname"] = target.sni_hostname
        try:
            with httpx.Client(
                timeout=effective_timeout,
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as client:
                response = client.post(
                    target.connect_url,
                    headers=sent_headers,
                    json=payload,
                    extensions=extensions,
                )
                response.raise_for_status()
                # ValueError (json.JSONDecodeError) nie jest httpx.HTTPError — też opakowujemy,
                # by dotrzymać kontraktu transportu (zwraca JSON albo rzuca TransportError).
                data: dict[str, Any] = response.json()
                return data
        except (httpx.HTTPError, ValueError) as exc:
            raise TransportError("Błąd HTTP przy wywołaniu modelu.") from exc


def _parse_openai_response(data: dict[str, Any], model_id: str) -> ChatResponse:
    """Parsuje odpowiedź w formacie OpenAI chat/completions."""
    try:
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelBackendError(model_id, f"Nieprawidłowa odpowiedź backendu: {exc}") from exc

    # content może być JSON null (np. odpowiedź tool-call) lub typem nie-tekstowym —
    # ChatResponse.content jest typu str, więc odrzucamy to jawnym błędem backendu.
    if not isinstance(content, str):
        got = type(content).__name__
        raise ModelBackendError(
            model_id, f"Odpowiedź nie zawiera tekstowego pola 'content' (otrzymano {got})."
        )

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


def _message_payload(m: ChatMessage) -> dict[str, Any]:
    """Serializuje wiadomość do formatu OpenAI. Z obrazami — treść jako lista części
    (text + image_url z data-URI), zgodnie ze standardem multimodal chat/completions."""
    if not m.images:
        return {"role": m.role, "content": m.content}
    parts: list[dict[str, Any]] = []
    if m.content:
        parts.append({"type": "text", "text": m.content})
    for img in m.images:
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:{img.mime};base64,{img.data_b64}"}}
        )
    return {"role": m.role, "content": parts}


class OpenAICompatClient:
    """Klient dowolnego backendu zgodnego z OpenAI (vLLM/Ollama/SGLang)."""

    def __init__(
        self,
        spec: ModelSpec,
        model_id: str,
        *,
        api_key: str | None,
        transport: Transport,
        resolve: HostResolver | None = None,
    ) -> None:
        self.spec = spec
        self.model_id = model_id
        self._api_key = api_key
        self._transport = transport
        self._resolve: HostResolver = resolve if resolve is not None else default_resolve
        timeout = spec.request_timeout_seconds
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS

    def chat(self, request: ChatRequest) -> ChatResponse:
        if self.spec.endpoint is None:
            raise ModelBackendError(self.model_id, "Model nie ma skonfigurowanego endpointu.")

        url = self.spec.endpoint.rstrip("/") + "/chat/completions"
        # Anty-SSRF + pin IP (ADR-0020). Endpoint modelu to własna infrastruktura operatora,
        # więc loopback i LAN są dozwolone — ale nazwa NIE może rozwiązać się na metadane
        # chmury ani inny zakres infrastrukturalny (tam poleciałby klucz API modelu).
        try:
            target = build_pinned_target(
                url, allow_loopback=True, allow_lan=True, resolve=self._resolve
            )
        except EgressError as exc:
            raise ModelBackendError(self.model_id, str(exc)) from exc
        # Priorytet parametrów (rosnąco): params modelu -> extra (escape hatch) ->
        # jawne pola żądania. Kanoniczne 'model'/'messages' ustawiamy NA KOŃCU,
        # aby params/extra nie mogły ich nadpisać (integralność routingu i treści).
        payload: dict[str, Any] = dict(self.spec.params)
        payload.update(request.extra)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop:
            payload["stop"] = request.stop
        payload["model"] = self.spec.model
        payload["messages"] = [_message_payload(m) for m in request.messages]

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            data = self._transport(target, headers, payload, self._timeout)
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
    resolve: HostResolver | None = None,
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
        resolved = provider.resolve(spec.api_key_ref)
        # Fail-closed: skoro model wymaga klucza (api_key_ref), brak sekretu jest
        # błędem konfiguracji, a nie cichą, nieuwierzytelnioną próbą połączenia.
        if resolved is None or not resolved.strip():
            raise ModelBackendError(
                model_id,
                f"Nie udało się rozwiązać sekretu klucza API (api_key_ref='{spec.api_key_ref}').",
            )
        api_key = resolved.strip()

    active_transport = transport or HttpxTransport(timeout=spec.request_timeout_seconds)
    return OpenAICompatClient(
        spec, model_id, api_key=api_key, transport=active_transport, resolve=resolve
    )
