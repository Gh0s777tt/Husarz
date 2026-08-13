"""REST API rdzenia Husarza (FastAPI).

``create_app`` składa aplikację z konfiguracji. Router modeli, audyt i katalog
promptów są wstrzykiwalne — dzięki temu API testuje się przez ``TestClient`` bez
uruchamiania serwera ani połączeń sieciowych.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
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
from husarz.security.audit import AuditLog, build_audit_log

_STATIC_DIR = Path(__file__).parent / "static"


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
) -> FastAPI:
    """Buduje aplikację FastAPI dla podanej konfiguracji."""
    app = FastAPI(title="Husarz API", version=__version__)
    audit_log = audit if audit is not None else build_audit_log(config.security)
    orchestrator: Orchestrator | None = None
    if router is not None:
        orchestrator = build_orchestrator(config, router, prompts_dir=prompts_dir)

    state: dict[str, Any] = {
        "config": config,
        "config_dir": config_dir,
        "orchestrator": orchestrator,
        "runtime_overrides": {},
        "orchestrations": 0,
    }

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        current: HusarzConfig = state["config"]
        return HealthResponse(
            status="ok", version=__version__, profile=current.platform.profile.value
        )

    @app.get("/api/config/summary", response_model=ConfigSummary)
    def config_summary() -> ConfigSummary:
        return _summary(state["config"])

    @app.get("/api/agents", response_model=list[AgentInfo])
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

    @app.get("/api/models", response_model=list[ModelInfo])
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

    @app.get("/api/tools", response_model=list[ToolInfo])
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

    @app.get("/api/audit", response_model=AuditView)
    def audit_view(limit: int = 50) -> AuditView:
        entries = audit_log.entries
        recent = entries[-limit:] if limit > 0 else entries
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

    @app.get("/api/usage", response_model=UsageResponse)
    def usage() -> UsageResponse:
        current: HusarzConfig = state["config"]
        cost = current.routing.cost_controls
        return UsageResponse(
            orchestrations=state["orchestrations"],
            max_tokens_per_request=cost.max_tokens_per_request,
            max_requests_per_minute=cost.max_requests_per_minute,
        )

    @app.post("/api/orchestrate", response_model=OrchestrateResponse)
    def orchestrate(request: OrchestrateRequest) -> OrchestrateResponse:
        orch: Orchestrator | None = state["orchestrator"]
        if orch is None:
            raise HTTPException(status_code=503, detail="Orkiestrator niedostępny (brak routera).")
        audit_log.record("api", "orchestrate", {"task": request.task[:200]})
        result = orch.run(request.task)
        state["orchestrations"] += 1
        return OrchestrateResponse(
            task=result.task,
            answer=result.answer,
            rounds=result.rounds,
            observations=[
                ObservationView(agent=o.agent, output=o.output, model=o.model)
                for o in result.observations
            ],
        )

    @app.post("/api/config/validate", response_model=ValidateResponse)
    def config_validate(request: ValidateRequest) -> ValidateResponse:
        cdir = state["config_dir"]
        if cdir is None:
            return ValidateResponse(ok=False, error="Walidacja wymaga katalogu konfiguracji.")
        try:
            merged = load_config(cdir, runtime_overrides=request.overrides)
        except ConfigError as exc:
            return ValidateResponse(ok=False, error=str(exc))
        return ValidateResponse(ok=True, summary=_summary(merged))

    @app.post("/api/config/runtime", response_model=ValidateResponse)
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
        audit_log.record("api", "config.runtime_override", {"keys": sorted(request.overrides)})
        return ValidateResponse(ok=True, summary=_summary(merged))

    @app.get("/")
    def console() -> FileResponse:
        return FileResponse(_STATIC_DIR / "console.html", media_type="text/html")

    return app
