"""ModelRouter — orkiestracja wyboru modelu, klienta, fallbacków i limitów.

Router jest niezależny od backendu i w pełni testowalny na mockach: fabryka
klientów jest wstrzykiwalna, więc żaden test nie wykonuje połączeń sieciowych.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from husarz.config.schema import HusarzConfig, ModelSpec
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.router.client import ModelClient, build_client
from husarz.router.egress import EgressError, check_endpoint_allowed
from husarz.router.errors import (
    AllModelsFailedError,
    ModelBackendError,
    NoModelAvailableError,
)
from husarz.router.rate_limit import RateLimiter
from husarz.router.selection import select_candidates
from husarz.router.types import ChatRequest, ChatResponse

# Fabryka klientów: (spec, model_id) -> klient.
ClientFactory = Callable[[ModelSpec, str], ModelClient]


class ModelRouter:
    """Dobiera model do żądania, wykonuje wywołanie z fallbackami i limitami kosztów."""

    def __init__(
        self,
        config: HusarzConfig,
        *,
        secrets: SecretsProvider | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._config = config
        self._secrets = secrets or NullSecretsProvider()
        self._client_factory = client_factory or self._default_factory
        max_rpm = config.routing.cost_controls.max_requests_per_minute
        self._rate_limiter = RateLimiter(max_rpm) if max_rpm is not None else None

    def _default_factory(self, spec: ModelSpec, model_id: str) -> ModelClient:
        return build_client(spec, model_id, secrets=self._secrets)

    def select(
        self,
        *,
        agent: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> list[str]:
        """Zwraca uporządkowaną listę modeli-kandydatów (patrz ``select_candidates``)."""
        return select_candidates(self._config, agent=agent, model=model, tags=tags)

    def complete(
        self,
        request: ChatRequest,
        *,
        agent: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> ChatResponse:
        """Wykonuje żądanie na pierwszym działającym modelu z listy kandydatów.

        Raises:
            NoModelAvailableError: brak pasującego, włączonego modelu.
            RateLimitExceededError: przekroczono limit żądań (kontrola kosztów).
            AllModelsFailedError: wszyscy kandydaci (z fallbackami) zawiedli.
        """
        candidates = self.select(agent=agent, model=model, tags=tags)
        if not candidates:
            raise NoModelAvailableError(
                f"Brak modelu dla żądania (agent={agent}, model={model}, tags={tags})."
            )

        if self._rate_limiter is not None:
            self._rate_limiter.acquire()

        request = self._apply_cost_controls(request)

        egress = self._config.security.egress
        failures: list[tuple[str, str]] = []
        for model_id in candidates:
            spec = self._config.models.registry[model_id]
            # Bramka egress (deny-all): nie łączymy się ze zdalnym hostem spoza allowlisty.
            try:
                check_endpoint_allowed(spec.endpoint, egress)
            except EgressError as exc:
                failures.append((model_id, str(exc)))
                continue
            client = self._client_factory(spec, model_id)
            try:
                return client.chat(request)
            except ModelBackendError as exc:
                failures.append((model_id, str(exc)))
                continue
        raise AllModelsFailedError(failures)

    def _apply_cost_controls(self, request: ChatRequest) -> ChatRequest:
        """Ogranicza ``max_tokens`` do limitu z ``routing.cost_controls`` (jeśli ustawiony)."""
        limit = self._config.routing.cost_controls.max_tokens_per_request
        if limit is None:
            return request
        if request.max_tokens is None or request.max_tokens > limit:
            return replace(request, max_tokens=limit)
        return request
