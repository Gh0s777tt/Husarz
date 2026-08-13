"""Router modeli — warstwa OpenAI-compat do vLLM/Ollama/SGLang.

Dobór modelu po tagach/capabilities z config/models.yaml + routing.yaml,
z fallbackami i kontrolą kosztów. Wszystko sterowane konfiguracją (zero hardcode).

Publiczne API:
    ModelRouter        — orkiestracja wyboru + wywołania z fallbackami,
    select_candidates  — czysta logika wyboru kandydatów,
    ChatRequest/ChatResponse/ChatMessage — typy żądania i odpowiedzi,
    build_client       — fabryka klientów (mock / OpenAI-compat),
    błędy routera      — hierarchia RouterError.
"""

from __future__ import annotations

from husarz.router.client import (
    HttpxTransport,
    MockClient,
    ModelClient,
    OpenAICompatClient,
    Transport,
    build_client,
)
from husarz.router.egress import EgressError, check_endpoint_allowed
from husarz.router.errors import (
    AllModelsFailedError,
    ModelBackendError,
    NoModelAvailableError,
    RateLimitExceededError,
    RouterError,
    TransportError,
)
from husarz.router.rate_limit import RateLimiter
from husarz.router.router import ClientFactory, ModelRouter
from husarz.router.selection import select_candidates
from husarz.router.types import ChatMessage, ChatRequest, ChatResponse, ImagePart, Usage

__all__ = [
    "AllModelsFailedError",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ClientFactory",
    "EgressError",
    "HttpxTransport",
    "ImagePart",
    "MockClient",
    "ModelBackendError",
    "ModelClient",
    "ModelRouter",
    "NoModelAvailableError",
    "OpenAICompatClient",
    "RateLimiter",
    "RateLimitExceededError",
    "RouterError",
    "Transport",
    "TransportError",
    "Usage",
    "build_client",
    "check_endpoint_allowed",
    "select_candidates",
]
