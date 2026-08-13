"""Modele żądań i odpowiedzi API (Pydantic)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Status działania platformy."""

    status: str
    version: str
    profile: str


class ConfigSummary(BaseModel):
    """Zwięzłe podsumowanie konfiguracji (dla panelu)."""

    profile: str
    log_level: str
    default_model: str
    models: list[str]
    agents: list[str]
    tools: list[str]
    roe: list[str]
    egress_policy: str
    sandbox_engine: str
    sandbox_network: bool


class AgentInfo(BaseModel):
    """Informacja o agencie Chorągwi."""

    name: str
    display_name: str | None
    agent_class: str
    model: str
    tools: list[str]
    roe_required: bool
    enabled: bool


class ModelInfo(BaseModel):
    """Informacja o modelu z rejestru."""

    id: str
    backend: str
    tags: list[str]
    context_length: int
    enabled: bool


class ToolInfo(BaseModel):
    """Informacja o narzędziu."""

    name: str
    kind: str
    enabled: bool
    requires_egress: bool


class AuditEntryView(BaseModel):
    """Widok pojedynczego wpisu audytu (bez pełnych szczegółów)."""

    timestamp: str
    actor: str
    action: str
    roe_ref: str | None


class AuditView(BaseModel):
    """Widok dziennika audytu."""

    verified: bool
    count: int
    entries: list[AuditEntryView]


class UsageResponse(BaseModel):
    """Monitor kosztów/tokenów (MVP).

    ``orchestrations`` liczy WSZYSTKIE próby (spójnie z audytem, który zapisuje
    wpis przed uruchomieniem), a ``failures`` — próby zakończone błędem routera.
    """

    orchestrations: int
    chats: int = 0
    failures: int = 0
    max_tokens_per_request: int | None
    max_requests_per_minute: int | None


class ChatMessageIn(BaseModel):
    """Pojedyncza wiadomość konwersacji (tryb bezpośredniego czatu)."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class AttachmentIn(BaseModel):
    """Załącznik czatu: nazwa + treść tekstowa (limity egzekwuje serwer).

    ``content`` ma twardy sufit schematu (defense-in-depth) — precyzyjne limity
    (per plik/łączny) egzekwuje ``sanitize_attachments`` wg ``config.chat.attachments``.
    """

    name: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=8_000_000)


class ChatRequest(BaseModel):
    """Żądanie bezpośredniego czatu (jeden model, bez orkiestracji wieloagentowej)."""

    messages: list[ChatMessageIn] = Field(min_length=1)
    # Nadpisanie modelu (opcjonalne). Domyślnie ``models.chat`` lub ``models.default``.
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Załączniki (pliki/foldery) dołączane jako ogrodzony kontekst NIEZAUFANY.
    # Twardy sufit liczby na poziomie schematu; precyzyjny limit — z configu.
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=1000)


class ChatReply(BaseModel):
    """Odpowiedź modelu w trybie bezpośredniego czatu."""

    model: str
    content: str


class RegisterRequest(BaseModel):
    """Żądanie rejestracji konta (gdy włączona)."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    """Żądanie logowania."""

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class AuthToken(BaseModel):
    """Wynik logowania/rejestracji: token sesji + podstawowe dane konta."""

    token: str
    username: str
    role: str


class MeResponse(BaseModel):
    """Bieżący użytkownik: rola, aktywny model czatu i budżet tokenów."""

    username: str
    role: str
    chat_model: str
    tokens_used: int
    token_quota: int | None
    tokens_remaining: int | None


class GitConnectionIn(BaseModel):
    """Żądanie dodania połączenia Git. ``token_ref`` to REFERENCJA do sekretu (nie token)."""

    name: str = Field(min_length=1, max_length=64)
    provider: Literal["github", "gitlab"]
    api_base: str = Field(min_length=1, max_length=256)
    token_ref: str = Field(min_length=1, max_length=256)
    username: str | None = Field(default=None, max_length=128)


class GitConnectionView(BaseModel):
    """Widok połączenia Git (bez sekretu — ``token_ref`` to tylko referencja)."""

    name: str
    provider: str
    api_base: str
    username: str | None
    token_ref: str


class RepoView(BaseModel):
    """Repozytorium (znormalizowane między dostawcami)."""

    full_name: str
    default_branch: str
    private: bool
    url: str


class PullRequestIn(BaseModel):
    """Żądanie utworzenia PR (GitHub) / MR (GitLab)."""

    repo: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=256)
    head: str = Field(min_length=1, max_length=256)
    base: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=100_000)


class PullRequestView(BaseModel):
    """Wynik utworzenia PR/MR."""

    number: int | None
    url: str
    title: str


class OrchestrateRequest(BaseModel):
    """Żądanie orkiestracji zadania."""

    task: str = Field(min_length=1, max_length=100_000)


class ObservationView(BaseModel):
    """Widok obserwacji (wynik delegacji kroku)."""

    agent: str
    output: str
    model: str


class OrchestrateResponse(BaseModel):
    """Wynik orkiestracji."""

    task: str
    answer: str
    rounds: int
    observations: list[ObservationView]


class ValidateRequest(BaseModel):
    """Żądanie walidacji nadpisań konfiguracji (panel)."""

    overrides: dict[str, Any] = {}


class ValidateResponse(BaseModel):
    """Wynik walidacji konfiguracji."""

    ok: bool
    summary: ConfigSummary | None = None
    error: str | None = None
