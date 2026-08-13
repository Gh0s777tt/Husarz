"""Schematy konfiguracji Husarza (Pydantic v2).

Ten moduł definiuje *jedyne* źródło prawdy o strukturze konfiguracji.
Zasada "zero hardcode": kod nie zawiera adresów, kluczy ani nazw modeli —
wszystko pochodzi z zwalidowanych tu struktur.

Walidacja jest surowa (``extra="forbid"``), aby literówka w pliku YAML dawała
czytelny komunikat, a nie ciche, błędne zachowanie.
"""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from husarz.config.net import is_local_endpoint

# ---------------------------------------------------------------------------
# Typy wyliczeniowe (enumy)
# ---------------------------------------------------------------------------


class Profile(StrEnum):
    """Profil działania platformy."""

    DEV = "dev"
    PROD = "prod"
    AIRGAP = "airgap"


class LogLevel(StrEnum):
    """Poziom logowania."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EgressPolicy(StrEnum):
    """Domyślna polityka ruchu wychodzącego."""

    DENY = "deny"
    ALLOW = "allow"


class ModelBackend(StrEnum):
    """Backend serwujący model (warstwa OpenAI-compat)."""

    VLLM = "vllm"
    OLLAMA = "ollama"
    SGLANG = "sglang"
    OPENAI_COMPAT = "openai_compat"
    MOCK = "mock"  # używany w testach — nie łączy się z siecią


class AgentClass(StrEnum):
    """Klasa agenta w Chorągwi."""

    TOWARZYSZ = "towarzysz"  # agent pełny
    POCZTOWY = "pocztowy"  # podwykonawca


class SandboxEngine(StrEnum):
    """Silnik sandboxa narzędzi."""

    NONE = "none"
    DOCKER = "docker"
    DOCKER_GVISOR = "docker+gvisor"
    FIRECRACKER = "firecracker"


class SecretsProviderKind(StrEnum):
    """Rodzaj dostawcy sekretów."""

    NONE = "none"
    ENV = "env"
    VAULT = "vault"
    SOPS = "sops"


class RoutingStrategy(StrEnum):
    """Strategia doboru modelu."""

    TAGS = "tags"
    COST = "cost"
    LATENCY = "latency"


# ---------------------------------------------------------------------------
# Baza — wspólna konfiguracja modeli Pydantic
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Baza z surową walidacją: nieznane pola = błąd (czytelny komunikat)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ---------------------------------------------------------------------------
# platform (config/husarz.yaml)
# ---------------------------------------------------------------------------


class PlatformConfig(_StrictModel):
    """Ustawienia globalne platformy."""

    profile: Profile = Profile.DEV
    log_level: LogLevel = LogLevel.INFO
    data_dir: Path = Path("./data")
    artifacts_dir: Path = Path("./artifacts")
    workspace_dir: Path = Path("./workspace")
    language_default: str = "pl"
    # Zero telemetrii — twardy wymóg. Pole istnieje wyłącznie, by jawnie je wyłączyć.
    telemetry_enabled: bool = False

    @model_validator(mode="after")
    def _forbid_telemetry(self) -> PlatformConfig:
        if self.telemetry_enabled:
            raise ValueError(
                "Telemetria jest zabroniona w Husarzu (zero telemetrii). "
                "Ustaw platform.telemetry_enabled=false."
            )
        return self


# ---------------------------------------------------------------------------
# models (config/models.yaml)
# ---------------------------------------------------------------------------


class ModelSpec(_StrictModel):
    """Pojedynczy model w rejestrze."""

    backend: ModelBackend
    # Nazwa/ścieżka modelu przekazywana do backendu (np. tag Ollama lub repo HF).
    model: str
    # Endpoint OpenAI-compat. W profilu airgap musi być lokalny (egzekwuje _cross_validate).
    endpoint: str | None = None
    # Referencja do sekretu z kluczem API (np. "env:GLM_API_KEY", "vault:...").
    # Sekret rozwiązywany w runtime przez dostawcę — NIGDY nie trzymany w pliku ani w params.
    api_key_ref: str | None = None
    request_timeout_seconds: int | None = Field(default=None, ge=1)
    tags: list[str] = Field(default_factory=list)
    # Model wizyjny (multimodal) — może przyjmować obrazy w czacie.
    vision: bool = False
    context_length: int = 8192
    max_tokens: int | None = None
    # Ścieżka do lokalnych wag (katalog models/ jest w .gitignore).
    weights_path: Path | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    fallback: list[str] = Field(default_factory=list)
    enabled: bool = True


class ModelsConfig(_StrictModel):
    """Rejestr modeli i domyślny wybór."""

    default: str
    # Model trybu bezpośredniego czatu (endpoint /api/chat). Gdy pusty — używany
    # jest ``default``. Pozwala oddzielić szybki model konwersacyjny/kodowy od
    # modelu orkiestracji, bez zmian w kodzie.
    chat: str | None = None
    registry: dict[str, ModelSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_refs(self) -> ModelsConfig:
        if self.default not in self.registry:
            raise ValueError(
                f"models.default='{self.default}' nie istnieje w models.registry "
                f"(dostępne: {sorted(self.registry)})."
            )
        if self.chat is not None and self.chat not in self.registry:
            raise ValueError(
                f"models.chat='{self.chat}' nie istnieje w models.registry "
                f"(dostępne: {sorted(self.registry)})."
            )
        for model_id, spec in self.registry.items():
            for fb in spec.fallback:
                if fb not in self.registry:
                    raise ValueError(
                        f"Model '{model_id}' wskazuje fallback '{fb}', "
                        f"którego nie ma w rejestrze."
                    )
                if fb == model_id:
                    raise ValueError(f"Model '{model_id}' nie może być własnym fallbackiem.")
        return self


# ---------------------------------------------------------------------------
# routing (config/routing.yaml)
# ---------------------------------------------------------------------------


class RoutingRule(_StrictModel):
    """Reguła routingu: dopasuj po tagach, preferuj wskazane modele."""

    match_tags: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)


class CostControls(_StrictModel):
    """Kontrola kosztów i limitów. Wartości liczbowe (gdy podane) muszą być >= 1."""

    max_tokens_per_request: int | None = Field(default=None, ge=1)
    max_cost_per_task: float | None = Field(default=None, gt=0)
    max_requests_per_minute: int | None = Field(default=None, ge=1)


class RoutingConfig(_StrictModel):
    """Konfiguracja routera modeli."""

    strategy: RoutingStrategy = RoutingStrategy.TAGS
    # Domyślny model per agent (nazwa agenta -> id modelu lub "auto").
    agent_models: dict[str, str] = Field(default_factory=dict)
    rules: list[RoutingRule] = Field(default_factory=list)
    cost_controls: CostControls = Field(default_factory=CostControls)
    fallbacks_enabled: bool = True


# ---------------------------------------------------------------------------
# security (config/security.yaml)
# ---------------------------------------------------------------------------


class EgressConfig(_StrictModel):
    """Polityka ruchu wychodzącego. Domyślnie DENY-ALL."""

    default_policy: EgressPolicy = EgressPolicy.DENY
    allowlist: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_allowlist(self) -> EgressConfig:
        # Wpisy muszą być czystymi nazwami hostów. Pusty/whitespace wpis stałby się
        # częściowym wildcardem (dopasowanie ``host.endswith('.')``), rozszczelniając
        # deny-all — odrzucamy przy starcie z czytelnym komunikatem.
        normalized: list[str] = []
        for entry in self.allowlist:
            host = entry.strip().lower()
            if not host:
                raise ValueError("security.egress.allowlist zawiera pusty wpis (usuń go).")
            if "/" in host or "@" in host or ":" in host or host != entry.strip():
                raise ValueError(
                    f"security.egress.allowlist['{entry}'] musi być samą nazwą hosta "
                    f"(bez schematu/portu/ścieżki/poświadczeń)."
                )
            normalized.append(host)
        object.__setattr__(self, "allowlist", normalized)
        return self


class SandboxConfig(_StrictModel):
    """Ograniczenia sandboxa narzędzi."""

    engine: SandboxEngine = SandboxEngine.DOCKER_GVISOR
    network: bool = False  # brak sieci domyślnie
    cpu_limit: str = "1"
    memory_limit: str = "512m"
    timeout_seconds: int = 60
    workspace_only: bool = True
    command_allowlist: list[str] = Field(default_factory=list)
    path_allowlist: list[str] = Field(default_factory=list)
    # Obraz kontenera i klasa runtime (np. 'runsc' dla gVisor) — bez hardcode w executorze.
    image: str | None = None
    runtime_class: str | None = None
    # Dodatkowy hardening kontenera: użytkownik non-root, limit procesów, rootfs tylko-do-odczytu.
    run_as_user: str | None = "1000:1000"
    pids_limit: int | None = Field(default=512, ge=1)
    read_only_rootfs: bool = True


class MtlsConfig(_StrictModel):
    """mTLS między usługami (referencje do sekretów, nie same materiały)."""

    enabled: bool = False
    ca_cert_ref: str | None = None
    cert_ref: str | None = None
    key_ref: str | None = None


class AuthConfig(_StrictModel):
    """OIDC + RBAC + uwierzytelnianie API tokenem Bearer.

    ``api_token_ref`` to **referencja do sekretu** (rozwiązywana przez dostawcę
    sekretów ENV/Vault/SOPS przy starcie), nigdy sam token — zgodnie z zasadą
    „sekrety nie trafiają do configu". Gdy ustawiona, API wymaga nagłówka
    ``Authorization: Bearer <token>`` na wszystkich endpointach ``/api``, a rola
    ``api_role`` decyduje o uprawnieniach (RBAC). Gdy pusta, API działa bez
    uwierzytelnienia — dopuszczalne wyłącznie dla nasłuchu loopback (dev).
    """

    oidc_enabled: bool = False
    issuer: str | None = None
    client_id: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["admin", "operator", "user", "viewer"])
    api_token_ref: str | None = None
    api_role: str = "operator"
    # --- Konta użytkowników (Etap 7): logowanie/rejestracja, sesje, limity ---
    # Rejestracja domyślnie WYŁĄCZONA (model „dla wybranych" — konta tworzy admin).
    allow_registration: bool = False
    # Domyślna rola nowego konta = 'user' (najmniejsze uprawnienia: czat/orkiestracja,
    # bez tool:*/roe:authorize/audit:read). Podniesienie roli wymaga decyzji admina.
    default_user_role: str = "user"
    # Limit tokenów dla nowego konta (None = bez limitu; sensowne w trybie hostowanym).
    default_token_quota: int | None = Field(default=None, ge=1)
    session_ttl_minutes: int = Field(default=720, ge=1)
    # Anty-brute-force logowania: blokada konta po N nieudanych próbach na M minut.
    login_max_attempts: int = Field(default=5, ge=1)
    login_lockout_minutes: int = Field(default=15, ge=1)
    # Trwały magazyn kont (JSON). None = tylko w pamięci (dev/testy, bez trwałości).
    accounts_path: Path | None = None
    # Seed konta administratora przy pustym magazynie (hasło z referencji do sekretu).
    # Oba pola muszą być ustawione RAZEM (walidator poniżej).
    seed_admin_username: str | None = None
    seed_admin_password_ref: str | None = None

    @model_validator(mode="after")
    def _validate_roles_and_seed(self) -> AuthConfig:
        known = set(self.roles)
        if self.api_role not in known:
            raise ValueError(
                f"security.auth.api_role='{self.api_role}' nie należy do auth.roles "
                f"({sorted(known)})."
            )
        if self.default_user_role not in known:
            raise ValueError(
                f"security.auth.default_user_role='{self.default_user_role}' nie należy "
                f"do auth.roles ({sorted(known)})."
            )
        if bool(self.seed_admin_username) != bool(self.seed_admin_password_ref):
            raise ValueError(
                "security.auth: seed_admin_username i seed_admin_password_ref muszą być "
                "ustawione RAZEM (albo oba, albo żadne)."
            )
        return self


class AuditConfig(_StrictModel):
    """Niemodyfikowalny dziennik audytu."""

    enabled: bool = True
    path: Path = Path("./audit/audit.log")
    immutable: bool = True
    hash_chain: bool = True  # łańcuch skrótów (tamper-evidence)


class EncryptionConfig(_StrictModel):
    """Szyfrowanie at-rest."""

    at_rest: bool = True
    algorithm: str = "AES-256-GCM"


class ToolLoopConfig(_StrictModel):
    """Limity pętli narzędziowej (function-calling). Feed danych NIEZAUFANYCH → bezpieczeństwo.

    ``max_iterations`` żyje per agent (``AgentConfig.max_iterations``); tu są limity
    globalne/rozmiarowe wspólne dla wszystkich agentów.
    """

    # Twardy limit rozmiaru pojedynczego wyniku narzędzia przed re-injekcją (anty-DoS).
    max_result_bytes: int = Field(default=100_000, ge=1)
    # Twardy limit rozmiaru tekstu wchodzącego do RAG (rag.add) — feed sterowany modelem,
    # anty-OOM współdzielonego magazynu. Osobne od max_result_bytes (wejście ≠ wyjście).
    max_rag_add_bytes: int = Field(default=100_000, ge=1)
    # Globalny budżet wywołań narzędzi na CAŁĄ orkiestrację (kroki × iteracje × rundy)
    # — pętla zamienia nieograniczony fan-out planu w realną amplifikację (spawny
    # kontenerów). Po wyczerpaniu: deterministyczne zakończenie (fail-closed) + audyt.
    max_total_calls: int = Field(default=64, ge=1)
    # Twardy cap liczby kroków planu (plan pochodzi z NIEZAUFANEGO wyjścia modelu).
    max_plan_steps: int = Field(default=20, ge=1)


class SecurityConfig(_StrictModel):
    """Zbiorcza konfiguracja bezpieczeństwa."""

    egress: EgressConfig = Field(default_factory=EgressConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mtls: MtlsConfig = Field(default_factory=MtlsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    tool_loop: ToolLoopConfig = Field(default_factory=ToolLoopConfig)
    prompt_injection_filters: bool = True


# ---------------------------------------------------------------------------
# agents (config/agents/*.yaml)
# ---------------------------------------------------------------------------


class AgentConfig(_StrictModel):
    """Definicja agenta Chorągwi."""

    name: str
    display_name: str | None = None
    agent_class: AgentClass = AgentClass.TOWARZYSZ
    role: str = ""
    # Id modelu z rejestru lub "auto" (wtedy decyduje router).
    model: str = "auto"
    # Nazwa pliku promptu w katalogu prompts/ — sama nazwa (bez ścieżek, bez '..',
    # bez litery dysku), by ENV/panel nie mogły wskazać dowolnego pliku (path traversal).
    prompt_file: str = Field(pattern=r"^[A-Za-z0-9._-]+\.md$")
    tools: list[str] = Field(default_factory=list)
    max_iterations: int = 8
    roe_required: bool = False  # True dla Puszkarza
    # Opt-in na pętlę narzędziową (function-calling). Domyślnie False (deny-by-default):
    # agent wykonuje narzędzia w pętli TYLKO po jawnym włączeniu — kontrola promienia
    # rażenia (np. shell 'python' = RCE-w-sandboxie). Patrz ADR-0016.
    tool_loop_enabled: bool = False
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# tools (config/tools/*.yaml)
# ---------------------------------------------------------------------------


class ToolConfig(_StrictModel):
    """Definicja narzędzia dostępnego dla agentów."""

    name: str
    kind: str  # web | shell | file_edit | git | run_tests | rag | custom
    description: str = ""
    enabled: bool = True
    requires_sandbox: bool = True
    requires_egress: bool = False
    # Allowlista: domeny (web), komendy (shell), ścieżki (file_edit) itd.
    allowlist: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_egress(self) -> ToolConfig:
        if self.requires_egress and not self.allowlist:
            raise ValueError(
                f"Narzędzie '{self.name}' wymaga egress, ale ma pustą allowlistę. "
                f"Podaj dozwolone domeny lub ustaw requires_egress=false."
            )
        return self


# ---------------------------------------------------------------------------
# plugins (config/plugins/*.yaml) — konektory wtyczek (MCP)
# ---------------------------------------------------------------------------

_SECRET_REF_SCHEMES = ("env:", "file:", "vault:", "sops:")


class EmbedderConfig(_StrictModel):
    """Konfiguracja embeddera (tekst → wektor) dla backendu pamięci ``embedding``.

    Suwerennie: domyślnie lokalny Ollama. ``fake`` służy WYŁĄCZNIE dev/testom (deterministyczny
    test-double, nie realne wyszukiwanie semantyczne). Klucz tylko jako referencja do sekretu.
    """

    kind: Literal["fake", "ollama"] = "ollama"
    endpoint: str | None = None  # domyślnie loopback http://127.0.0.1:11434
    model: str | None = None  # domyślnie nomic-embed-text (768D)
    api_key_ref: str | None = None  # referencja env:/file:/vault:/sops: (embedder za proxy)
    # Wymiar wektora — MUSI pasować do modelu (nomic-embed-text=768, mxbai-embed-large=1024).
    # Niezgodność blokuje się fail-closed przy pierwszym wywołaniu (anty-korupcja magazynu).
    dim: int = Field(default=768, ge=1)

    @model_validator(mode="after")
    def _validate(self) -> EmbedderConfig:
        if self.api_key_ref is not None and not self.api_key_ref.startswith(_SECRET_REF_SCHEMES):
            raise ValueError(
                "embedder.api_key_ref musi być referencją do sekretu "
                "(env:/file:/vault:/sops:), a nie samą wartością."
            )
        return self


class RagBackendConfig(_StrictModel):
    """Konfiguracja backendu pamięci (RAG) parsowana z ``ToolConfig.config`` narzędzia rag.

    ``memory`` — obecny backend słowny (zero zależności, domyślny). ``embedding`` — wektorowy
    (embedder + magazyn wektorów). Magazyn: ``in_memory`` (ulotny) lub ``sqlite`` (trwały,
    szyfrowany at-rest przez ``encryption_key_ref``) — patrz ADR-0018.
    """

    backend: Literal["memory", "embedding"] = "memory"
    collection: str = "husarz_memory"  # namespace — izolacja pamięci między agentami/kolekcjami
    top_k: int = Field(default=8, ge=1)
    max_items: int = Field(default=5000, ge=1)  # cap magazynu + ewikcja FIFO (anty-OOM)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    # Magazyn wektorów (dla backend=embedding): ``in_memory`` (ulotny) lub ``sqlite`` (trwały).
    store: Literal["in_memory", "sqlite"] = "in_memory"
    path: Path | None = None  # plik sqlite; None → data_dir/memory/<collection>.db
    # Szyfrowanie at-rest (sqlite): None → dziedziczy security.encryption.at_rest.
    encrypt_at_rest: bool | None = None
    encryption_key_ref: str | None = None  # referencja env:/file:/vault:/sops: do klucza (DEK)

    @model_validator(mode="after")
    def _validate(self) -> RagBackendConfig:
        if self.encryption_key_ref is not None and not self.encryption_key_ref.startswith(
            _SECRET_REF_SCHEMES
        ):
            raise ValueError(
                "encryption_key_ref musi być referencją do sekretu (env:/file:/vault:/sops:)."
            )
        # Walidacja krzyżowa: pola trwałości/at-rest mają sens WYŁĄCZNIE dla trwałego magazynu
        # sqlite — inaczej byłyby po cichu ignorowane (fałszywe poczucie włączonego szyfrowania).
        atrest_set = (
            self.encrypt_at_rest is not None
            or self.encryption_key_ref is not None
            or self.path is not None
        )
        if atrest_set and self.store != "sqlite":
            raise ValueError(
                "Pola trwałości/at-rest (path/encrypt_at_rest/encryption_key_ref) wymagają "
                "store: sqlite — dla store: in_memory byłyby ignorowane."
            )
        if self.store == "sqlite" and self.backend != "embedding":
            raise ValueError("store: sqlite wymaga backend: embedding (trwały magazyn wektorowy).")
        return self


class PluginConfig(_StrictModel):
    """Konektor wtyczki (serwer narzędzi MCP przez HTTP JSON-RPC).

    Token jest WYŁĄCZNIE referencją do sekretu (nie wartością). Pełna polityka egress
    (loopback/allowlista/anty-SSRF) egzekwowana jest w runtime (``build_connector``);
    tu walidujemy jedynie kształt endpointu (http(s), bez userinfo, z hostem).
    """

    name: str
    transport: Literal["http"] = "http"  # MVP: tylko HTTP JSON-RPC (stdio odłożone)
    description: str = ""
    enabled: bool = True
    endpoint: str  # URL serwera MCP (np. http://127.0.0.1:8808)
    token_ref: str | None = None  # referencja env:/file:/vault:/sops: (nie sam token)
    timeout_seconds: int = Field(default=30, ge=1)
    max_output_bytes: int = Field(default=1_000_000, ge=1)  # twardy limit odpowiedzi (DoS)

    @model_validator(mode="after")
    def _validate(self) -> PluginConfig:
        if self.token_ref is not None and not self.token_ref.startswith(_SECRET_REF_SCHEMES):
            raise ValueError(
                f"Wtyczka '{self.name}': token_ref musi być referencją do sekretu "
                f"(env:/file:/vault:/sops:), a nie samą wartością tokenu."
            )
        parsed = urlparse(self.endpoint)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Wtyczka '{self.name}': endpoint musi być adresem http(s)://.")
        if parsed.username or parsed.password or "@" in parsed.netloc:
            raise ValueError(
                f"Wtyczka '{self.name}': endpoint nie może zawierać poświadczeń w URL (userinfo)."
            )
        if not parsed.hostname:
            raise ValueError(f"Wtyczka '{self.name}': endpoint nie zawiera hosta.")
        return self


# ---------------------------------------------------------------------------
# roe (config/roe/*.yaml) — Rules of Engagement dla Puszkarza
# ---------------------------------------------------------------------------


class RoeScope(_StrictModel):
    """Zakres celów autoryzowanego pentestu."""

    targets_cidr: list[str] = Field(default_factory=list)
    targets_domains: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> RoeScope:
        if not self.targets_cidr and not self.targets_domains:
            raise ValueError(
                "ROE musi definiować co najmniej jeden cel (targets_cidr lub targets_domains)."
            )
        # CIDR muszą być poprawne i wyrównane (strict) — inaczej '203.0.113.5/24'
        # cicho poszerzałby zakres do całej sieci (nadmierna autoryzacja).
        for entry in self.targets_cidr:
            try:
                ipaddress.ip_network(entry, strict=True)
            except ValueError as exc:
                raise ValueError(
                    f"targets_cidr: '{entry}' nie jest poprawną, wyrównaną siecią CIDR "
                    f"(dla pojedynczego hosta użyj /32 lub /128)."
                ) from exc
        return self


def _to_utc(value: datetime) -> datetime:
    """Normalizuje datetime do UTC-aware (naive traktujemy jako UTC)."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


class RoeWindow(_StrictModel):
    """Okno czasowe autoryzacji (normalizowane do UTC-aware)."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _normalize_and_validate(self) -> RoeWindow:
        # object.__setattr__ omija validate_assignment (inaczej rekurencja walidatora).
        object.__setattr__(self, "start", _to_utc(self.start))
        object.__setattr__(self, "end", _to_utc(self.end))
        if self.end <= self.start:
            raise ValueError("ROE window: 'end' musi być późniejsze niż 'start'.")
        return self


class RoeConfig(_StrictModel):
    """Podpisany plik ROE. Domyślnie dry-run; akcje aktywne wymagają zgody operatora."""

    engagement_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    authorized_by: str = Field(min_length=1)
    scope: RoeScope
    window: RoeWindow
    allowed_techniques: list[str] = Field(default_factory=list)
    forbidden_techniques: list[str] = Field(default_factory=list)
    consent: bool = False  # musi być True, by ROE było aktywne
    signature: str | None = None  # referencja/hash podpisu (nie sam materiał)
    dry_run_default: bool = True

    @property
    def is_active(self) -> bool:
        """Statyczna bramka ważności ROE: zgoda + niepusta (nie-biała) referencja podpisu.

        Uwaga: nie sprawdza okna czasowego — do tego służy ``is_active_at``.
        Kryptograficzną weryfikację podpisu wykonuje ROE-gate przez wstrzykiwalny
        weryfikator (Etap 4+); tu wymagamy jedynie, by referencja podpisu była obecna.
        """
        return self.consent and bool(self.signature and self.signature.strip())

    def is_active_at(self, now: datetime) -> bool:
        """ROE jest aktywne (``is_active``) i mieści się w oknie czasowym w chwili ``now``.

        ``now`` naive jest traktowane jako UTC — porównanie jest zawsze w jednej strefie.
        """
        moment = _to_utc(now)
        return self.is_active and self.window.start <= moment < self.window.end


# ---------------------------------------------------------------------------
# HusarzConfig — złożenie całości + walidacja krzyżowa
# ---------------------------------------------------------------------------


class AttachmentsConfig(_StrictModel):
    """Limity załączników czatu (pliki/foldery jako kontekst). Ochrona przed DoS."""

    enabled: bool = True
    max_files: int = Field(default=20, ge=1)
    max_bytes_per_file: int = Field(default=256_000, ge=1)  # ~256 kB tekstu / plik
    max_total_bytes: int = Field(default=1_000_000, ge=1)  # ~1 MB łącznego kontekstu


class ImagesConfig(_StrictModel):
    """Limity obrazów w czacie (modele wizyjne). Treść binarna — sniffowana z bajtów."""

    enabled: bool = True
    max_images: int = Field(default=4, ge=1)
    max_bytes_per_image: int = Field(default=2_000_000, ge=1)  # ~2 MB (dekodowane)


class ChatConfig(_StrictModel):
    """Ustawienia trybu czatu (config/chat.yaml). Opcjonalny — działają wartości domyślne."""

    attachments: AttachmentsConfig = Field(default_factory=AttachmentsConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    # Twardy limit rozmiaru ciała żądania (ochrona pamięci przed OOM podczas ingestii,
    # zanim logika limitów cokolwiek przytnie). Podniesiony, by zmieścić kilka obrazów
    # (base64 ~+33%). Odrzucenie → HTTP 413.
    max_request_bytes: int = Field(default=12_000_000, ge=1024)


class GitConfig(_StrictModel):
    """Integracje Git (config/git.yaml). Opcjonalny — domyślnie wyłączony.

    Połączenia (host + referencja tokenu) trzymane są w magazynie runtime; tu tylko
    włącznik i ścieżka trwałego magazynu. Hosty dostawców i tak muszą przejść przez
    bramkę egress (deny-all) — dodaj je do ``security.egress.allowlist``.
    """

    enabled: bool = False
    connections_path: Path | None = None  # np. ./data/git-connections.json; None = w pamięci


class HusarzConfig(_StrictModel):
    """Kompletna, zwalidowana konfiguracja platformy Husarz."""

    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    models: ModelsConfig
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)
    roe: dict[str, RoeConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _cross_validate(self) -> HusarzConfig:
        errors: list[str] = []

        known_models = set(self.models.registry)
        known_tools = set(self.tools)

        # 1) Routing: modele per agent muszą istnieć (lub "auto").
        for agent_name, model_id in self.routing.agent_models.items():
            if model_id != "auto" and model_id not in known_models:
                errors.append(
                    f"routing.agent_models['{agent_name}'] -> '{model_id}' "
                    f"nie istnieje w models.registry."
                )

        # 2) Reguły routingu preferują istniejące modele.
        for i, rule in enumerate(self.routing.rules):
            for model_id in rule.prefer:
                if model_id not in known_models:
                    errors.append(
                        f"routing.rules[{i}].prefer -> '{model_id}' "
                        f"nie istnieje w models.registry."
                    )

        # 3) Agenci: model i narzędzia muszą istnieć.
        for agent_name, agent in self.agents.items():
            if agent.model != "auto" and agent.model not in known_models:
                errors.append(
                    f"agents['{agent_name}'].model -> '{agent.model}' "
                    f"nie istnieje w models.registry."
                )
            # Pusty rejestr narzędzi NIE wyłącza walidacji — wtedy KAŻDE odwołanie
            # do narzędzia jest błędem (agent nie może używać nieistniejącego narzędzia).
            for tool_name in agent.tools:
                if tool_name not in known_tools:
                    errors.append(
                        f"agents['{agent_name}'].tools -> '{tool_name}' "
                        f"nie jest zdefiniowane w config/tools/."
                    )

        # 4) Bazowa linia bezpieczeństwa dla profili nieodwołalnych (prod, airgap):
        #    twardych wymagań nie wolno cicho wyłączyć. W dev zostawiamy elastyczność.
        if self.platform.profile in (Profile.PROD, Profile.AIRGAP):
            profile_name = self.platform.profile.value
            if self.security.sandbox.engine is SandboxEngine.NONE:
                errors.append(
                    f"Profil '{profile_name}' wymaga sandboxa "
                    f"(security.sandbox.engine != none)."
                )
            if not self.security.audit.enabled:
                errors.append(
                    f"Profil '{profile_name}' wymaga włączonego audytu "
                    f"(security.audit.enabled=true)."
                )
            if not self.security.audit.immutable:
                errors.append(
                    f"Profil '{profile_name}' wymaga niemodyfikowalnego audytu "
                    f"(security.audit.immutable=true)."
                )
            if not self.security.encryption.at_rest:
                errors.append(
                    f"Profil '{profile_name}' wymaga szyfrowania at-rest "
                    f"(security.encryption.at_rest=true)."
                )

        # 5) Profil airgap: brak egress i brak zdalnych endpointów modeli.
        if self.platform.profile is Profile.AIRGAP:
            if self.security.egress.default_policy is not EgressPolicy.DENY:
                errors.append("Profil airgap wymaga security.egress.default_policy=deny.")
            if self.security.egress.allowlist:
                errors.append(
                    "Profil airgap wymaga pustej security.egress.allowlist "
                    f"(znaleziono: {self.security.egress.allowlist})."
                )
            if self.security.sandbox.network:
                errors.append("Profil airgap wymaga security.sandbox.network=false.")
            for model_id, spec in self.models.registry.items():
                if spec.enabled and not is_local_endpoint(spec.endpoint):
                    errors.append(
                        f"Profil airgap: model '{model_id}' ma nielokalny endpoint "
                        f"'{spec.endpoint}'. Dozwolone są tylko adresy lokalne/prywatne."
                    )

        # 6) Pętla narzędziowa pisze do workspace (file_edit) — workspace NIE może pokrywać
        #    się z katalogami danych/artefaktów (izolacja promienia rażenia; patrz ADR-0016).
        workspace = self.platform.workspace_dir.resolve()
        for label, other_dir in (
            ("data_dir", self.platform.data_dir),
            ("artifacts_dir", self.platform.artifacts_dir),
        ):
            other = other_dir.resolve()
            if (
                workspace == other
                or workspace.is_relative_to(other)
                or other.is_relative_to(workspace)
            ):
                errors.append(
                    f"platform.workspace_dir ({workspace}) nie może pokrywać się z "
                    f"platform.{label} ({other}) — rozdziel katalogi."
                )

        # 7) Pamięć (RAG): kolekcje narzędzi rag muszą być rozłączne (izolacja pamięci między
        #    agentami — zderzenie namespace = kanał trwałej injekcji cross-agent, ADR-0017),
        #    a endpoint embeddera musi być lokalny w profilu airgap (embeddingi ~ PII).
        seen_collections: dict[str, str] = {}
        for name, tc in self.tools.items():
            if tc.kind != "rag" or not tc.enabled:
                continue
            collection = str(tc.config.get("collection") or "husarz_memory")
            if collection in seen_collections:
                errors.append(
                    f"Narzędzia rag '{seen_collections[collection]}' i '{name}' współdzielą "
                    f"kolekcję '{collection}' — rozłączne kolekcje izolują pamięć agentów."
                )
            else:
                seen_collections[collection] = name
            embedder = tc.config.get("embedder")
            endpoint = embedder.get("endpoint") if isinstance(embedder, dict) else None
            if (
                self.platform.profile is Profile.AIRGAP
                and isinstance(endpoint, str)
                and not is_local_endpoint(endpoint)
            ):
                errors.append(
                    f"Profil airgap: embedder narzędzia rag '{name}' ma nielokalny endpoint "
                    f"'{endpoint}'. Embeddingi (odwracalne do PII) muszą pozostać lokalne."
                )

        if errors:
            raise ValueError("Błędy walidacji krzyżowej konfiguracji:\n- " + "\n- ".join(errors))
        return self
