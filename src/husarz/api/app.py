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
from husarz.agents.base import SupportsComplete
from husarz.api.schemas import (
    AgentInfo,
    AuditEntryView,
    AuditView,
    ConfigSummary,
    HealthResponse,
    ModelInfo,
    ObservationView,
    OrchestrateRequest,
    OrchestrateResponse,
    ToolInfo,
    UsageResponse,
    ValidateRequest,
    ValidateResponse,
)
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError
from husarz.orchestrator import Orchestrator, build_orchestrator
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
    prompts_dir: str | Path = "./prompts",
    api_token: str | None = None,
    api_role: str | None = None,
    rbac: Rbac | None = None,
    orchestrator_factory: Callable[[HusarzConfig], Orchestrator | None] | None = None,
    trusted_hosts: list[str] | None = None,
) -> FastAPI:
    """Buduje aplikację FastAPI dla podanej konfiguracji.

    ``api_token`` (opcjonalny) włącza uwierzytelnianie Bearer + RBAC. ``api_role``
    to rola przypisywana ważnemu tokenowi (domyślnie z ``security.auth.api_role``).
    ``orchestrator_factory`` pozwala PRZEBUDOWAĆ orkiestrator po nadpisaniu configu
    w runtime — bez niego ``/api/orchestrate`` działałby dalej na starej konfiguracji.
    """
    app = FastAPI(title="Husarz API", version=__version__)
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    audit_log = audit if audit is not None else build_audit_log(config.security)
    role = api_role if api_role is not None else config.security.auth.api_role
    authz = rbac if rbac is not None else Rbac()

    def _build_orch(cfg: HusarzConfig) -> Orchestrator | None:
        if orchestrator_factory is not None:
            return orchestrator_factory(cfg)
        if router is not None:
            return build_orchestrator(cfg, router, prompts_dir=prompts_dir)
        return None

    state: dict[str, Any] = {
        "config": config,
        "config_dir": config_dir,
        "orchestrator": _build_orch(config),
        "runtime_overrides": {},
        "orchestrations": 0,
        "failures": 0,
    }
    counter_lock = threading.Lock()

    def _authenticate(authorization: str | None) -> str | None:
        """Zwraca rolę ważnego tokenu; ``None`` gdy uwierzytelnianie wyłączone."""
        if api_token is None:
            return None  # brak tokenu = tryb dev (tylko loopback — patrz launcher)
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            raise HTTPException(status_code=401, detail="Wymagany token Bearer.")
        presented = authorization[len(prefix) :]
        # Porównanie w stałym czasie — brak wycieku przez różnicę czasu.
        if not hmac.compare_digest(presented.encode("utf-8"), api_token.encode("utf-8")):
            raise HTTPException(status_code=401, detail="Nieprawidłowy token.")
        return role

    def _require(permission: str) -> Callable[[str | None], None]:
        def dependency(authorization: str | None = Header(default=None)) -> None:
            principal = _authenticate(authorization)
            if principal is None:
                return  # uwierzytelnianie wyłączone (dev)
            if not authz.can(principal, permission):
                raise HTTPException(
                    status_code=403,
                    detail=f"Rola '{principal}' nie ma uprawnienia '{permission}'.",
                )

        return dependency

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
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
    )
    def config_summary() -> ConfigSummary:
        return _summary(state["config"])

    @app.get(
        "/api/agents",
        response_model=list[AgentInfo],
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
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
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
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
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
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
        dependencies=[Depends(_require(_PERM_AUDIT_READ))],
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
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
    )
    def usage() -> UsageResponse:
        current: HusarzConfig = state["config"]
        cost = current.routing.cost_controls
        return UsageResponse(
            orchestrations=state["orchestrations"],
            failures=state["failures"],
            max_tokens_per_request=cost.max_tokens_per_request,
            max_requests_per_minute=cost.max_requests_per_minute,
        )

    @app.post(
        "/api/orchestrate",
        response_model=OrchestrateResponse,
        dependencies=[Depends(_require(_PERM_AGENT_RUN))],
    )
    def orchestrate(request: OrchestrateRequest) -> OrchestrateResponse:
        orch: Orchestrator | None = state["orchestrator"]
        if orch is None:
            raise HTTPException(status_code=503, detail="Orkiestrator niedostępny (brak routera).")
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

    @app.post(
        "/api/config/validate",
        response_model=ValidateResponse,
        dependencies=[Depends(_require(_PERM_CONFIG_READ))],
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
        dependencies=[Depends(_require(_PERM_CONFIG_WRITE))],
    )
    def config_apply(request: ValidateRequest) -> ValidateResponse:
        cdir = state["config_dir"]
        if cdir is None:
            return ValidateResponse(ok=False, error="Nadpisania wymagają katalogu konfiguracji.")
        try:
            merged = load_config(cdir, runtime_overrides=request.overrides)
        except ConfigError as exc:
            return ValidateResponse(ok=False, error=str(exc))
        state["config"] = merged
        state["runtime_overrides"] = request.overrides
        # Przebuduj orkiestrator, by /api/orchestrate używał NOWEJ konfiguracji.
        state["orchestrator"] = _build_orch(merged)
        audit_log.record("api", "config.runtime_override", {"keys": sorted(request.overrides)})
        return ValidateResponse(ok=True, summary=_summary(merged))

    @app.get("/")
    def console() -> FileResponse:
        return FileResponse(_STATIC_DIR / "console.html", media_type="text/html")

    return app


def _record_failure(
    state: dict[str, Any],
    counter_lock: threading.Lock,
    audit_log: AuditLog,
    reason: str,
) -> None:
    """Liczy porażkę i zapisuje audyt (bez surowej treści błędu — tylko kategoria)."""
    with counter_lock:
        state["failures"] += 1
    audit_log.record("api", "orchestrate.error", {"error": reason})
