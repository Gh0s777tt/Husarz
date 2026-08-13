"""REST API rdzenia Husarza (FastAPI).

``create_app`` składa aplikację z konfiguracji. Router modeli, audyt i katalog
promptów są wstrzykiwalne — dzięki temu API testuje się przez ``TestClient`` bez
uruchamiania serwera ani połączeń sieciowych.

Uwierzytelnianie: gdy podano ``api_token`` (rozwiązywany z sekretu przez launcher),
wszystkie endpointy ``/api`` — poza ``/api/health`` (liveness) — wymagają nagłówka
``Authorization: Bearer <token>``, a autoryzacja opiera się na RBAC (rola z
``api_role``). Gdy tokenu brak, API działa bez uwierzytelnienia — dopuszczalne
wyłącznie dla nasłuchu loopback (launcher wymusza token dla adresów nie-loopback).
"""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse

from husarz import __version__
from husarz.accounts import AccountService, Principal
from husarz.accounts.errors import (
    AccountError,
    AuthenticationError,
    QuotaExceededError,
    RegistrationDisabledError,
)
from husarz.agents.base import SupportsComplete
from husarz.api.schemas import (
    AgentInfo,
    AuditEntryView,
    AuditView,
    AuthToken,
    ChatReply,
    ChatRequest,
    ConfigSummary,
    HealthResponse,
    LoginRequest,
    MeResponse,
    ModelInfo,
    ObservationView,
    OrchestrateRequest,
    OrchestrateResponse,
    RegisterRequest,
    ToolInfo,
    UsageResponse,
    ValidateRequest,
    ValidateResponse,
)
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError
from husarz.orchestrator import Orchestrator, build_orchestrator
from husarz.router import ChatMessage, Usage
from husarz.router import ChatRequest as RouterChatRequest
from husarz.router.errors import (
    NoModelAvailableError,
    RateLimitExceededError,
    RouterError,
)
from husarz.security.audit import AuditLog, build_audit_log
from husarz.security.rbac import Rbac

_STATIC_DIR = Path(__file__).parent / "static"

# Uprawnienia RBAC wymagane per endpoint (obszar:akcja — patrz husarz.security.rbac).
_PERM_CONFIG_READ = "config:read"
_PERM_CONFIG_WRITE = "config:write"
_PERM_AGENT_RUN = "agent:run"
_PERM_AUDIT_READ = "audit:read"


def _summary(config: HusarzConfig) -> ConfigSummary:
    return ConfigSummary(
        profile=config.platform.profile.value,
        log_level=config.platform.log_level.value,
        default_model=config.models.default,
        models=sorted(config.models.registry),
        agents=sorted(config.agents),
        tools=sorted(config.tools),
        roe=sorted(config.roe),
        egress_policy=config.security.egress.default_policy.value,
        sandbox_engine=config.security.sandbox.engine.value,
        sandbox_network=config.security.sandbox.network,
    )


def create_app(
    config: HusarzConfig,
    *,
    config_dir: str | Path | None = None,
    audit: AuditLog | None = None,
    router: SupportsComplete | None = None,
    router_factory: Callable[[HusarzConfig], SupportsComplete] | None = None,
    prompts_dir: str | Path = "./prompts",
    api_token: str | None = None,
    api_role: str | None = None,
    rbac: Rbac | None = None,
    accounts: AccountService | None = None,
    chat_model: str | None = None,
    trusted_hosts: list[str] | None = None,
) -> FastAPI:
    """Buduje aplikację FastAPI dla podanej konfiguracji.

    ``api_token`` (opcjonalny) włącza uwierzytelnianie Bearer + RBAC. ``api_role``
    to rola przypisywana ważnemu tokenowi (domyślnie z ``security.auth.api_role``).
    ``router_factory`` pozwala PRZEBUDOWAĆ router+orkiestrator po nadpisaniu configu
    w runtime — bez niego ``/api/orchestrate`` i ``/api/chat`` działałyby na starej
    konfiguracji. ``chat_model`` nadpisuje model trybu czatu (domyślnie z configu).
    """
    app = FastAPI(title="Husarz API", version=__version__)
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    audit_log = audit if audit is not None else build_audit_log(config.security)
    role = api_role if api_role is not None else config.security.auth.api_role
    authz = rbac if rbac is not None else Rbac()

    def _build_stack(cfg: HusarzConfig) -> tuple[SupportsComplete | None, Orchestrator | None]:
        active = router_factory(cfg) if router_factory is not None else router
        orch = build_orchestrator(cfg, active, prompts_dir=prompts_dir) if active else None
        return active, orch

    def _resolve_chat_model(cfg: HusarzConfig) -> str:
        return chat_model or cfg.models.chat or cfg.models.default

    initial_router, initial_orch = _build_stack(config)
    state: dict[str, Any] = {
        "config": config,
        "config_dir": config_dir,
        "router": initial_router,
        "orchestrator": initial_orch,
        "runtime_overrides": {},
        "orchestrations": 0,
        "chats": 0,
        "failures": 0,
    }
    counter_lock = threading.Lock()

    def _authenticate(authorization: str | None) -> Principal | None:
        """Zwraca principala ważnego tokenu; ``None`` gdy uwierzytelnianie wyłączone.

        Akceptuje statyczny token maszynowy (``api_token``) ORAZ token sesji
        użytkownika (gdy skonfigurowano konta). Uwierzytelnianie jest włączone, gdy
        ustawiono ``api_token`` albo wstrzyknięto usługę kont.
        """
        if api_token is None and accounts is None:
            return None  # tryb dev (tylko loopback — patrz launcher)
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=401, detail="Wymagany token Bearer.")
        presented = authorization[len(prefix) :]
        # 1) Statyczny token maszynowy (admin/CI) — porównanie w stałym czasie.
        if api_token is not None and hmac.compare_digest(
            presented.encode("utf-8"), api_token.encode("utf-8")
        ):
            return Principal(role=role, user_id=None, username=None)
        # 2) Token sesji użytkownika.
        if accounts is not None:
            account = accounts.resolve_session(presented)
            if account is not None:
                return Principal(
                    role=account.role, user_id=account.user_id, username=account.username
                )
        raise HTTPException(status_code=401, detail="Nieprawidłowy lub wygasły token.")

    def _require(permission: str) -> Callable[[str | None], Principal | None]:
        def dependency(authorization: str | None = Header(default=None)) -> Principal | None:
            principal = _authenticate(authorization)
            if principal is None:
                return None  # uwierzytelnianie wyłączone (dev)
            if not authz.can(principal.role, permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"Rola '{principal.role}' nie ma uprawnienia '{permission}'.",
                )
            return principal

        return dependency

    # Zależności policzone raz (uniknięcie wywołania fabryki w domyślnych argumentach).
    dep_config_read = Depends(_require(_PERM_CONFIG_READ))
    dep_config_write = Depends(_require(_PERM_CONFIG_WRITE))
    dep_agent_run = Depends(_require(_PERM_AGENT_RUN))
    dep_audit_read = Depends(_require(_PERM_AUDIT_READ))

    # /api/health celowo BEZ uwierzytelniania — sonda liveness (minimalne dane).
    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        current: HusarzConfig = state["config"]
        return HealthResponse(
            status="ok", version=__version__, profile=current.platform.profile.value
        )

    @app.get(
        "/api/config/summary",
        response_model=ConfigSummary,
        dependencies=[dep_config_read],
    )
    def config_summary() -> ConfigSummary:
        return _summary(state["config"])

    @app.get(
        "/api/agents",
        response_model=list[AgentInfo],
        dependencies=[dep_config_read],
    )
    def agents() -> list[AgentInfo]:
        current: HusarzConfig = state["config"]
        return [
            AgentInfo(
                name=name,
                display_name=agent.display_name,
                agent_class=agent.agent_class.value,
                model=agent.model,
                tools=list(agent.tools),
                roe_required=agent.roe_required,
                enabled=agent.enabled,
            )
            for name, agent in sorted(current.agents.items())
        ]

    @app.get(
        "/api/models",
        response_model=list[ModelInfo],
        dependencies=[dep_config_read],
    )
    def models() -> list[ModelInfo]:
        current: HusarzConfig = state["config"]
        return [
            ModelInfo(
                id=model_id,
                backend=spec.backend.value,
                tags=list(spec.tags),
                context_length=spec.context_length,
                enabled=spec.enabled,
            )
            for model_id, spec in sorted(current.models.registry.items())
        ]

    @app.get(
        "/api/tools",
        response_model=list[ToolInfo],
        dependencies=[dep_config_read],
    )
    def tools() -> list[ToolInfo]:
        current: HusarzConfig = state["config"]
        return [
            ToolInfo(
                name=name,
                kind=tool.kind,
                enabled=tool.enabled,
                requires_egress=tool.requires_egress,
            )
            for name, tool in sorted(current.tools.items())
        ]

    @app.get(
        "/api/audit",
        response_model=AuditView,
        dependencies=[dep_audit_read],
    )
    def audit_view(limit: int = Query(default=50, ge=0, le=10_000)) -> AuditView:
        entries = audit_log.entries
        # limit==0 → pusta lista (nie „wszystko"); zakres pilnowany przez Query.
        recent = entries[-limit:] if limit > 0 else []
        return AuditView(
            verified=audit_log.verify(),
            count=len(entries),
            entries=[
                AuditEntryView(
                    timestamp=e.timestamp, actor=e.actor, action=e.action, roe_ref=e.roe_ref
                )
                for e in recent
            ],
        )

    @app.get(
        "/api/usage",
        response_model=UsageResponse,
        dependencies=[dep_config_read],
    )
    def usage() -> UsageResponse:
        current: HusarzConfig = state["config"]
        cost = current.routing.cost_controls
        return UsageResponse(
            orchestrations=state["orchestrations"],
            chats=state["chats"],
            failures=state["failures"],
            max_tokens_per_request=cost.max_tokens_per_request,
            max_requests_per_minute=cost.max_requests_per_minute,
        )

    @app.post("/api/orchestrate", response_model=OrchestrateResponse)
    def orchestrate(
        request: OrchestrateRequest,
        principal: Principal | None = dep_agent_run,
    ) -> OrchestrateResponse:
        orch: Orchestrator | None = state["orchestrator"]
        if orch is None:
            raise HTTPException(status_code=503, detail="Orkiestrator niedostępny (brak routera).")
        _enforce_quota(accounts, principal)
        # Audyt + licznik prób PRZED uruchomieniem — spójne również przy porażce.
        audit_log.record("api", "orchestrate", {"task": request.task[:200]})
        with counter_lock:
            state["orchestrations"] += 1
        try:
            result = orch.run(request.task)
        except RateLimitExceededError as exc:
            _record_failure(state, counter_lock, audit_log, "rate_limit")
            raise HTTPException(
                status_code=429, detail="Przekroczono limit żądań (kontrola kosztów)."
            ) from exc
        except NoModelAvailableError as exc:
            _record_failure(state, counter_lock, audit_log, "no_model")
            raise HTTPException(
                status_code=503, detail="Brak dostępnego modelu dla żądania."
            ) from exc
        except RouterError as exc:
            _record_failure(state, counter_lock, audit_log, "backend")
            raise HTTPException(status_code=502, detail="Backend modelu zawiódł.") from exc
        return OrchestrateResponse(
            task=result.task,
            answer=result.answer,
            rounds=result.rounds,
            observations=[
                ObservationView(agent=o.agent, output=o.output, model=o.model)
                for o in result.observations
            ],
        )

    @app.post("/api/chat", response_model=ChatReply)
    def chat(
        request: ChatRequest,
        principal: Principal | None = dep_agent_run,
    ) -> ChatReply:
        # Tryb bezpośredni: jeden model, bez orkiestracji wieloagentowej (szybki czat
        # + kodowanie). Persona jest zaszyta w customowym modelu (ollama/Husarz.Modelfile).
        # Spójny snapshot pod zamkiem — inaczej równoległe /api/config/runtime mogłoby
        # sparować NOWY models.chat ze STARYM routerem (przejściowy 503).
        with counter_lock:
            active_router: SupportsComplete | None = state["router"]
            current_config: HusarzConfig = state["config"]
        if active_router is None:
            raise HTTPException(status_code=503, detail="Model czatu niedostępny (brak routera).")
        _enforce_quota(accounts, principal)
        model_id = request.model or _resolve_chat_model(current_config)
        chat_request = RouterChatRequest(
            messages=[ChatMessage(role=m.role, content=m.content) for m in request.messages],
            temperature=request.temperature,
        )
        audit_log.record("api", "chat", {"model": model_id, "turns": len(request.messages)})
        with counter_lock:
            state["chats"] += 1
        try:
            result = active_router.complete(chat_request, model=model_id)
        except RateLimitExceededError as exc:
            _record_failure(state, counter_lock, audit_log, "rate_limit", action="chat.error")
            raise HTTPException(
                status_code=429, detail="Przekroczono limit żądań (kontrola kosztów)."
            ) from exc
        except NoModelAvailableError as exc:
            _record_failure(state, counter_lock, audit_log, "no_model", action="chat.error")
            raise HTTPException(
                status_code=503, detail=f"Model czatu '{model_id}' niedostępny."
            ) from exc
        except RouterError as exc:
            _record_failure(state, counter_lock, audit_log, "backend", action="chat.error")
            raise HTTPException(status_code=502, detail="Backend modelu zawiódł.") from exc
        _record_tokens(accounts, principal, result.usage)
        return ChatReply(model=result.model, content=result.content)

    @app.post(
        "/api/config/validate",
        response_model=ValidateResponse,
        dependencies=[dep_config_read],
    )
    def config_validate(request: ValidateRequest) -> ValidateResponse:
        cdir = state["config_dir"]
        if cdir is None:
            return ValidateResponse(ok=False, error="Walidacja wymaga katalogu konfiguracji.")
        try:
            merged = load_config(cdir, runtime_overrides=request.overrides)
        except ConfigError as exc:
            return ValidateResponse(ok=False, error=str(exc))
        return ValidateResponse(ok=True, summary=_summary(merged))

    @app.post(
        "/api/config/runtime",
        response_model=ValidateResponse,
        dependencies=[dep_config_write],
    )
    def config_apply(request: ValidateRequest) -> ValidateResponse:
        cdir = state["config_dir"]
        if cdir is None:
            return ValidateResponse(ok=False, error="Nadpisania wymagają katalogu konfiguracji.")
        try:
            merged = load_config(cdir, runtime_overrides=request.overrides)
        except ConfigError as exc:
            return ValidateResponse(ok=False, error=str(exc))
        # Przebuduj router+orkiestrator, by /api/orchestrate i /api/chat używały
        # NOWEJ konfiguracji (a nie starej sprzed nadpisania). Budowa poza zamkiem,
        # atomowa podmiana pod zamkiem — spójna para (config, router) dla /api/chat.
        new_router, new_orch = _build_stack(merged)
        with counter_lock:
            state["config"] = merged
            state["runtime_overrides"] = request.overrides
            state["router"] = new_router
            state["orchestrator"] = new_orch
        audit_log.record("api", "config.runtime_override", {"keys": sorted(request.overrides)})
        return ValidateResponse(ok=True, summary=_summary(merged))

    # --- Konta: rejestracja / logowanie / wylogowanie / bieżący użytkownik -----
    # Endpointy register/login są PUBLICZNE (inaczej nie dałoby się zalogować).

    @app.post("/api/auth/register", response_model=AuthToken)
    def auth_register(request: RegisterRequest) -> AuthToken:
        if accounts is None:
            raise HTTPException(status_code=404, detail="Konta nie są włączone.")
        try:
            accounts.register(request.username, request.password)
            account, token = accounts.login(request.username, request.password)
        except RegistrationDisabledError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except AccountError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_log.record("api", "auth.register", {"username": account.username})
        return AuthToken(token=token, username=account.username, role=account.role)

    @app.post("/api/auth/login", response_model=AuthToken)
    def auth_login(request: LoginRequest) -> AuthToken:
        if accounts is None:
            raise HTTPException(status_code=404, detail="Konta nie są włączone.")
        try:
            account, token = accounts.login(request.username, request.password)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=401, detail="Nieprawidłowa nazwa użytkownika lub hasło."
            ) from exc
        audit_log.record("api", "auth.login", {"username": account.username})
        return AuthToken(token=token, username=account.username, role=account.role)

    @app.post("/api/auth/logout")
    def auth_logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        prefix = "Bearer "
        if accounts is not None and authorization and authorization.startswith(prefix):
            accounts.logout(authorization[len(prefix) :])
        return {"ok": True}

    @app.get("/api/auth/me", response_model=MeResponse)
    def auth_me(
        principal: Principal | None = dep_config_read,
    ) -> MeResponse:
        model_id = _resolve_chat_model(state["config"])
        if principal is None:
            # Auth wyłączone (dev/loopback) — tryb otwarty, bez limitu.
            return MeResponse(
                username="(dev)",
                role="(bez uwierzytelniania)",
                chat_model=model_id,
                tokens_used=0,
                token_quota=None,
                tokens_remaining=None,
            )
        if principal.user_id is None or accounts is None:
            # Statyczny token maszynowy — bez konta i limitu.
            return MeResponse(
                username=principal.username or "(token maszynowy)",
                role=principal.role,
                chat_model=model_id,
                tokens_used=0,
                token_quota=None,
                tokens_remaining=None,
            )
        account = accounts.get(principal.user_id)
        if account is None:
            raise HTTPException(status_code=404, detail="Konto nie istnieje.")
        remaining = (
            None
            if account.token_quota is None
            else max(0, account.token_quota - account.tokens_used)
        )
        return MeResponse(
            username=account.username,
            role=account.role,
            chat_model=model_id,
            tokens_used=account.tokens_used,
            token_quota=account.token_quota,
            tokens_remaining=remaining,
        )

    @app.get("/")
    def console() -> FileResponse:
        return FileResponse(_STATIC_DIR / "console.html", media_type="text/html")

    return app


def _record_failure(
    state: dict[str, Any],
    counter_lock: threading.Lock,
    audit_log: AuditLog,
    reason: str,
    *,
    action: str = "orchestrate.error",
) -> None:
    """Liczy porażkę i zapisuje audyt (bez surowej treści błędu — tylko kategoria).

    ``action`` odróżnia powierzchnię błędu (``orchestrate.error`` / ``chat.error``),
    by wpis w niemodyfikowalnym dzienniku wiernie wskazywał źródło porażki.
    """
    with counter_lock:
        state["failures"] += 1
    audit_log.record("api", action, {"error": reason})


def _enforce_quota(accounts: AccountService | None, principal: Principal | None) -> None:
    """Blokuje żądanie (HTTP 402), gdy konto wyczerpało limit tokenów."""
    if accounts is None or principal is None or principal.user_id is None:
        return
    try:
        accounts.check_quota(principal.user_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc


def _record_tokens(
    accounts: AccountService | None, principal: Principal | None, usage: Usage | None
) -> None:
    """Dolicza zużyte tokeny do konta zalogowanego użytkownika (jeśli backend je zwrócił)."""
    if accounts is None or principal is None or principal.user_id is None:
        return
    total = usage.total_tokens if usage is not None else None
    if total:
        accounts.record_usage(principal.user_id, total)
