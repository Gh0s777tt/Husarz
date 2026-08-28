"""Klienci modeli — warstwa OpenAI-compat (vLLM/Ollama/SGLang) + mock.

Transport (HTTP) jest oddzielony od logiki klienta, dzięki czemu testy wstrzykują
własny transport i nie wykonują żadnych połączeń sieciowych (zgodnie z deny-all
egress). Klucz API pochodzi z referencji do sekretu (``api_key_ref``) rozwiązywanej
przez dostawcę sekretów — nigdy nie jest wpisywany na stałe.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
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
class StreamingTransport(Protocol):
    """Transport strumieniowy — zwraca kolejne ładunki zdarzeń SSE.

    **Dlaczego OSOBNY protokół, a nie metoda w ``Transport``.** Dopisanie metody do
    istniejącego protokołu unieważniłoby każdą atrapę transportu w testach, a jest ich
    wiele — i to bez żadnego zysku, bo strumieniowanie jest zdolnością OPCJONALNĄ.
    Klient sprawdza obecność metody i mówi wprost, gdy transportu nie da się strumieniować.

    Zwracamy SUROWE ładunki ``data:`` (bez prefiksu), a nie gotowe fragmenty tekstu:
    transport ma pozostać nieświadomy formatu OpenAI, tak jak przy zwykłym wywołaniu.
    """

    def stream(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int,
    ) -> Iterator[str]: ...


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

    def stream(
        self,
        target: PinnedTarget,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int | None = None,
    ) -> Iterator[str]:
        """Zwraca kolejne ładunki ``data:`` ze strumienia SSE.

        Zachowuje WSZYSTKIE zabezpieczenia zwykłego wywołania — pin IP, nagłówek ``Host``
        i SNI po oryginalnej nazwie, brak przekierowań, ``trust_env=False``. To nie jest
        kopia dla wygody: gdyby ścieżka strumieniowa rozwiązywała nazwę ponownie, otwierałaby
        okno TOCTOU zamknięte w ADR-0020, i to na połączeniu niosącym klucz API modelu.

        Args:
            target: Przypięty cel (IP + oryginalna nazwa do TLS).
            headers: Nagłówki żądania.
            payload: Ładunek JSON (musi zawierać ``stream: true``).
            timeout: Limit czasu; ``None`` = wartość z konstruktora albo domyślna.

        Yields:
            Surowe ładunki po ``data:`` — bez prefiksu, bez pustych linii, bez ``[DONE]``.

        Raises:
            TransportError: Gdy połączenie albo odpowiedź HTTP zawiodą.
        """
        try:
            import httpx  # noqa: PLC0415
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
            with (
                httpx.Client(
                    timeout=effective_timeout,
                    follow_redirects=False,
                    verify=True,
                    trust_env=False,
                ) as client,
                client.stream(
                    "POST",
                    target.connect_url,
                    headers=sent_headers,
                    json=payload,
                    extensions=extensions,
                ) as response,
            ):
                response.raise_for_status()
                for linia in response.iter_lines():
                    ladunek = _ladunek_sse(linia)
                    if ladunek is not None:
                        yield ladunek
        except httpx.HTTPError as exc:
            raise TransportError("Błąd HTTP przy strumieniowaniu z modelu.") from exc


#: Prefiks pola danych w Server-Sent Events.
_PREFIKS_SSE = "data:"
#: Umowny znacznik końca strumienia w API zgodnym z OpenAI.
ZNACZNIK_KONCA = "[DONE]"


def _ladunek_sse(linia: str) -> str | None:
    """Wyłuskuje ładunek z linii SSE; ``None`` dla linii bez znaczenia.

    Przepuszczamy WYŁĄCZNIE linie ``data:``, więc wszystko inne odpada samo: puste linie
    (separatory zdarzeń), komentarze SSE zaczynające się od ``:`` (używane jako sygnał
    utrzymania połączenia), nagłówki ``event:`` i ``id:``. Parsowanie formatu należy do
    klienta — transport ma pozostać nieświadomy OpenAI.

    Pierwsza wersja sprawdzała komentarze osobnym warunkiem ``startswith(":")``. Kontrola
    nośności pokazała, że jest ZBĘDNY: linia zaczynająca się od dwukropka i tak nie zaczyna
    się od ``data:``. Warunek, który nigdy nie rozstrzyga, wygląda na kontrolę i nią nie
    jest — a to ta sama klasa wady, którą usuwał Etap 17m.

    Args:
        linia: Pojedyncza linia odpowiedzi.

    Returns:
        Ładunek bez prefiksu albo ``None``.
    """
    tekst = linia.strip()
    if not tekst.startswith(_PREFIKS_SSE):
        return None
    ladunek = tekst[len(_PREFIKS_SSE) :].strip()
    if not ladunek or ladunek == ZNACZNIK_KONCA:
        return None
    return ladunek


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

    def _przygotuj(
        self, request: ChatRequest, *, strumien: bool
    ) -> tuple[PinnedTarget, dict[str, str], dict[str, Any]]:
        """Buduje cel, nagłówki i ładunek — WSPÓLNIE dla wywołania zwykłego i strumieniowego.

        Wydzielone celowo, a nie skopiowane: ścieżka strumieniowa musi mieć dokładnie ten
        sam pin IP, tę samą kolejność pierwszeństwa parametrów i te same nagłówki. Dwie
        kopie rozjechałyby się przy pierwszej zmianie jednej z nich, a rozjazd dotyczyłby
        połączenia niosącego klucz API modelu.

        Args:
            request: Żądanie czatu.
            strumien: Czy dopisać ``stream: true`` do ładunku.

        Returns:
            Trójka ``(cel, nagłówki, ładunek)``.

        Raises:
            ModelBackendError: Gdy brak endpointu albo cel nie przechodzi kontroli anty-SSRF.
        """
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

        if strumien:
            payload["stream"] = True

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return target, headers, payload

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Wykonuje żądanie i zwraca pełną odpowiedź.

        Args:
            request: Żądanie czatu.

        Returns:
            Odpowiedź modelu.

        Raises:
            ModelBackendError: Gdy backend zawiedzie albo odpowiedź jest nieprawidłowa.
        """
        target, headers, payload = self._przygotuj(request, strumien=False)
        try:
            data = self._transport(target, headers, payload, self._timeout)
        except TransportError as exc:
            raise ModelBackendError(self.model_id, str(exc)) from exc
        return _parse_openai_response(data, self.model_id)

    def chat_stream(self, request: ChatRequest) -> Iterator[str]:
        """Zwraca kolejne FRAGMENTY treści odpowiedzi, w miarę jak przychodzą.

        Args:
            request: Żądanie czatu.

        Yields:
            Niepuste fragmenty tekstu (``choices[0].delta.content``).

        Raises:
            ModelBackendError: Gdy transport nie wspiera strumieniowania albo backend
                zawiedzie. Awaria PO wysłaniu pierwszego fragmentu też kończy się tym
                wyjątkiem — router nie może już wtedy sięgnąć po model zapasowy, bo
                wywołujący widzi już początek innej odpowiedzi (patrz ``complete_stream``).
        """
        if not isinstance(self._transport, StreamingTransport):
            raise ModelBackendError(
                self.model_id,
                "Transport nie obsługuje strumieniowania (brak metody `stream`).",
            )
        target, headers, payload = self._przygotuj(request, strumien=True)
        try:
            for ladunek in self._transport.stream(target, headers, payload, self._timeout):
                fragment = _fragment_z_delty(ladunek)
                if fragment:
                    yield fragment
        except TransportError as exc:
            raise ModelBackendError(self.model_id, str(exc)) from exc


def _fragment_z_delty(ladunek: str) -> str:
    """Wyłuskuje tekst z jednego zdarzenia strumienia OpenAI.

    Zdarzenie bez treści (sam ``finish_reason``, sama rola, licznik zużycia) daje pusty
    napis — to normalny element strumienia, nie błąd. Ładunek NIEPARSOWALNY również
    pomijamy: pojedyncze uszkodzone zdarzenie nie może wywrócić całej odpowiedzi, a jego
    treści i tak nie da się odzyskać.

    Args:
        ladunek: Ładunek ``data:`` jednego zdarzenia SSE.

    Returns:
        Fragment tekstu albo pusty napis.
    """
    try:
        dane = json.loads(ladunek)
    except ValueError:
        return ""
    if not isinstance(dane, dict):
        return ""
    wybory = dane.get("choices")
    if not isinstance(wybory, list) or not wybory:
        return ""
    pierwszy = wybory[0]
    if not isinstance(pierwszy, dict):
        return ""
    delta = pierwszy.get("delta")
    if not isinstance(delta, dict):
        return ""
    tresc = delta.get("content")
    return tresc if isinstance(tresc, str) else ""


class MockClient:
    """Klient testowy/deweloperski — nie łączy się z siecią, zwraca deterministyczną odpowiedź."""

    def __init__(self, model_id: str, spec: ModelSpec) -> None:
        self.model_id = model_id
        self.spec = spec

    def chat(self, request: ChatRequest) -> ChatResponse:
        """Zwraca deterministyczną odpowiedź echa."""
        last = request.messages[-1].content if request.messages else ""
        return ChatResponse(
            model=self.model_id,
            content=f"[mock:{self.spec.model}] {last}",
            finish_reason="stop",
        )

    def chat_stream(self, request: ChatRequest) -> Iterator[str]:
        """Oddaje tę samą odpowiedź słowo po słowie.

        Istnieje po to, żeby ścieżkę strumieniową dało się uruchomić i obejrzeć BEZ modelu —
        w testach i na stanowisku bez silnika. Sklejenie fragmentów daje dokładnie treść
        z ``chat``, więc atrapa nie może po cichu rozjechać się z wersją nierostrumieniową.
        """
        tresc = self.chat(request).content
        for indeks, slowo in enumerate(tresc.split(" ")):
            yield slowo if indeks == 0 else f" {slowo}"


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
