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

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from husarz import __version__
from husarz.accounts import AccountService, Principal
from husarz.accounts.errors import (
    AccountError,
    AccountLockedError,
    AuthenticationError,
    QuotaExceededError,
    RegistrationDisabledError,
)
from husarz.agents.base import SupportsComplete
from husarz.agents.tool_loop import ToolLoop, build_tool_loop
from husarz.api.schemas import (
    AgentInfo,
    AuditEntryView,
    AuditView,
    AuthToken,
    ChatReply,
    ChatRequest,
    ConfigSummary,
    GitConnectionIn,
    GitConnectionView,
    HealthResponse,
    LoginRequest,
    MeResponse,
    ModelInfo,
    ObservationView,
    OrchestrateRequest,
    OrchestrateResponse,
    PluginView,
    PullRequestIn,
    PullRequestView,
    RegisterRequest,
    RemoteToolView,
    RepoView,
    ToolInfo,
    UsageResponse,
    ValidateRequest,
    ValidateResponse,
)
from husarz.attachments import (
    AttachmentError,
    build_context_block,
    sanitize_attachments,
    sanitize_images,
)
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError
from husarz.config.secrets import SecretsProvider
from husarz.git import GitConnection, GitProviderKind, GitService
from husarz.git.errors import GitAuthError, GitConnectionError, GitError
from husarz.orchestrator import Orchestrator, build_orchestrator
from husarz.plugins import PluginService
from husarz.plugins.errors import (
    PluginAuthError,
    PluginDisabledError,
    PluginError,
    PluginNotFoundError,
    PluginSecretError,
)
from husarz.router import ChatMessage, ImagePart, Usage
from husarz.router import ChatRequest as RouterChatRequest
from husarz.router.egress import EgressError
from husarz.router.errors import (
    NoModelAvailableError,
    RateLimitExceededError,
    RouterError,
)
from husarz.security.audit import AuditLog, build_audit_log
from husarz.security.errors import AuditError
from husarz.security.rbac import Rbac

_STATIC_DIR = Path(__file__).parent / "static"


class BodySizeLimitMiddleware:
    """Twardy limit rozmiaru ciała żądania (ochrona przed OOM/DoS) — czyste ASGI.

    Egzekwuje limit DWUTOROWO, bo kontrola oparta wyłącznie na nagłówku ``Content-Length``
    jest omijana przez ``Transfer-Encoding: chunked`` (żądanie bez ``Content-Length``):

    1. szybka ścieżka — zadeklarowany ``Content-Length`` ponad limit → ``413`` bez czytania ciała;
    2. bufor z twardym sufitem — middleware sam czyta ciało, licząc bajty, i przerywa z ``413``
       PRZED przekazaniem żądania do aplikacji, gdy tylko suma przekroczy limit; działa także
       dla żądań chunked bez ``Content-Length``. Chunk, który przekroczyłby limit, NIE jest
       doklejany do bufora (sufit pamięci ≈ ``max_bytes`` + jeden bufor odczytu serwera).

    Ciało jest buforowane i ODTWARZANE dla aplikacji (wszystkie endpointy czytają ciało
    w całości — JSON — więc utrata strumieniowania nie ma znaczenia). Dzięki temu 413 jest
    czyste (aplikacja nie zdąży zamienić wyjątku na 400 „error parsing the body").
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        content_length = Headers(scope=scope).get("content-length")
        if (
            content_length is not None
            and content_length.isdigit()
            and int(content_length) > self._max_bytes
        ):
            await self._reject(scope, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                # http.disconnect (klient zerwał połączenie) — kończymy buforowanie.
                break
            chunk = message.get("body", b"")
            # Sprawdzamy PRZED doklejeniem — pojedynczy nadmiarowy chunk nie wejdzie do pamięci.
            if len(body) + len(chunk) > self._max_bytes:
                await self._reject(scope, send)
                return
            body += chunk
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return {"type": "http.disconnect"}

        await self._app(scope, replay_receive, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        response = JSONResponse({"detail": "Żądanie przekracza limit rozmiaru."}, status_code=413)
        await response(scope, _noop_receive, send)


async def _noop_receive() -> Message:
    """Pusty ``receive`` dla odpowiedzi 413 (odsyłamy bez czytania reszty ciała)."""
    return {"type": "http.disconnect"}


# Uprawnienia RBAC wymagane per endpoint (obszar:akcja — patrz husarz.security.rbac).
_PERM_CONFIG_READ = "config:read"
_PERM_CONFIG_WRITE = "config:write"
_PERM_AGENT_RUN = "agent:run"
_PERM_AUDIT_READ = "audit:read"
_PERM_GIT_READ = "git:read"
_PERM_GIT_WRITE = "git:write"
_PERM_GIT_PR = "git:pr"
_PERM_PLUGIN_READ = "plugin:read"


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
    git_service: GitService | None = None,
    git_service_factory: Callable[[HusarzConfig], GitService | None] | None = None,
    plugin_service: PluginService | None = None,
    plugin_service_factory: Callable[[HusarzConfig], PluginService | None] | None = None,
    chat_model: str | None = None,
    trusted_hosts: list[str] | None = None,
    secrets: SecretsProvider | None = None,
) -> FastAPI:
    """Buduje aplikację FastAPI dla podanej konfiguracji.

    ``api_token`` (opcjonalny) włącza uwierzytelnianie Bearer + RBAC. ``api_role``
    to rola przypisywana ważnemu tokenowi (domyślnie z ``security.auth.api_role``).
    ``router_factory`` pozwala PRZEBUDOWAĆ router+orkiestrator po nadpisaniu configu
    w runtime — bez niego ``/api/orchestrate`` i ``/api/chat`` działałyby na starej
    konfiguracji. ``chat_model`` nadpisuje model trybu czatu (domyślnie z configu).
    """
    # Pusty/whitespace token maszynowy traktujemy jak brak — inaczej „Bearer " (pusty)
    # mógłby się zrównać z pustym api_token (compare_digest(b"", b"")==True).
    api_token = api_token.strip() if api_token and api_token.strip() else None

    app = FastAPI(title="Husarz API", version=__version__)
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    # Twardy limit rozmiaru ciała (ochrona OOM/DoS przy dużych załącznikach/obrazach).
    # Czyste ASGI: egzekwuje limit też strumieniowo, więc 'Transfer-Encoding: chunked'
    # (żądanie bez Content-Length) NIE omija kontroli — patrz BodySizeLimitMiddleware.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=config.chat.max_request_bytes)

    @app.exception_handler(AuditError)
    async def _audit_error_handler(request: Request, exc: AuditError) -> JSONResponse:
        # Niezapisywalny audyt (np. read-only CWD binarki) → czytelne 503, nie surowe 500.
        # Audyt jest twardym wymogiem — fail-closed: akcja nie „udaje się" bez zapisu.
        return JSONResponse(
            {"detail": "Audyt niedostępny (błąd zapisu dziennika). Sprawdź uprawnienia katalogu."},
            status_code=503,
        )

    audit_log = audit if audit is not None else build_audit_log(config.security)
    role = api_role if api_role is not None else config.security.auth.api_role
    authz = rbac if rbac is not None else Rbac()

    def _active_git(cfg: HusarzConfig) -> GitService | None:
        # Serwis Git PRZEBUDOWANY z bieżącego configu (jak router i wtyczki) — inaczej
        # nadpisanie runtime nie propagowałoby polityki egress na JEDYNĄ ścieżkę wychodzącą
        # niosącą token z prawem ZAPISU do repozytoriów (fail-open kill-switch: przełączenie
        # na profil `airgap` nie blokowałoby Gita aż do restartu). Magazyn połączeń jest
        # PRZEKAZYWANY dalej, więc przebudowa nie gubi połączeń dodanych przez API.
        if git_service_factory is None:
            return git_service
        return git_service_factory(cfg)

    def _active_plugins(cfg: HusarzConfig) -> PluginService | None:
        # Serwis wtyczek PRZEBUDOWANY z bieżącego configu (jak router) — inaczej nadpisanie
        # runtime nie propagowałoby polityki konektora (allow_call/call_allowlist/enabled/egress),
        # dając fail-open kill-switch. Bez fabryki: statyczny serwis (testy/back-compat).
        return plugin_service_factory(cfg) if plugin_service_factory is not None else plugin_service

    def _build_stack(
        cfg: HusarzConfig,
    ) -> tuple[
        SupportsComplete | None,
        Orchestrator | None,
        PluginService | None,
        ToolLoop | None,
        GitService | None,
    ]:
        git = _active_git(cfg)
        plugins = _active_plugins(cfg)
        active = router_factory(cfg) if router_factory is not None else router
        if active is None:
            return None, None, plugins, None, git
        # Pętla narzędziowa: pierwszy egzekutor narzędzi. Zależności są leniwe
        # (executor/fetcher/rag budowane domyślnie), więc konstrukcja jest bezpieczna
        # bez Dockera — realne wykonanie potrzebują tylko agenci z opt-in (tool_loop_enabled).
        loop = build_tool_loop(
            cfg,
            workspace=cfg.platform.workspace_dir,
            audit=audit_log,
            secrets=secrets,
            data_dir=cfg.platform.data_dir,
            plugin_service=plugins,  # ten sam (świeży) serwis co /api/plugins — jedno źródło prawdy
        )
        orch = build_orchestrator(cfg, active, prompts_dir=prompts_dir, tool_loop=loop)
        return active, orch, plugins, loop, git

    def _resolve_chat_model(cfg: HusarzConfig) -> str:
        return chat_model or cfg.models.chat or cfg.models.default

    initial_router, initial_orch, initial_plugins, initial_loop, initial_git = _build_stack(config)
    state: dict[str, Any] = {
        "config": config,
        "config_dir": config_dir,
        "router": initial_router,
        "orchestrator": initial_orch,
        "plugin_service": initial_plugins,
        "git_service": initial_git,
        "tool_loop": initial_loop,
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
        # Guard na prawdziwości obu operandów: pusty token nigdy nie pasuje.
        if (
            api_token
            and presented
            and hmac.compare_digest(presented.encode("utf-8"), api_token.encode("utf-8"))
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
    dep_git_read = Depends(_require(_PERM_GIT_READ))
    dep_git_write = Depends(_require(_PERM_GIT_WRITE))
    dep_git_pr = Depends(_require(_PERM_GIT_PR))
    dep_plugin_read = Depends(_require(_PERM_PLUGIN_READ))

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
        messages = [ChatMessage(role=m.role, content=m.content) for m in request.messages]
        # Załączniki (NIEZAUFANE) → ogrodzony blok doklejany do bieżącej wiadomości.
        if request.attachments:
            try:
                atts = sanitize_attachments(
                    [(a.name, a.content) for a in request.attachments],
                    current_config.chat.attachments,
                )
            except AttachmentError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            context = build_context_block(atts)
            if context and messages:
                messages[-1].content = f"{context}\n\n{messages[-1].content}"
        # Obrazy (NIEZAUFANE, binarne) → wymagają modelu wizyjnego; sniff typu + limity.
        if request.images:
            spec = current_config.models.registry.get(model_id)
            if spec is None or not spec.vision:
                raise HTTPException(
                    status_code=400,
                    detail=f"Model '{model_id}' nie obsługuje obrazów — użyj modelu wizyjnego "
                    "(models: vision: true).",
                )
            try:
                imgs = sanitize_images(
                    [(im.name, im.data) for im in request.images], current_config.chat.images
                )
            except AttachmentError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # Obrazy wiąże się z OSTATNIĄ wiadomością użytkownika (nie ślepo z messages[-1] —
            # ostatnia mogłaby być 'assistant'/'system', gdzie backend wizyjny obraz zignoruje).
            target = next((m for m in reversed(messages) if m.role == "user"), None)
            if target is None:
                raise HTTPException(
                    status_code=400,
                    detail="Obrazy wymagają wiadomości użytkownika (rola 'user').",
                )
            target.images = [ImagePart(mime=i.mime, data_b64=i.data_b64) for i in imgs]
        chat_request = RouterChatRequest(messages=messages, temperature=request.temperature)
        audit_log.record(
            "api",
            "chat",
            {
                "model": model_id,
                "turns": len(request.messages),
                "attachments": len(request.attachments),
                "images": len(request.images),
            },
        )
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
        # Profil jest KOTWICĄ bazowej linii bezpieczeństwa (_cross_validate wymusza na jego
        # podstawie sandbox, audyt, szyfrowanie i weryfikację podpisu ROE). Gdyby dało się go
        # nadpisać przez API, wystarczyłoby jedno żądanie `{"platform": {"profile": "dev"}}`,
        # by zdegradować prod/airgap i wyłączyć wszystkie te wymagania naraz. Profil zmienia
        # się wyłącznie przez konfigurację startową (plik/ENV/`husarz up --profile`).
        if "profile" in (request.overrides.get("platform") or {}):
            return ValidateResponse(
                ok=False,
                error=(
                    "Nadpisanie 'platform.profile' w runtime jest zabronione — profil kotwiczy "
                    "bazową linię bezpieczeństwa (sandbox, audyt, szyfrowanie, podpis ROE). "
                    "Zmień profil w konfiguracji startowej i uruchom ponownie."
                ),
            )
        try:
            merged = load_config(cdir, runtime_overrides=request.overrides)
        except ConfigError as exc:
            return ValidateResponse(ok=False, error=str(exc))
        # Przebuduj router+orkiestrator, by /api/orchestrate i /api/chat używały
        # NOWEJ konfiguracji (a nie starej sprzed nadpisania). Budowa poza zamkiem,
        # atomowa podmiana pod zamkiem — spójna para (config, router) dla /api/chat.
        new_router, new_orch, new_plugins, new_loop, new_git = _build_stack(merged)
        with counter_lock:
            state["config"] = merged
            state["runtime_overrides"] = request.overrides
            state["router"] = new_router
            state["orchestrator"] = new_orch
            state["plugin_service"] = new_plugins
            state["git_service"] = new_git
            old_loop = state["tool_loop"]
            state["tool_loop"] = new_loop
        # Zamknij STARĄ pętlę (zwalnia np. połączenie sqlite RAG) PO atomowej podmianie —
        # inaczej każde nadpisanie runtime wyciekałoby uchwyt pliku (limitacja z Etapu 14b).
        # Bezpieczne wobec żądań w locie: magazyn sqlite serializuje operacje własnym zamkiem,
        # więc close() czeka na bieżącą operację; ewentualne późniejsze użycie starej pętli
        # degraduje do ToolResult(ok=False), nie wywala żądania.
        if old_loop is not None:
            old_loop.close()
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
        except AccountLockedError as exc:
            # Nieudane próby są audytowane (w tym blokada) — inaczej niż sukces.
            audit_log.record("api", "auth.login.locked", {"username": request.username[:64]})
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AuthenticationError as exc:
            audit_log.record("api", "auth.login.failed", {"username": request.username[:64]})
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

    # --- Integracje Git (GitHub/GitLab): połączenia, repozytoria, tworzenie PR ---

    def _require_git() -> GitService:
        # Czyta BIEŻĄCY serwis ze stanu (przebudowywany przy nadpisaniu runtime), nie domknięcie
        # — inaczej zmiana polityki egress nie obowiązywałaby aż do restartu (fail-open).
        svc: GitService | None = state.get("git_service")
        if svc is None:
            raise HTTPException(status_code=404, detail="Integracje Git nie są włączone.")
        return svc

    @app.get(
        "/api/git/connections",
        response_model=list[GitConnectionView],
        dependencies=[dep_git_read],
    )
    def git_connections() -> list[GitConnectionView]:
        svc = _require_git()
        return [
            GitConnectionView(
                name=c.name,
                provider=c.provider.value,
                api_base=c.api_base,
                username=c.username,
                token_ref=c.token_ref,
            )
            for c in svc.list_connections()
        ]

    @app.post(
        "/api/git/connections",
        response_model=GitConnectionView,
        dependencies=[dep_git_write],
    )
    def git_add_connection(request: GitConnectionIn) -> GitConnectionView:
        svc = _require_git()
        conn = GitConnection(
            name=request.name,
            provider=GitProviderKind(request.provider),
            api_base=request.api_base,
            token_ref=request.token_ref,
            username=request.username,
        )
        try:
            svc.add(conn)
        except GitConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        audit_log.record(
            "api", "git.connection.add", {"name": conn.name, "provider": conn.provider}
        )
        return GitConnectionView(
            name=conn.name,
            provider=conn.provider.value,
            api_base=conn.api_base,
            username=conn.username,
            token_ref=conn.token_ref,
        )

    @app.delete("/api/git/connections/{name}", dependencies=[dep_git_write])
    def git_remove_connection(name: str) -> dict[str, bool]:
        _require_git().remove(name)
        audit_log.record("api", "git.connection.remove", {"name": name[:64]})
        return {"ok": True}

    @app.get(
        "/api/git/connections/{name}/repos",
        response_model=list[RepoView],
        dependencies=[dep_git_read],
    )
    def git_repos(name: str) -> list[RepoView]:
        provider = _git_provider(_require_git(), name)
        try:
            repos = provider.list_repositories()
        except (GitError, EgressError) as exc:
            raise _git_http_error(exc) from exc
        return [
            RepoView(
                full_name=r.full_name, default_branch=r.default_branch, private=r.private, url=r.url
            )
            for r in repos
        ]

    @app.post(
        "/api/git/connections/{name}/pull-request",
        response_model=PullRequestView,
        dependencies=[dep_git_pr],
    )
    def git_pull_request(name: str, request: PullRequestIn) -> PullRequestView:
        # Audyt PRÓBY przed budową dostawcy — także odrzucenia egress/nieznane połączenie.
        audit_log.record(
            "api",
            "git.pull_request",
            {"connection": name[:64], "repo": request.repo[:128], "base": request.base[:128]},
        )
        provider = _git_provider(_require_git(), name)
        try:
            pr = provider.create_pull_request(
                request.repo,
                title=request.title,
                head=request.head,
                base=request.base,
                body=request.body,
            )
        except (GitError, EgressError) as exc:
            raise _git_http_error(exc) from exc
        return PullRequestView(number=pr.number, url=pr.url, title=pr.title)

    # --- Wtyczki (konektory MCP): lista + odkrywanie narzędzi (discover) ---

    def _require_plugins() -> PluginService:
        # Czyta BIEŻĄCY serwis ze stanu (przebudowywany przy nadpisaniu runtime), nie domknięcie.
        svc: PluginService | None = state.get("plugin_service")
        if svc is None:
            raise HTTPException(status_code=404, detail="Wtyczki nie są włączone.")
        return svc

    @app.get(
        "/api/plugins",
        response_model=list[PluginView],
        dependencies=[dep_plugin_read],
    )
    def plugins_list() -> list[PluginView]:
        svc = _require_plugins()
        return [
            PluginView(
                name=p.name,
                transport=p.transport,
                endpoint=p.endpoint,
                description=p.description,
                enabled=p.enabled,
                token_ref=p.token_ref,
                timeout_seconds=p.timeout_seconds,
                max_output_bytes=p.max_output_bytes,
            )
            for p in svc.list_plugins()
        ]

    @app.get(
        "/api/plugins/{name}/tools",
        response_model=list[RemoteToolView],
        dependencies=[dep_plugin_read],
    )
    def plugin_tools(name: str) -> list[RemoteToolView]:
        svc = _require_plugins()
        # Audyt PRÓBY przed wyjściem na zewnątrz — także odrzucenia egress/nieznana wtyczka.
        audit_log.record("api", "plugin.discover", {"plugin": name[:64]})
        try:
            tools = svc.discover(name)
        except (PluginError, EgressError) as exc:
            raise _plugin_http_error(exc) from exc
        return [RemoteToolView(name=t.name, description=t.description) for t in tools]

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


def _git_http_error(exc: Exception) -> HTTPException:
    """Mapuje wyjątki Git na kody HTTP (nieznane połączenie/egress/upstream)."""
    if isinstance(exc, GitConnectionError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, EgressError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, GitAuthError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=502, detail="Błąd dostawcy Git.")


def _git_provider(svc: GitService, name: str) -> Any:
    """Buduje klienta dostawcy dla połączenia, mapując błędy na HTTP."""
    try:
        return svc.provider_for(name)
    except (GitError, EgressError) as exc:
        raise _git_http_error(exc) from exc


def _plugin_http_error(exc: Exception) -> HTTPException:
    """Mapuje wyjątki wtyczek na kody HTTP. Błędy transportu → generyczne 502 (bez wnętrzności)."""
    if isinstance(exc, PluginNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PluginDisabledError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, EgressError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, PluginSecretError):
        # Lokalna błędna konfiguracja sekretu (nie wina serwera) → 500 z jasną przyczyną.
        return HTTPException(
            status_code=500,
            detail="Nie udało się rozwiązać referencji sekretu wtyczki (sprawdź token_ref).",
        )
    if isinstance(exc, PluginAuthError):
        return HTTPException(
            status_code=502, detail="Autoryzacja u serwera wtyczki nie powiodła się."
        )
    # PluginTransportError / PluginError (bazowy) — generyczny komunikat, bez URL/wnętrzności.
    return HTTPException(status_code=502, detail="Błąd serwera wtyczki.")
