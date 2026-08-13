"""Schematy konfiguracji Husarza (Pydantic v2).

Ten moduł definiuje *jedyne* źródło prawdy o strukturze konfiguracji.
Zasada "zero hardcode": kod nie zawiera adresów, kluczy ani nazw modeli —
wszystko pochodzi z zwalidowanych tu struktur.

Walidacja jest surowa (``extra="forbid"``), aby literówka w pliku YAML dawała
czytelny komunikat, a nie ciche, błędne zachowanie.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

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
    registry: dict[str, ModelSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_refs(self) -> ModelsConfig:
        if self.default not in self.registry:
            raise ValueError(
                f"models.default='{self.default}' nie istnieje w models.registry "
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


class MtlsConfig(_StrictModel):
    """mTLS między usługami (referencje do sekretów, nie same materiały)."""

    enabled: bool = False
    ca_cert_ref: str | None = None
    cert_ref: str | None = None
    key_ref: str | None = None


class AuthConfig(_StrictModel):
    """OIDC + RBAC."""

    oidc_enabled: bool = False
    issuer: str | None = None
    client_id: str | None = None
    roles: list[str] = Field(default_factory=lambda: ["admin", "operator", "viewer"])


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


class SecurityConfig(_StrictModel):
    """Zbiorcza konfiguracja bezpieczeństwa."""

    egress: EgressConfig = Field(default_factory=EgressConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mtls: MtlsConfig = Field(default_factory=MtlsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
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
# roe (config/roe/*.yaml) — Rules of Engagement dla Puszkarza
# ---------------------------------------------------------------------------


class RoeScope(_StrictModel):
    """Zakres celów autoryzowanego pentestu."""

    targets_cidr: list[str] = Field(default_factory=list)
    targets_domains: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_targets(self) -> RoeScope:
        if not self.targets_cidr and not self.targets_domains:
            raise ValueError(
                "ROE musi definiować co najmniej jeden cel (targets_cidr lub targets_domains)."
            )
        return self


class RoeWindow(_StrictModel):
    """Okno czasowe autoryzacji."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _validate_order(self) -> RoeWindow:
        if self.end <= self.start:
            raise ValueError("ROE window: 'end' musi być późniejsze niż 'start'.")
        return self


class RoeConfig(_StrictModel):
    """Podpisany plik ROE. Domyślnie dry-run; akcje aktywne wymagają zgody operatora."""

    engagement_id: str
    owner: str
    authorized_by: str
    scope: RoeScope
    window: RoeWindow
    allowed_techniques: list[str] = Field(default_factory=list)
    forbidden_techniques: list[str] = Field(default_factory=list)
    consent: bool = False  # musi być True, by ROE było aktywne
    signature: str | None = None  # referencja/hash podpisu (nie sam materiał)
    dry_run_default: bool = True

    @property
    def is_active(self) -> bool:
        """Statyczna bramka ważności ROE: zgoda + niepusta referencja podpisu.

        Uwaga: nie sprawdza okna czasowego — do tego służy ``is_active_at``
        (używane przez ROE-gate w runtime, Etap 4). Kryptograficzną weryfikację
        podpisu wykona dostawca sekretów w Etapie 4; tu wymagamy jedynie, by
        referencja podpisu była obecna i niepusta.
        """
        return self.consent and bool(self.signature)

    def is_active_at(self, now: datetime) -> bool:
        """ROE jest aktywne (``is_active``) i mieści się w oknie czasowym w chwili ``now``."""
        return self.is_active and self.window.start <= now < self.window.end


# ---------------------------------------------------------------------------
# HusarzConfig — złożenie całości + walidacja krzyżowa
# ---------------------------------------------------------------------------


class HusarzConfig(_StrictModel):
    """Kompletna, zwalidowana konfiguracja platformy Husarz."""

    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    models: ModelsConfig
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
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

        if errors:
            raise ValueError("Błędy walidacji krzyżowej konfiguracji:\n- " + "\n- ".join(errors))
        return self
