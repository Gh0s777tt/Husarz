"""ModelRouter — orkiestracja wyboru modelu, klienta, fallbacków i limitów.

Router jest niezależny od backendu i w pełni testowalny na mockach: fabryka
klientów jest wstrzykiwalna, więc żaden test nie wykonuje połączeń sieciowych.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace

from husarz.config.schema import HusarzConfig, ModelSpec
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.router.budget import check_fits
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
from husarz.router.zdrowie import RejestrZdrowia

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
        # Rejestr zdrowia żyje TAK DŁUGO jak router, czyli od startu do najbliższego
        # nadpisania konfiguracji w runtime. To świadome: wiedza o awarii jest właściwością
        # bieżącej instalacji, a zmiana konfiguracji może zmienić endpointy, więc odziedziczenie
        # po niej starych liczników odsuwałoby model, który przed chwilą został naprawiony.
        zdrowie = config.routing.health
        self._zdrowie = (
            RejestrZdrowia(
                awarii_do_otwarcia=zdrowie.failures_to_open,
                odsuniecie_sekund=float(zdrowie.cooldown_seconds),
            )
            if zdrowie.cooldown_seconds is not None
            else None
        )

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
        candidates = self._kandydaci(agent=agent, model=model, tags=tags)
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()
        request = self._apply_cost_controls(request)

        failures: list[tuple[str, str]] = []
        for model_id, client in self._zdatni_kandydaci(request, candidates, failures):
            try:
                odpowiedz = client.chat(request)
            except ModelBackendError as exc:
                # Awarią jest WYŁĄCZNIE błąd realnego wywołania. Pominięcia (brak wizji,
                # prompt poza oknem, blokada egress) wynikają z właściwości ŻĄDANIA,
                # nie ze zdrowia modelu — karanie za nie zdegradowałoby model sprawny.
                if self._zdrowie is not None:
                    self._zdrowie.odnotuj_awarie(model_id)
                failures.append((model_id, str(exc)))
                continue
            if self._zdrowie is not None:
                self._zdrowie.odnotuj_sukces(model_id)
            return odpowiedz
        raise AllModelsFailedError(failures)

    def complete_stream(
        self,
        request: ChatRequest,
        *,
        agent: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
    ) -> Iterator[str]:
        """Strumieniuje odpowiedź pierwszego działającego modelu z listy kandydatów.

        **Fallback działa TYLKO do pierwszego fragmentu — i to jest istota tej metody.**
        Dopóki nic nie poszło do wywołującego, awaria jest zwyczajną przyczyną przejścia do
        kolejnego kandydata, dokładnie jak w ``complete``. Gdy jednak fragment już wyszedł,
        przełączenie modelu byłoby SKLEJENIEM dwóch różnych odpowiedzi w jedną — użytkownik
        zobaczyłby początek jednej myśli i dalszy ciąg innej, bez żadnego sygnału. Milcząca
        niespójność jest tu gorsza od widocznego błędu, więc awaria w połowie kończy strumień.

        Yields:
            Fragmenty treści odpowiedzi.

        Raises:
            NoModelAvailableError: Brak pasującego, włączonego modelu.
            RateLimitExceededError: Przekroczono limit żądań.
            ModelBackendError: Model zawiódł PO wysłaniu pierwszego fragmentu.
            AllModelsFailedError: Wszyscy kandydaci zawiedli, zanim cokolwiek wysłali.
        """
        candidates = self._kandydaci(agent=agent, model=model, tags=tags)
        if self._rate_limiter is not None:
            self._rate_limiter.acquire()
        request = self._apply_cost_controls(request)

        failures: list[tuple[str, str]] = []
        for model_id, client in self._zdatni_kandydaci(request, candidates, failures):
            strumien = getattr(client, "chat_stream", None)
            if strumien is None:
                failures.append((model_id, "klient nie obsługuje strumieniowania — pominięto"))
                continue
            cokolwiek_wyslano = False
            try:
                for fragment in strumien(request):
                    cokolwiek_wyslano = True
                    yield fragment
            except ModelBackendError as exc:
                if self._zdrowie is not None:
                    self._zdrowie.odnotuj_awarie(model_id)
                failures.append((model_id, str(exc)))
                if cokolwiek_wyslano:
                    raise
                continue
            if self._zdrowie is not None:
                self._zdrowie.odnotuj_sukces(model_id)
            return
        raise AllModelsFailedError(failures)

    def _kandydaci(
        self, *, agent: str | None, model: str | None, tags: list[str] | None
    ) -> list[str]:
        """Zwraca kandydatów po uporządkowaniu wyłącznikiem bezpiecznikowym.

        Raises:
            NoModelAvailableError: Gdy nie ma żadnego pasującego, włączonego modelu.
        """
        candidates = self.select(agent=agent, model=model, tags=tags)
        if self._zdrowie is not None:
            candidates = self._zdrowie.uporzadkuj(candidates)
        if not candidates:
            raise NoModelAvailableError(
                f"Brak modelu dla żądania (agent={agent}, model={model}, tags={tags})."
            )
        return candidates

    def _zdatni_kandydaci(
        self,
        request: ChatRequest,
        candidates: list[str],
        failures: list[tuple[str, str]],
    ) -> Iterator[tuple[str, ModelClient]]:
        """Filtruje kandydatów przez bramki ŻĄDANIA i buduje klientów.

        Wydzielone, a nie skopiowane do ścieżki strumieniowej: bramki wizyjna, budżetu okna
        kontekstu i egressu są warunkami bezpieczeństwa, a dwie kopie rozjechałyby się przy
        pierwszej zmianie jednej z nich.

        Args:
            request: Żądanie czatu.
            candidates: Identyfikatory kandydatów w kolejności preferencji.
            failures: Lista, do której dopisujemy powody pominięcia (mutowana).

        Yields:
            Pary ``(identyfikator, klient)`` dla kandydatów, którzy przeszli bramki.
        """
        # Bramka wizyjna na poziomie KANDYDATA (nie tylko modelu wybranego w handlerze):
        # jeśli żądanie niesie obrazy, wykonać je może wyłącznie model z ``vision: true``.
        # Inaczej łańcuch fallbacków wysłałby treść multimodalną do modelu tekstowego
        # (cichy błąd/halucynacja) — patrz ADR-0013. Obraz NIE trafia do modelu bez wizji.
        requires_vision = any(m.images for m in request.messages)
        egress = self._config.security.egress
        for model_id in candidates:
            spec = self._config.models.registry[model_id]
            if requires_vision and not spec.vision:
                failures.append(
                    (model_id, "model nie obsługuje obrazów (vision:false) — pominięto")
                )
                continue
            # Bramka budżetu okna kontekstu — PER KANDYDAT, bo modele mają różne okna.
            # Prompt za duży dla modelu 7B może zmieścić się w fallbacku o większym oknie,
            # więc niezmieszczenie się traktujemy jak każdą inną przyczynę pominięcia,
            # a nie jak błąd przerywający łańcuch. Bez tej bramki backend zwracał błąd albo
            # po cichu ucinał kontekst, a agent wypalał limit iteracji, nie wiedząc dlaczego.
            powod = check_fits(
                request.messages,
                context_length=spec.context_length,
                request_max_tokens=request.max_tokens,
                model_max_tokens=spec.max_tokens,
            )
            if powod is not None:
                failures.append((model_id, powod))
                continue
            # Bramka egress (deny-all): nie łączymy się ze zdalnym hostem spoza allowlisty.
            try:
                check_endpoint_allowed(spec.endpoint, egress)
            except EgressError as exc:
                failures.append((model_id, str(exc)))
                continue
            yield model_id, self._client_factory(spec, model_id)

    def _apply_cost_controls(self, request: ChatRequest) -> ChatRequest:
        """Ogranicza ``max_tokens`` do limitu z ``routing.cost_controls`` (jeśli ustawiony)."""
        limit = self._config.routing.cost_controls.max_tokens_per_request
        if limit is None:
            return request
        if request.max_tokens is None or request.max_tokens > limit:
            return replace(request, max_tokens=limit)
        return request
