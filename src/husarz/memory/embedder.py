"""Embedder — zamiana tekstu na wektor. Suwerennie: ZERO cudzych API embeddingów.

Embeddingi są odwracalne do treści/PII, więc traktujemy je jak dane wrażliwe:
- domyślnie i we WSZYSTKICH testach ``FakeEmbedder`` (deterministyczny, offline),
- produkcyjnie ``OllamaEmbedder`` (lokalny ``/api/embeddings``, transport WSTRZYKIWALNY),
  z twardą bramką ``check_endpoint_allowed`` PRZED każdym wywołaniem (deny-all egress).

Transport (HTTP) jest oddzielony od logiki — testy nie wykonują połączeń sieciowych.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any, Protocol, runtime_checkable

from husarz.config.schema import EgressConfig, EmbedderConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.memory.errors import EmbedderError
from husarz.router.egress import check_endpoint_allowed

_DEFAULT_TIMEOUT = 30


@runtime_checkable
class EmbeddingTransport(Protocol):
    """Warstwa transportu HTTP embeddera. Zwraca ``(status, sparsowany_json_lub_None)``."""

    def __call__(
        self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> tuple[int, Any]: ...


@runtime_checkable
class Embedder(Protocol):
    """Zamienia teksty na wektory. API batchowe (pod przyszłe re-indeksowanie)."""

    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class FakeEmbedder:
    """Deterministyczny embedder do testów/dev: wektor z SHA-256 tokenów, L2-norm.

    Identyczny tekst → identyczny wektor (round-trip bez sieci). NIE do produkcji —
    to test-double, nie realne wyszukiwanie semantyczne.
    """

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in text.lower().split() or [text]:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            # Rozkładamy skrót po wymiarach (deterministycznie): każdy token dokłada wagi.
            for offset in range(0, len(digest) - 3, 4):
                idx = struct.unpack(">I", digest[offset : offset + 4])[0] % self._dim
                vector[idx] += 1.0
        return _l2_normalize(vector)


class OllamaEmbedder:
    """Produkcyjny embedder: lokalny Ollama ``/api/embeddings`` przez wstrzykiwalny transport.

    Bramka egress (deny-all) PRZED każdym wywołaniem — loopback/lokalny dozwolony, WAN
    pod deny-all → ``EgressError`` (nie łączymy się, nie wysyłamy wektorów na zewnątrz).
    Klucz (gdy embedder za lokalnym proxy) WYŁĄCZNIE jako referencja do sekretu.
    """

    def __init__(
        self,
        endpoint: str,
        model: str,
        *,
        transport: EmbeddingTransport,
        egress: EgressConfig,
        dim: int,
        token: str | None = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._transport = transport
        self._egress = egress
        self._dim = dim
        self._token = token
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _one(self, text: str) -> list[float]:
        url = f"{self._endpoint}/api/embeddings"
        # Bramka egress (deny-all) — druga warstwa poza walidacją configu; embeddingi
        # (odwracalne do PII) NIE mogą wyjść do hosta spoza allowlisty (rzuca EgressError).
        check_endpoint_allowed(url, self._egress)
        status, data = self._transport(
            url, self._headers(), {"model": self._model, "prompt": text}, self._timeout
        )
        if status >= 400:
            raise EmbedderError(f"Serwer embeddingów zwrócił HTTP {status}.")
        vector = data.get("embedding") if isinstance(data, dict) else None
        if not isinstance(vector, list) or not all(isinstance(v, (int, float)) for v in vector):
            raise EmbedderError("Odpowiedź embeddera nie zawiera wektora 'embedding'.")
        if len(vector) != self._dim:
            # Fail-closed: niezgodny wymiar skorumpowałby magazyn (mieszanie modeli).
            raise EmbedderError(
                f"Wektor embeddera ma wymiar {len(vector)}, oczekiwano {self._dim} "
                "(embedding_dim). Sprawdź model embeddingów."
            )
        return [float(v) for v in vector]


class HttpxEmbeddingTransport:
    """Transport oparty o httpx (import leniwy). ``verify=True`` jawnie, bez redirectów."""

    def __call__(
        self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int
    ) -> tuple[int, Any]:
        try:
            import httpx  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - httpx w pyproject
            raise EmbedderError("Pakiet 'httpx' nie jest zainstalowany.") from exc
        try:
            response = httpx.post(
                url,
                headers=headers,
                json=json,
                timeout=timeout,
                follow_redirects=False,
                verify=True,
            )
        except httpx.HTTPError as exc:
            raise EmbedderError("Błąd transportu do serwera embeddingów.") from exc
        try:
            parsed: Any = response.json()
        except ValueError:
            parsed = None
        return response.status_code, parsed


def build_embedder(
    config: EmbedderConfig,
    egress: EgressConfig,
    *,
    transport: EmbeddingTransport | None = None,
    secrets: SecretsProvider | None = None,
) -> Embedder:
    """Buduje embedder wg konfiguracji (``fake`` — dev/test; ``ollama`` — produkcja).

    Raises:
        EmbedderError: nieznany rodzaj embeddera lub nierozwiązywalny token.
    """
    if config.kind == "fake":
        return FakeEmbedder(dim=config.dim)
    if config.kind == "ollama":
        resolver = secrets if secrets is not None else NullSecretsProvider()
        token: str | None = None
        if config.api_key_ref:
            resolved = resolver.resolve(config.api_key_ref)
            if not resolved or not resolved.strip():
                raise EmbedderError(
                    f"Nie udało się rozwiązać api_key_ref embeddera ('{config.api_key_ref}')."
                )
            token = resolved.strip()
        endpoint = config.endpoint or "http://127.0.0.1:11434"
        return OllamaEmbedder(
            endpoint,
            config.model or "nomic-embed-text",
            transport=transport if transport is not None else HttpxEmbeddingTransport(),
            egress=egress,
            dim=config.dim,
            token=token,
        )
    raise EmbedderError(f"Nieznany rodzaj embeddera: '{config.kind}'.")
