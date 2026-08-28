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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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
from husarz.api.audit_view import public_detail
from husarz.api.schemas import (
    AgentInfo,
    AuditEntryView,
    AuditView,
    AuthToken,
    ChatReply,
    ChatRequest,
    ConfigSummary,
    DoctorFinding,
    DoctorReport,
    GitConnectionIn,
    GitConnectionView,
    GitConnectionWizardIn,
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
    SecretEntryView,
    SecretStoreStatusView,
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
from husarz.git.client import build_ssl_context
from husarz.git.errors import GitAuthError, GitConnectionError, GitError
from husarz.launcher.doctor import Sonda, SondaSystemowa, Stan, Waga, zdiagnozuj
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
from husarz.router.rate_limit import RateLimiter
from husarz.router.selection import resolve_agent_model
from husarz.runs import build_run_store_from_config
from husarz.security.audit import AuditLog, build_audit_log
from husarz.security.errors import AuditError, SecurityError
from husarz.security.rbac import Rbac
from husarz.security.roe_runtime import build_roe_runtime
from husarz.security.secret_store import SCHEME as SECRET_SCHEME
from husarz.security.secret_store import EncryptedFileSecretStore, SecretStoreError

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
_PERM_DIAGNOSTICS_READ = "diagnostics:read"


# Pola, których nadpisanie w runtime NIE MOŻE odnieść skutku, bo odpowiadający im obiekt
# powstaje RAZ przy starcie i nie podlega przebudowie. Endpoint musi je ODRZUCIĆ, a nie
# odpowiadać `ok: true` na zmianę, której nie zastosował.
#
# Dlaczego nie „po prostu przebudować": magazyn sekretów i dziennik audytu wymagają zasobów
# rozwiązywalnych zwykle wyłącznie w procesie launchera (klucz główny ze środowiska, prawa
# do katalogu). Nieudana odbudowa w trakcie obsługi żądania zostawiłaby aplikację bez
# działającego audytu albo bez magazynu — czyli gorzej niż przed zmianą. Odmowa z czytelnym
# komunikatem jest tu uczciwsza niż cicha zmiana, która nie działa, ORAZ bezpieczniejsza niż
# odbudowa, która może zawieść w połowie.
#
# Sprawdzone empirycznie: przed tą bramką nadpisanie `secret_store.path`, `secret_store.key_ref`
# oraz `audit.path` kończyło się `ok: true`, po czym token trafiał do STAREJ ścieżki starym
# kluczem, a dziennik pisał do starego pliku. Nowe pliki nie powstawały nigdy.
_NIEZMIENNE_W_RUNTIME: tuple[tuple[str, ...], ...] = (
    ("security", "secret_store", "path"),
    ("security", "secret_store", "key_ref"),
    ("security", "audit"),
)


def _pobierz(config: HusarzConfig, sciezka: tuple[str, ...]) -> Any:
    """Odczytuje zagnieżdżone pole konfiguracji po ścieżce atrybutów."""
    wartosc: Any = config
    for czlon in sciezka:
        wartosc = getattr(wartosc, czlon, None)
    return wartosc


def _martwe_zmiany(startowa: HusarzConfig, nowa: HusarzConfig) -> list[str]:
    """Zwraca pola, których nadpisanie ZMIENIA, a które nie mogą odnieść skutku.

    Punktem odniesienia jest konfiguracja **z chwili startu**, a nie bieżąca — to z niej
    zbudowano magazyn sekretów i dziennik audytu, więc to jej wartości opisują stan, w którym
    te obiekty faktycznie działają.

    Porównujemy WARTOŚCI, nie obecność klucza w żądaniu: nadpisanie powtarzające dotychczasową
    wartość jest nieszkodliwe i musi przejść. Pola magazynu sekretów sprawdzamy WYŁĄCZNIE gdy
    magazyn ma pozostać włączony — przy wyłączaniu ścieżka i klucz przestają mieć znaczenie,
    a wymaganie ich powtórzenia byłoby uciążliwe bez żadnego zysku.

    Args:
        startowa: Konfiguracja, z której zbudowano obiekty przy starcie.
        nowa: Konfiguracja po scaleniu nadpisań.

    Returns:
        Posortowana lista ścieżek w zapisie kropkowym; pusta, gdy wszystko da się zastosować.
    """
    magazyn_pozostaje_wlaczony = bool(nowa.security.secret_store.enabled)
    znalezione: list[str] = []
    for sciezka in _NIEZMIENNE_W_RUNTIME:
        if sciezka[:2] == ("security", "secret_store") and not magazyn_pozostaje_wlaczony:
            continue
        if _pobierz(startowa, sciezka) != _pobierz(nowa, sciezka):
            znalezione.append(".".join(sciezka))
    return sorted(znalezione)


def _principal_ref(principal: Principal | None) -> str:
    """Stabilna referencja wywołującego do audytu — ID konta, NIE nazwa użytkownika.

    Dziennik audytu jest niemodyfikowalny, więc nie wkładamy do niego danych, które mogą
    być PII (nazwa bywa e-mailem). Identyfikator konta jest losowy i wystarcza do
    powiązania wpisu z użytkownikiem przez magazyn kont. Token maszynowy nie ma konta —
    zapisujemy wtedy samą rolę, żeby odróżnić go od wywołania użytkownika.
    """
    if principal is None:
        return ""
    if principal.user_id:
        return f"user:{principal.user_id}"
    return f"token:{principal.role}"


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
    git_service_factory: Callable[[HusarzConfig, Any], GitService | None] | None = None,
    plugin_service: PluginService | None = None,
    plugin_service_factory: Callable[[HusarzConfig], PluginService | None] | None = None,
    chat_model: str | None = None,
    trusted_hosts: list[str] | None = None,
    secrets: SecretsProvider | None = None,
    secret_store: EncryptedFileSecretStore | None = None,
    listen_host: str = "127.0.0.1",
    listen_port: int = 8000,
    doctor_probe: Sonda | None = None,
) -> FastAPI:
    """Buduje aplikację FastAPI dla podanej konfiguracji.

    ``api_token`` (opcjonalny) włącza uwierzytelnianie Bearer + RBAC. ``api_role``
    to rola przypisywana ważnemu tokenowi (domyślnie z ``security.auth.api_role``).
    ``router_factory`` pozwala PRZEBUDOWAĆ router+orkiestrator po nadpisaniu configu
    w runtime — bez niego ``/api/orchestrate`` i ``/api/chat`` działałyby na starej
    konfiguracji. ``chat_model`` nadpisuje model trybu czatu (domyślnie z configu).

    ``listen_host``/``listen_port`` mówią aplikacji, GDZIE faktycznie stanie serwer.
    Używa tego wyłącznie diagnoza (``GET /api/doctor``), żeby wykryć model celujący
    w port zajęty przez samego Husarza. Świadomie NIE bierzemy tego z nagłówka ``Host``
    żądania: nagłówek pochodzi od klienta, więc kontrola bezpieczeństwa oparta na nim
    dawałaby wynik sterowany przez pytającego.

    ``doctor_probe`` podmienia sondę diagnozy (sieć, system plików). Wstrzykiwalność jest
    tu wymogiem, nie wygodą: moduł diagnozy jest w całości testowalny offline WŁAŚNIE
    dlatego, że sonda przychodzi z zewnątrz — API zaszywające ``SondaSystemowa`` na sztywno
    odebrałoby tę własność wszystkiemu, co idzie przez HTTP. Domyślnie (``None``) sonda jest
    budowana per żądanie z AKTUALNEJ konfiguracji.
    """
    # Pusty/whitespace token maszynowy traktujemy jak brak — inaczej „Bearer " (pusty)
    # mógłby się zrównać z pustym api_token (compare_digest(b"", b"")==True).
    api_token = api_token.strip() if api_token and api_token.strip() else None

    # Sprzeczność wykrywana przy KONSTRUKCJI, nie po cichu tolerowana. Wstrzyknięty magazyn
    # przy wyłączonym `security.secret_store` byłby parametrem martwym: kreator i tak
    # odmawiałby, bo bramka czyta konfigurację. Cicho ignorowany parametr to dokładnie ta
    # klasa pułapki, która w tym projekcie już raz kosztowała — `internal: true` w compose
    # bezgłośnie wyłączało publikowanie portów, a testy utrwalały sprzeczność zamiast ją
    # wykryć. Zmiana konfiguracji W RUNTIME jest czym innym i pozostaje dozwolona: wtedy
    # operator ŚWIADOMIE wyłącza zapis, a instancja zostaje, by ponowne włączenie działało
    # bez restartu.
    if secret_store is not None and not config.security.secret_store.enabled:
        raise ValueError(
            "Przekazano magazyn sekretów, ale konfiguracja ma security.secret_store.enabled=false "
            "— parametr byłby martwy (kreator i tak odmówi). Włącz go w konfiguracji albo nie "
            "przekazuj magazynu."
        )

    app = FastAPI(title="Husarz API", version=__version__)
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    # Twardy limit rozmiaru ciała (ochrona OOM/DoS przy dużych załącznikach/obrazach).
    # Czyste ASGI: egzekwuje limit też strumieniowo, więc 'Transfer-Encoding: chunked'
    # (żądanie bez Content-Length) NIE omija kontroli — patrz BodySizeLimitMiddleware.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=config.chat.max_request_bytes)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Zwraca błąd walidacji BEZ pola ``input`` — inaczej API odsyła to, co dostało.

        **Po co ten handler istnieje.** Domyślna obsługa w FastAPI zwraca ``exc.errors()``,
        a każdy wpis niesie pole ``input`` z ODRZUCONĄ WARTOŚCIĄ. Dla endpointów przyjmujących
        materiał sekretu oznacza to, że token wraca w ciele odpowiedzi 422 — a stamtąd trafia
        do zakładki sieciowej przeglądarki, do pośredników logujących ciała odpowiedzi i do
        zgłoszeń błędów.

        Pole ``token`` w :class:`~husarz.api.schemas.GitConnectionWizardIn` celowo nie ma
        ograniczeń Pydantica właśnie po to, by nie dało się wywołać dla niego błędu walidacji.
        Zamykało to JEDEN wariant z wielu. Pozostałe pięć wychodzi bez żadnego ograniczenia na
        samym polu: brak innego wymaganego pola (``input`` = CAŁE ciało z tokenem), literówka
        w nazwie pola, ``Content-Type: application/x-www-form-urlencoded`` (zwykłe ``curl -d``
        — ``input`` = surowe ciało), ciało jako lista JSON, a przede wszystkim wklejenie
        surowego tokenu w pole ``token_ref`` na ``POST /api/git/connections``, gdzie walidator
        odrzuca wartość i odsyła ją w ``input``.

        Dlatego bramka jest tutaj, na poziomie CAŁEJ aplikacji, a nie przy pojedynczym polu:
        pojedyncze pole zamyka jedną drogę, handler zamyka wszystkie — także na endpointach,
        które dopiero powstaną.

        Zwracamy ``type``, ``loc`` i ``msg``: wołający wie, GDZIE i CO jest nie tak, ale nie
        dostaje z powrotem wartości, którą wysłał. ``ctx`` również pomijamy — dla walidatorów
        własnych niesie obiekt wyjątku, którego treści nie kontrolujemy w jednym miejscu.
        """
        bezpieczne = [
            {"type": e.get("type", ""), "loc": e.get("loc", []), "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
        return JSONResponse({"detail": jsonable_encoder(bezpieczne)}, status_code=422)

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

    # Ostatnio zbudowany serwis Git — źródło magazynu połączeń przy przebudowie.
    # Jednoelementowa lista, a nie zmienna: `_active_git` jest domknięciem definiowanym
    # PRZED `state`, więc musi mieć uchwyt, który da się podmienić w miejscu.
    _ostatni_git: list[GitService | None] = [git_service]

    def _active_git(cfg: HusarzConfig) -> GitService | None:
        # Serwis Git PRZEBUDOWANY z bieżącego configu (jak router i wtyczki) — inaczej
        # nadpisanie runtime nie propagowałoby polityki egress na JEDYNĄ ścieżkę wychodzącą
        # niosącą token z prawem ZAPISU do repozytoriów (fail-open kill-switch: przełączenie
        # na profil `airgap` nie blokowałoby Gita aż do restartu).
        #
        # Magazyn połączeń przekazujemy fabryce JAWNIE i bierzemy go z serwisu AKTUALNEGO,
        # nie z tego przekazanego przy starcie. Wcześniej fabryka launchera domykała na
        # `git_service` z chwili uruchomienia; gdy Git był wtedy WYŁĄCZONY (domyślnie jest),
        # domknięta wartość zostawała `None`, więc każda kolejna przebudowa tworzyła PUSTY
        # magazyn i kasowała połączenia dodane przez API. Przy włączonym magazynie sekretów
        # token zostawał wtedy na dysku jako sekret osierocony. Sprawdzone i odtworzone.
        if git_service_factory is None:
            return git_service
        biezacy = _ostatni_git[0]
        return git_service_factory(cfg, biezacy.store if biezacy is not None else None)

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
        # Runtime ROE budowany z BIEŻĄCEGO configu (jak router i wtyczki) — zmiana polityki
        # podpisu albo treści zlecenia obowiązuje bez restartu, a agent `roe_required` jest
        # delegowany wyłącznie pod zleceniem z ważnym podpisem (ADR-0021).
        roe_runtime = build_roe_runtime(cfg, audit_log, secrets=secrets)
        # Magazyn budowany tą SAMĄ fabryką co w pętli narzędziowej, więc obie ścieżki piszą
        # do tego samego PLIKU i rekordy łączą się po `run_id`. To dwie instancje, nie jeden
        # obiekt: każda ma własny zamek, więc atomowość opiera się na `O_APPEND` systemu
        # plików. Przy domyślnych limitach rekord ma ~1 KB i mieści się w jednym zapisie.
        orch = build_orchestrator(
            cfg,
            active,
            prompts_dir=prompts_dir,
            tool_loop=loop,
            roe_runtime=roe_runtime,
            runs=build_run_store_from_config(cfg, data_dir=cfg.platform.data_dir),
        )
        return active, orch, plugins, loop, git

    def _resolve_chat_model(cfg: HusarzConfig) -> str:
        return chat_model or cfg.models.chat or cfg.models.default

    initial_router, initial_orch, initial_plugins, initial_loop, initial_git = _build_stack(config)
    _ostatni_git[0] = initial_git
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
    # Limit tempa diagnozy. Budowany RAZ, z konfiguracji startowej: przebudowa po
    # `POST /api/config/runtime` zerowałaby kubełek, więc nadpisanie konfiguracji stałoby się
    # sposobem na obejście limitu. `None` = operator świadomie zrezygnował.
    _limit_diagnozy = (
        RateLimiter(config.security.diagnostics.max_requests_per_minute)
        if config.security.diagnostics.max_requests_per_minute is not None
        else None
    )
    _mutex_diagnozy = threading.Lock()

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
    dep_diagnostics_read = Depends(_require(_PERM_DIAGNOSTICS_READ))

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
                # Model EFEKTYWNY, a nie samo pole z pliku agenta: pierwszeństwo ma
                # `routing.agent_models`, więc panel musi liczyć go tą samą regułą co router.
                model=resolve_agent_model(current, name) or agent.model,
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
                    timestamp=e.timestamp,
                    actor=e.actor,
                    action=e.action,
                    roe_ref=e.roe_ref,
                    principal=e.principal,
                    # Allowlista, deny-by-default — pełny `detail` zostaje w dzienniku na dysku.
                    detail=public_detail(e.action, e.detail),
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

    @app.get("/api/doctor", response_model=DoctorReport)
    def doctor(principal: Principal | None = dep_diagnostics_read) -> DoctorReport:
        """Diagnoza instalacji — TA SAMA funkcja, którą wykonuje `husarz doctor`.

        Domykamy obietnicę z docstringa modułu diagnozy: jedno źródło prawdy dla CLI
        i konsoli. Gdyby panel liczył ustalenia po swojemu, oba nośniki rozjechałyby się
        w ocenie tej samej instalacji, a operator nie wiedziałby, któremu wierzyć.

        **Dlaczego osobne uprawnienie `diagnostics:read`, a nie `config:read`.**
        Odpowiedź niesie endpointy silników i ścieżki katalogów operatora, których
        `config:read` celowo nie wystawia, a samo wywołanie OTWIERA połączenia
        wychodzące. Rola `user` (zakładana samodzielną rejestracją) ma `config:read`,
        więc oparcie diagnozy na nim wystawiłoby jedno i drugie publicznie.

        **Tempo jest ograniczone** (`security.diagnostics.max_requests_per_minute`,
        domyślnie 6/min). Każde wywołanie otwiera połączenia wychodzące, więc bez limitu
        uprawnienie `diagnostics:read` byłoby dźwignią: żądanie tanie dla wywołującego,
        kosztowne dla instalacji i dla silników, do których się odzywamy. Limit sprawdzamy
        PRZED sondowaniem — chodzi o to, żeby nadmiarowe żądanie nie wygenerowało ruchu,
        a nie żeby dostało 429 po fakcie.

        **Dlaczego to nie jest skaner portów.** Sondowanie przechodzi przez tę samą
        bramkę egress, co ruch routera (:class:`SondaSystemowa`). Endpoint spoza
        allowlisty nie jest odpytywany — kontrola kończy się stanem `nieznany`
        z podaniem powodu. Bez tego wystarczyłoby wpisać dowolny adres jako endpoint
        modelu (`config:write`) i odczytać z diagnozy, czy odpowiada.

        Args:
            principal: Wywołujący (z RBAC) — do wpisu audytu.

        Returns:
            Ustalenia posortowane wg wagi wraz z licznikami policzonymi z TEJ SAMEJ listy.
        """
        # Limit tempa PRZED czymkolwiek innym: sedno sprawy jest w tym, żeby żądanie ponad
        # limit nie wygenerowało ruchu wychodzącego, a nie w tym, żeby dostało 429 na końcu.
        # `RateLimiter` nie jest bezpieczny wątkowo, a FastAPI wykonuje funkcje synchroniczne
        # w puli wątków — stąd zamek.
        if _limit_diagnozy is not None:
            with _mutex_diagnozy:
                try:
                    _limit_diagnozy.acquire()
                except RateLimitExceededError as exc:
                    raise HTTPException(
                        status_code=429,
                        detail=(
                            f"{exc} Diagnoza odpytuje silniki przy KAŻDYM wywołaniu, więc jej "
                            f"tempo jest ograniczone (security.diagnostics)."
                        ),
                    ) from exc

        current: HusarzConfig = state["config"]
        # Sonda budowana z AKTUALNEJ konfiguracji, nie z tej ze startu: po
        # `POST /api/config/runtime` obowiązuje nowa polityka egress i nowe endpointy.
        sonda = doctor_probe if doctor_probe is not None else SondaSystemowa(current)
        ustalenia = zdiagnozuj(current, sonda=sonda, host=listen_host, port=listen_port)
        blokujace = sum(1 for u in ustalenia if u.stan is Stan.PROBLEM and u.waga is Waga.BLOKUJACA)
        ostrzezenia = sum(
            1 for u in ustalenia if u.stan is Stan.PROBLEM and u.waga is not Waga.BLOKUJACA
        )
        nieznane = sum(1 for u in ustalenia if u.stan is Stan.NIEZNANY)
        # Audytujemy ODCZYT, bo ten odczyt wysyła pakiety: bez wpisu ruch wychodzący
        # wywołany przez API nie miałby śladu. W szczególe wyłącznie liczby — żadnych
        # endpointów ani ścieżek, bo dziennik audytu jest niemodyfikowalny.
        audit_log.record(
            "api",
            "doctor",
            {"blocking": blokujace, "warnings": ostrzezenia, "unknown": nieznane},
            principal=_principal_ref(principal),
        )
        return DoctorReport(
            findings=[
                DoctorFinding(
                    id=u.id,
                    state=u.stan.value,
                    severity=u.waga.value,
                    description=u.opis,
                    remedy=u.naprawa,
                )
                for u in ustalenia
            ],
            blocking=blokujace,
            warnings=ostrzezenia,
            unknown=nieznane,
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
        caller = _principal_ref(principal)
        audit_log.record("api", "orchestrate", {"task": request.task[:200]}, principal=caller)
        with counter_lock:
            state["orchestrations"] += 1
        try:
            result = orch.run(request.task, principal=caller)
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
        # Rozliczenie limitu: orkiestracja to WIELE wywołań modelu (plan + delegacje +
        # refleksja + synteza). Bez tego najdroższy endpoint sprawdzał limit, ale go nie
        # naliczał — konto z ustawioną kwotą mogło orkiestrować w nieskończoność.
        _record_tokens(accounts, principal, result.usage)
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
            principal=_principal_ref(principal),
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
    )
    def config_apply(
        request: ValidateRequest, principal: Principal | None = dep_config_write
    ) -> ValidateResponse:
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
        # Odmawiamy ZMIAN, których nie da się zastosować — zamiast odpowiadać `ok: true`
        # na zmianę, która nie zaszła. Bez tej bramki operator „przenosił" magazyn sekretów
        # na inny wolumen i rotował klucz główny, dostawał 200, a token szedł dalej do starej
        # ścieżki, zaszyfrowany starym kluczem; to samo dotyczyło ścieżki dziennika audytu.
        # Punkt odniesienia to konfiguracja STARTOWA (`config`), bo to z niej zbudowano
        # magazyn sekretów i dziennik audytu. Bieżąca konfiguracja mogła już przejść
        # przez wyłączenie magazynu, które gubi `key_ref` — porównanie z nią kazałoby
        # potem odmawiać ponownego włączenia z tym samym, poprawnym kluczem.
        martwe = _martwe_zmiany(config, merged)
        if martwe:
            return ValidateResponse(
                ok=False,
                error=(
                    "Nadpisanie w runtime nie może zmienić: "
                    + ", ".join(martwe)
                    + ". Te ustawienia są stosowane RAZ przy starcie (magazyn sekretów, "
                    "dziennik audytu), więc zmiana byłaby cicho nieskuteczna. Zmień je "
                    "w konfiguracji startowej i uruchom ponownie."
                ),
            )
        # Przebuduj router+orkiestrator, by /api/orchestrate i /api/chat używały
        # NOWEJ konfiguracji (a nie starej sprzed nadpisania). Budowa poza zamkiem,
        # atomowa podmiana pod zamkiem — spójna para (config, router) dla /api/chat.
        try:
            new_router, new_orch, new_plugins, new_loop, new_git = _build_stack(merged)
        except SecurityError as exc:
            # np. nadpisanie podnosi `consent`, a brak klucza weryfikującego podpis ROE.
            # Fail-closed: NIE stosujemy configu, ale zwracamy czytelny błąd zamiast 500.
            return ValidateResponse(ok=False, error=str(exc))
        with counter_lock:
            state["config"] = merged
            state["runtime_overrides"] = request.overrides
            state["router"] = new_router
            state["orchestrator"] = new_orch
            state["plugin_service"] = new_plugins
            state["git_service"] = new_git
            _ostatni_git[0] = new_git
            old_loop = state["tool_loop"]
            state["tool_loop"] = new_loop
        # Zamknij STARĄ pętlę (zwalnia np. połączenie sqlite RAG) PO atomowej podmianie —
        # inaczej każde nadpisanie runtime wyciekałoby uchwyt pliku (limitacja z Etapu 14b).
        # Bezpieczne wobec żądań w locie: magazyn sqlite serializuje operacje własnym zamkiem,
        # więc close() czeka na bieżącą operację; ewentualne późniejsze użycie starej pętli
        # degraduje do ToolResult(ok=False), nie wywala żądania.
        if old_loop is not None:
            old_loop.close()
        audit_log.record(
            "api",
            "config.runtime_override",
            {"keys": sorted(request.overrides)},
            principal=_principal_ref(principal),
        )
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
                ca_bundle=c.ca_bundle,
            )
            for c in svc.list_connections()
        ]

    # Zamek obejmujący operacje, które mutują DWA magazyny naraz: połączeń i sekretów.
    # Każdy z nich ma własną synchronizację wewnętrzną, ale to nie wystarcza — niebezpieczna
    # jest sekwencja „sprawdź, czy nazwa wolna → zapisz sekret → dodaj połączenie", bo między
    # sprawdzeniem a zapisem wciska się drugie żądanie. Bez tego zamka dwa równoległe żądania
    # kreatora o tej samej nazwie kończyły się tak: oba przechodzą pre-check, drugie NADPISUJE
    # sekret pierwszego, jego `add` zawodzi na kolizji, a sprzątanie kasuje token ZWYCIĘZCY.
    # Skutkiem jest połączenie bez działającego poświadczenia — dokładnie ta cicha utrata,
    # przed którą miał chronić pre-check. Ten sam wyścig zachodzi między kreatorem a DELETE.
    # Operacje są administracyjne i rzadkie, więc jeden zamek na całość jest wystarczający
    # i znacznie łatwiejszy do uzasadnienia niż zamki per nazwa.
    _mutex_polaczen = threading.Lock()

    @app.post("/api/git/connections", response_model=GitConnectionView)
    def git_add_connection(
        request: GitConnectionIn, principal: Principal | None = dep_git_write
    ) -> GitConnectionView:
        svc = _require_git()
        _sprawdz_ca(request.ca_bundle)
        conn = GitConnection(
            name=request.name,
            provider=GitProviderKind(request.provider),
            api_base=request.api_base,
            token_ref=request.token_ref,
            username=request.username,
            ca_bundle=request.ca_bundle,
        )
        # Ta droga też pod zamkiem. Sama w sobie dotyka jednego magazynu, ale wyścig zachodzi
        # z DELETE: usunięcie połączenia o tej samej nazwie sprząta sekret z przestrzeni
        # `husarz:git/<nazwa>`, więc równoległe dodanie mogło zostać z referencją wskazującą
        # na wpis skasowany chwilę później.
        try:
            with _mutex_polaczen:
                svc.add(conn)
        except GitConnectionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # `principal` jest OBOWIĄZKOWY dla wejścia poświadczenia: dziennik audytu jest
        # niemodyfikowalny, a wpis bez wywołującego nie odpowiada na pytanie „kto
        # wprowadził ten token" — czyli na jedyne pytanie, które przy incydencie ma
        # znaczenie. To ID konta, nie nazwa (brak PII w dzienniku).
        audit_log.record(
            "api",
            "git.connection.add",
            {"name": conn.name, "provider": conn.provider, "token_ref": conn.token_ref},
            principal=_principal_ref(principal),
        )
        return GitConnectionView(
            name=conn.name,
            provider=conn.provider.value,
            api_base=conn.api_base,
            username=conn.username,
            token_ref=conn.token_ref,
            ca_bundle=conn.ca_bundle,
        )

    # Maksymalna długość przyjmowanego tokenu. Nie jest to ograniczenie Pydantic (patrz
    # GitConnectionWizardIn) — sprawdzamy tu, żeby komunikat błędu NIE powtórzył wartości.
    _MAX_TOKEN_LEN = 4096

    def _sprawdz_ca(ca_bundle: str | None) -> None:
        """Waliduje ścieżkę do własnego CA już przy dodawaniu połączenia.

        Bez tego literówka w ścieżce ujawniłaby się dopiero przy pierwszej operacji, jako
        błąd TLS — komunikat, którego nikt nie powiąże z polem w formularzu.
        """
        if not ca_bundle:
            return
        try:
            build_ssl_context(ca_bundle)
        except GitError as exc:
            # Komunikat NIE powtarza wartości pola. `build_ssl_context` wpisuje ścieżkę do
            # swojego komunikatu (przydatne w logu operatora), ale w ODPOWIEDZI API byłoby to
            # echo wejścia — druga droga obok tej, którą zamknął handler walidacji. Pole jest
            # jedno, więc wskazanie go wystarcza wołającemu do naprawy.
            _ = exc
            raise HTTPException(
                status_code=400,
                detail=(
                    "Pole ca_bundle nie wskazuje czytelnego pliku PEM z certyfikatem urzędu. "
                    "Sprawdź, czy ścieżka istnieje, wskazuje zwykły plik i zawiera certyfikat "
                    "(katalog w stylu capath nie jest obsługiwany)."
                ),
            ) from exc

    def _magazyn_wlaczony() -> bool:
        """Czy zapis sekretów jest DZIŚ dozwolony — wg BIEŻĄCEJ konfiguracji.

        Dwa warunki, oba konieczne. Instancja magazynu musi istnieć (launcher zbudował ją
        przy starcie, bo tylko tam da się rozwiązać klucz główny) ORAZ bieżąca konfiguracja
        musi mieć go włączonego.

        Drugi warunek to poprawka fail-open: `POST /api/config/runtime` przebudowuje router,
        orkiestrator, wtyczki i serwis Gita, ale magazyn sekretów jest domknięciem z chwili
        startu i przebudowie nie podlega. Bez sprawdzania bieżącej konfiguracji wyłączenie
        `security.secret_store` w panelu kończyło się odpowiedzią `ok: true`, a kreator
        **nadal przyjmował i zapisywał tokeny** — kontrola bezpieczeństwa wyglądała na
        wyłączoną, będąc włączoną.

        Świadomie NIE przebudowujemy tu magazynu: klucz główny bywa rozwiązywalny wyłącznie
        ze środowiska procesu launchera, więc próba odtworzenia go w API mogłaby zawieść
        i zamienić „włącz z powrotem" w nieodwracalne wyłączenie do restartu. Instancja
        zostaje, zmienia się wyłącznie BRAMKA — dzięki temu ponowne włączenie działa od razu.
        """
        if secret_store is None:
            return False
        biezacy: HusarzConfig = state["config"]
        return biezacy.security.secret_store.enabled

    def _require_secret_store() -> EncryptedFileSecretStore:
        # Kreator wymaga ZAPISYWALNEGO magazynu. Bez niego jedyną drogą jest referencja do
        # źródła zewnętrznego — mówimy to wprost, zamiast po cichu zapisać token gdziekolwiek.
        if not _magazyn_wlaczony() or secret_store is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Magazyn sekretów jest wyłączony — kreator nie ma gdzie bezpiecznie "
                    "zapisać tokenu. Włącz security.secret_store (enabled + key_ref) albo "
                    "dodaj połączenie przez referencję (env:/file:/vault:/sops:)."
                ),
            )
        return secret_store

    @app.get(
        "/api/secrets/store",
        response_model=SecretStoreStatusView,
        dependencies=[dep_git_read],
    )
    def secret_store_status() -> SecretStoreStatusView:
        """Stan magazynu dla panelu: czy kreator jest dostępny i jakie wpisy istnieją.

        Zwraca WYŁĄCZNIE nazwy i znaczniki czasu. Wartości i szyfrogramy nie opuszczają
        procesu — panel nigdy nie ma powodu ich zobaczyć.
        """
        # Panel musi widzieć stan BIEŻĄCY, nie ten z chwili startu — inaczej po wyłączeniu
        # magazynu w runtime pokazywałby „włączony" i podpowiadał tryb, który już nie działa.
        if not _magazyn_wlaczony() or secret_store is None:
            return SecretStoreStatusView(enabled=False, entries=[])
        wpisy = [secret_store.describe(n) for n in secret_store.names()]
        return SecretStoreStatusView(
            enabled=True,
            entries=[SecretEntryView(**o) for o in wpisy if o is not None],
        )

    @app.post("/api/git/connections/wizard", response_model=GitConnectionView)
    def git_add_connection_with_token(
        request: GitConnectionWizardIn, principal: Principal | None = dep_git_write
    ) -> GitConnectionView:
        """Kreator: przyjmuje token, zapisuje go zaszyfrowany, tworzy połączenie z referencją.

        Kolejność operacji jest ISTOTNA. Najpierw sprawdzamy kolizję nazwy połączenia, potem
        zapisujemy sekret, na końcu tworzymy połączenie. Odwrotna kolejność zostawiałaby
        w magazynie osierocony sekret za każdym razem, gdy nazwa jest zajęta.
        """
        store = _require_secret_store()
        svc_wstepny = _require_git()
        # Trwałość obu magazynów MUSI być zgodna. Sekret idzie zawsze na dysk, więc przy
        # ULOTNYM magazynie połączeń każdy restart zostawiałby sekret osierocony: połączenie
        # znika, token zostaje. Odmawiamy z instrukcją, zamiast po cichu produkować śmieci,
        # o których operator dowie się dopiero po restarcie.
        if not svc_wstepny.store.persistent:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Magazyn połączeń jest ulotny (git.connections_path: null), a sekret "
                    "zapisujemy na dysk — po restarcie zostałby token bez połączenia. "
                    "Ustaw git.connections_path (np. ./data/git-connections.json) albo dodaj "
                    "połączenie przez referencję do sekretu, którym zarządzasz sam."
                ),
            )
        # PRZED zapisem sekretu: błędna ścieżka CA po zapisie zostawiłaby sekret osierocony.
        _sprawdz_ca(request.ca_bundle)
        token = request.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="Token jest pusty.")
        if len(request.token) > _MAX_TOKEN_LEN:
            # Komunikat celowo nie powtarza wartości ani jej fragmentu.
            raise HTTPException(
                status_code=400,
                detail=f"Token jest dłuższy niż {_MAX_TOKEN_LEN} znaków — to nie wygląda na token.",
            )
        svc = _require_git()
        nazwa_sekretu = f"git/{request.name}"
        conn = GitConnection(
            name=request.name,
            provider=GitProviderKind(request.provider),
            api_base=request.api_base,
            token_ref=f"{SECRET_SCHEME}{nazwa_sekretu}",
            username=request.username,
            ca_bundle=request.ca_bundle,
        )
        # CAŁA sekwencja pod jednym zamkiem — patrz uzasadnienie przy `_mutex_polaczen`.
        # Sam pre-check niczego nie gwarantuje: między sprawdzeniem a zapisem mieści się
        # drugie żądanie, a wtedy sprzątanie po przegranym kasuje token zwycięzcy.
        with _mutex_polaczen:
            # Bramkę sprawdzamy PONOWNIE, już pod zamkiem. Pierwsze sprawdzenie (przy
            # `_require_secret_store`) dzieje się poza nim, więc żądanie, które przeszło je
            # tuż przed wyłączeniem magazynu, zapisałoby token JUŻ PO tym wyłączeniu.
            # Okno jest wąskie, ale kontrola bezpieczeństwa nie może mieć okna „prawie
            # zamknięte" — a koszt to jedno porównanie pod zamkiem, który i tak trzymamy.
            if not _magazyn_wlaczony():
                raise HTTPException(
                    status_code=409,
                    detail="Magazyn sekretów został wyłączony w trakcie obsługi żądania.",
                )
            # Kolizję sprawdzamy przez magazyn (`svc.get` rzuca wyjątek dla nieistniejącej
            # nazwy, więc nie nadaje się do sprawdzania istnienia).
            if svc.store.get(request.name) is not None:
                raise HTTPException(
                    status_code=409, detail=f"Połączenie '{request.name}' już istnieje."
                )
            try:
                token_ref = store.put(nazwa_sekretu, token)
            except SecretStoreError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            try:
                svc.add(conn)
            except GitConnectionError as exc:
                # Pod zamkiem ta gałąź jest nieosiągalna dla kolizji nazwy (pre-check ją
                # wyklucza), ale zostaje jako obrona przed innym błędem magazynu — wtedy
                # sprzątamy sekret, żeby nie został osierocony.
                store.delete(nazwa_sekretu)
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        # W audycie: nazwa, dostawca i REFERENCJA. Nigdy token — dziennik audytu jest
        # niemodyfikowalny, więc sekret raz w nim zapisany zostałby tam na zawsze.
        audit_log.record(
            "api",
            "git.connection.add",
            {
                "name": conn.name,
                "provider": conn.provider,
                "token_ref": token_ref,
                "wizard": True,
            },
            principal=_principal_ref(principal),
        )
        return GitConnectionView(
            name=conn.name,
            provider=conn.provider.value,
            api_base=conn.api_base,
            username=conn.username,
            token_ref=conn.token_ref,
            ca_bundle=conn.ca_bundle,
        )

    @app.delete("/api/git/connections/{name}")
    def git_remove_connection(
        name: str, principal: Principal | None = dep_git_write
    ) -> dict[str, bool]:
        svc = _require_git()
        sekret_usuniety = False
        oczekiwana = f"{SECRET_SCHEME}git/{name}"
        # Pod tym samym zamkiem co kreator. Bez niego kasowanie sekretu wyścigało się
        # z zapisem: DELETE odczytywał połączenie, kreator w międzyczasie tworzył NOWE
        # połączenie o tej samej nazwie wraz z sekretem, a DELETE kasował świeżo zapisany
        # token — zostawiając połączenie z nierozwiązywalną referencją.
        with _mutex_polaczen:
            # Referencję odczytujemy PRZED usunięciem — potem połączenia już nie ma.
            istniejace = svc.store.get(name)
            try:
                svc.remove(name)
            except GitConnectionError as exc:
                # Awaria zapisu magazynu połączeń (np. pełny dysk, brak praw). Bez tej
                # obsługi leciało surowe 500 i — co gorsza — operacja nie zostawiała
                # ŻADNEGO wpisu w audycie, mimo że dotyczy usuwania poświadczenia.
                # Stan pozostaje spójny: `FileGitConnectionStore` utrwala PRZED podmianą
                # stanu w pamięci, więc nieudany zapis nie usuwa niczego.
                audit_log.record(
                    "api",
                    "git.connection.remove.failed",
                    {"name": name[:64]},
                    principal=_principal_ref(principal),
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Nie udało się zapisać magazynu połączeń — połączenie NIE zostało "
                        "usunięte, a sekret pozostaje nietknięty. Sprawdź miejsce na dysku "
                        "i prawa do katalogu."
                    ),
                ) from exc
            # Sekret kasujemy, gdy należy do NASZEJ przestrzeni nazw (`husarz:git/<nazwa>`)
            # i gdy NIKT go już nie używa. Trzy warunki, każdy z osobnego powodu:
            #
            #   1. Referencji ZEWNĘTRZNEJ (env:/vault:) nie ruszamy nigdy — nie jest nasza,
            #      a operator mógł jej użyć także poza Husarzem.
            #   2. Usuwamy także wtedy, gdy połączenia JUŻ NIE MA — to sierota (np. po
            #      restarcie z ulotnym magazynem połączeń). Bez tej gałęzi taki token był
            #      NIE DO USUNIĘCIA przez API, a `DELETE` zwracał `ok: true`, nie kasując nic.
            #   3. ...ale TYLKO jeżeli żadne INNE połączenie nie wskazuje tej referencji.
            #      Bez tego warunku (regresja wprowadzona wraz z punktem 2) usunięcie
            #      nieistniejącej nazwy niszczyło DZIAŁAJĄCE poświadczenie innego połączenia,
            #      które współdzieliło ten sam wpis — np. po zmianie nazwy połączenia.
            #      Sprawdzamy PO `remove`, więc usuwane połączenie nie liczy się do siebie.
            uzywa_ktos_inny = any(c.token_ref == oczekiwana for c in svc.list_connections())
            nasz_sekret = istniejace is None or istniejace.token_ref == oczekiwana
            if secret_store is not None and nasz_sekret and not uzywa_ktos_inny:
                sekret_usuniety = secret_store.delete(f"git/{name}")
        audit_log.record(
            "api",
            "git.connection.remove",
            {"name": name[:64], "secret_removed": sekret_usuniety},
            principal=_principal_ref(principal),
        )
        # Odpowiedź niesie SKUTEK, nie samo „przyjęto": operator musi wiedzieć, czy
        # połączenie w ogóle istniało i czy sekret faktycznie zniknął. Wcześniej
        # `DELETE` zwracał `ok: true` także wtedy, gdy nie usunął niczego — a osierocony
        # token zostawał na dysku.
        return {
            "ok": True,
            "removed": istniejace is not None,
            "secret_removed": sekret_usuniety,
        }

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
    )
    def plugin_tools(
        name: str, principal: Principal | None = dep_plugin_read
    ) -> list[RemoteToolView]:
        svc = _require_plugins()
        # Audyt PRÓBY przed wyjściem na zewnątrz — także odrzucenia egress/nieznana wtyczka.
        audit_log.record(
            "api",
            "plugin.discover",
            {"plugin": name[:64]},
            principal=_principal_ref(principal),
        )
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
