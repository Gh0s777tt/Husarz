"""Modele żądań i odpowiedzi API (Pydantic)."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


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
    """Widok pojedynczego wpisu audytu (szczegóły WYŁĄCZNIE z allowlisty)."""

    timestamp: str
    actor: str  # kto WYKONAŁ (agent albo 'api')
    action: str
    roe_ref: str | None
    # Kto ZLECIŁ (`user:<id>` / `token:<rola>`); puste = brak uwierzytelnienia. Bez tego pola
    # audyt widziany przez API/konsolę nie odpowiadał na pytanie o rozliczalność (Etap 13c).
    principal: str = ""
    # Wąski, jawnie dozwolony podzbiór `AuditEntry.detail` — dla `tool.call` odpowiada na
    # pytanie „KTÓRE narzędzie i czy się powiodło". Pełny szczegół (argumenty, rozmiary,
    # przypięte IP) zostaje w dzienniku na dysku. Reguła: husarz.api.audit_view.public_detail.
    detail: dict[str, str | int | bool] = Field(default_factory=dict)


class AuditView(BaseModel):
    """Widok dziennika audytu.

    Attributes:
        verified: Czy łańcuch skrótów i kotwica zgadzają się z zawartością pliku.
        kotwica: Czy kontrola KOMPLETNOŚCI w ogóle działa — ``ok``, ``brak``, ``nieczytelna``
            albo ``wylaczona``. Pole istnieje, bo brak kotwicy po prostu WYŁĄCZA wykrywanie
            odcięcia ogona, a robił to dotąd niewidocznie: ``verified`` pokazywało wtedy
            ``true``, choć jedyny mechanizm wykrywający usunięcie wpisów przestał działać.
            Rozróżnienie „nie wykryto naruszenia" od „nie ma czym wykrywać" należy do
            operatora, nie do serwera.
        count: Liczba wpisów w dzienniku.
        entries: Ostatnie wpisy (bez pola ``detail`` — może nieść ścieżki i referencje kont).
    """

    verified: bool
    kotwica: str
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


class ImageIn(BaseModel):
    """Obraz do czatu: nazwa + base64 (bez prefiksu ``data:``). Typ sniffowany na serwerze."""

    name: str = Field(min_length=1, max_length=256)
    data: str = Field(min_length=1, max_length=8_000_000)


class ChatRequest(BaseModel):
    """Żądanie bezpośredniego czatu (jeden model, bez orkiestracji wieloagentowej)."""

    messages: list[ChatMessageIn] = Field(min_length=1)
    # Nadpisanie modelu (opcjonalne). Domyślnie ``models.chat`` lub ``models.default``.
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    # Załączniki (pliki/foldery) dołączane jako ogrodzony kontekst NIEZAUFANY.
    # Twardy sufit liczby na poziomie schematu; precyzyjny limit — z configu.
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=1000)
    # Obrazy (modele wizyjne) — twardy sufit liczby; precyzyjne limity z configu.
    images: list[ImageIn] = Field(default_factory=list, max_length=50)


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


# Prefiksy poświadczeń dostawców Git. Nazwa połączenia trafia do NIEMODYFIKOWALNEGO dziennika
# audytu, do pliku połączeń i — jako JAWNY klucz — do magazynu sekretów. Token wklejony
# omyłkowo w pole nazwy jest więc zapisywany na stałe w miejscu, którego z definicji nie da
# się wyczyścić, a jedynym wyjściem pozostaje unieważnienie tokenu u dostawcy.
#
# Wzorzec nazwy (`[A-Za-z0-9._-]`, do 64 znaków) przepuszcza DOKŁADNIE kształt tokenów, które
# ten kreator obsługuje: `ghp_` + 36 znaków alfanumerycznych oraz `glpat-` + 20. Odrzucamy je
# po prefiksie — sprawdzeniu precyzyjnym, nie heurystyce na entropii, która myliłaby się na
# sensownych nazwach typu `gh-prod-2026`.
_PREFIKSY_POSWIADCZEN: tuple[str, ...] = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "glpat-",
)


def _odrzuc_nazwe_wygladajaca_na_token(value: str) -> str:
    """Odrzuca nazwę połączenia zaczynającą się prefiksem poświadczenia.

    Args:
        value: Proponowana nazwa połączenia.

    Returns:
        Nazwę bez zmian, gdy jest bezpieczna.

    Raises:
        ValueError: Gdy nazwa wygląda na token. Komunikat CELOWO nie powtarza wartości —
            trafiłby do treści odpowiedzi, czyli tam, skąd token właśnie wypychamy.
    """
    if value.lower().startswith(_PREFIKSY_POSWIADCZEN):
        raise ValueError(
            "Nazwa połączenia wygląda na token dostępu. Nazwa trafia do dziennika audytu "
            "(niemodyfikowalnego), pliku połączeń i jako jawny klucz do magazynu sekretów — "
            "token byłby tam zapisany na stałe. Podaj nazwę opisową (np. 'moj-github'), "
            "a token wklej w polu tokenu."
        )
    return value


class GitConnectionIn(BaseModel):
    """Żądanie dodania połączenia Git. ``token_ref`` to REFERENCJA do sekretu (nie token)."""

    # Nazwa trafia do ŚCIEŻKI URL-a (`/api/git/connections/{name}`), więc nie może
    # zawierać ukośnika ani znaków wymagających kodowania. Bez tego ograniczenia
    # połączenie o nazwie "grupa/projekt" powstawało poprawnie, ale DELETE zwracało 404
    # (routing FastAPI nie dopasowuje ukośnika w segmencie, a `%2F` też nie pomaga) —
    # zostawało nieusuwalne przez API i trzymało token bezterminowo. Sprawdzone.
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    provider: Literal["github", "gitlab"]
    api_base: str = Field(min_length=1, max_length=256)
    token_ref: str = Field(min_length=1, max_length=256)
    username: str | None = Field(default=None, max_length=128)
    # Ścieżka do pliku PEM z certyfikatem urzędu, który podpisał certyfikat serwera —
    # dla samodzielnie hostowanego GitLaba z PRYWATNYM CA. Zaufanie jest zawężone do
    # tego jednego połączenia (patrz husarz.git.client.build_ssl_context).
    ca_bundle: str | None = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def _name_is_not_a_token(cls, value: str) -> str:
        """Nazwa nie może być omyłkowo wklejonym tokenem — patrz uzasadnienie przy funkcji."""
        return _odrzuc_nazwe_wygladajaca_na_token(value)

    @field_validator("token_ref")
    @classmethod
    def _token_ref_is_reference(cls, value: str) -> str:
        # Wymuszamy REFERENCJĘ — surowy token nie może trafić do magazynu połączeń na
        # dysku (spójnie z „sekrety poza repo/magazynem"). `husarz:` wskazuje zapisywalny
        # magazyn sekretów; wpisy powstają tam przez kreator, nie przez to pole.
        if not value.startswith(("env:", "file:", "vault:", "sops:", "husarz:")):
            raise ValueError(
                "token_ref musi być referencją do sekretu "
                "(env:/file:/vault:/sops:/husarz:), a nie samą wartością tokenu."
            )
        return value

    @field_validator("api_base")
    @classmethod
    def _api_base_is_https(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https":
            raise ValueError("api_base musi być adresem https://.")
        if parsed.username or parsed.password or "@" in parsed.netloc:
            raise ValueError("api_base nie może zawierać poświadczeń w URL.")
        if not parsed.hostname:
            raise ValueError("api_base nie zawiera hosta.")
        return value


class GitConnectionWizardIn(BaseModel):
    """Żądanie kreatora połączeń: przyjmuje SAM TOKEN, nie referencję.

    To jedyne miejsce w API, przez które materiał sekretu wchodzi do Husarza. Token jest
    natychmiast zapisywany w szyfrowanym magazynie (:mod:`husarz.security.secret_store`),
    a w magazynie połączeń ląduje wyłącznie wygenerowana referencja ``husarz:git/<nazwa>``.

    **Pole ``token`` celowo NIE ma ograniczeń Pydantic** (``min_length``/``max_length``).
    Powód: domyślna obsługa ``RequestValidationError`` w FastAPI zwraca w ciele odpowiedzi
    pole ``input`` z ODRZUCONĄ WARTOŚCIĄ, więc ograniczenie długości sprawiłoby, że przy
    naruszeniu limitu token wróciłby w treści błędu 422. Długość i pustkę sprawdza endpoint,
    komunikatem, który wartości nie powtarza.

    **To NIE wystarcza i pierwotnie było tu opisane jako wystarczające — sprostowanie.**
    Brak ograniczeń na polu zamyka wyłącznie wariant, w którym błąd dotyczy TEGO pola.
    ``input`` wraca również, gdy brakuje innego wymaganego pola (echo CAŁEGO ciała wraz
    z tokenem), gdy nazwa pola ma literówkę, gdy ciało przyszło jako formularz
    ``x-www-form-urlencoded`` albo jako lista JSON. Właściwą bramką jest handler
    ``RequestValidationError`` zarejestrowany w :func:`husarz.api.app.create_app`, który
    usuwa ``input`` z KAŻDEJ odpowiedzi walidacyjnej. Brak ograniczeń na tym polu zostaje
    jako druga warstwa, nie jako jedyna.

    Ten model NIE jest używany jako model odpowiedzi nigdzie w API — odpowiedzią jest
    :class:`GitConnectionView`, w którym pola ``token`` po prostu nie ma.
    """

    # Nazwa trafia do ŚCIEŻKI URL-a (`/api/git/connections/{name}`), więc nie może
    # zawierać ukośnika ani znaków wymagających kodowania. Bez tego ograniczenia
    # połączenie o nazwie "grupa/projekt" powstawało poprawnie, ale DELETE zwracało 404
    # (routing FastAPI nie dopasowuje ukośnika w segmencie, a `%2F` też nie pomaga) —
    # zostawało nieusuwalne przez API i trzymało token bezterminowo. Sprawdzone.
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    provider: Literal["github", "gitlab"]
    api_base: str = Field(min_length=1, max_length=256)
    token: str
    username: str | None = Field(default=None, max_length=128)
    # Ścieżka do pliku PEM z certyfikatem urzędu, który podpisał certyfikat serwera —
    # dla samodzielnie hostowanego GitLaba z PRYWATNYM CA. Zaufanie jest zawężone do
    # tego jednego połączenia (patrz husarz.git.client.build_ssl_context).
    ca_bundle: str | None = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def _name_is_not_a_token(cls, value: str) -> str:
        """Ten sam kontrakt co w :class:`GitConnectionIn` — nazwa to nie token."""
        return _odrzuc_nazwe_wygladajaca_na_token(value)

    @field_validator("api_base")
    @classmethod
    def _api_base_is_https(cls, value: str) -> str:
        """Ten sam kontrakt co w :class:`GitConnectionIn` — https i bez poświadczeń w URL."""
        parsed = urlparse(value)
        if parsed.scheme != "https":
            raise ValueError("api_base musi być adresem https://.")
        if parsed.username or parsed.password or "@" in parsed.netloc:
            raise ValueError("api_base nie może zawierać poświadczeń w URL.")
        if not parsed.hostname:
            raise ValueError("api_base nie zawiera hosta.")
        return value


class SecretEntryView(BaseModel):
    """Widok wpisu magazynu sekretów — nazwa i data, NIGDY wartość ani szyfrogram."""

    name: str
    created_at: str


class SecretStoreStatusView(BaseModel):
    """Stan magazynu sekretów dla panelu (czy kreator jest dostępny)."""

    enabled: bool
    entries: list[SecretEntryView]


class GitConnectionView(BaseModel):
    """Widok połączenia Git (bez sekretu — ``token_ref`` to tylko referencja)."""

    name: str
    provider: str
    api_base: str
    username: str | None
    token_ref: str
    ca_bundle: str | None = None


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

    @field_validator("repo")
    @classmethod
    def _repo_is_safe_path(cls, value: str) -> str:
        # 'owner/name' (GitHub) lub 'grupa/.../projekt' (GitLab): segmenty [\w.-], min. dwa,
        # bez '..' — koniec wstrzykiwania '?','#',spacji do ścieżki URL.
        segments = value.split("/")
        if len(segments) < 2 or any(
            not seg or seg == ".." or not all(ch.isalnum() or ch in "._-" for ch in seg)
            for seg in segments
        ):
            raise ValueError(
                "repo musi mieć postać 'owner/name' (dozwolone znaki: litery, cyfry, . _ -)."
            )
        return value


class PullRequestView(BaseModel):
    """Wynik utworzenia PR/MR."""

    number: int | None
    url: str
    title: str


class PluginView(BaseModel):
    """Widok konektora wtyczki (bez sekretu — ``token_ref`` to tylko referencja)."""

    name: str
    transport: str
    endpoint: str
    description: str
    enabled: bool
    token_ref: str | None
    timeout_seconds: int
    max_output_bytes: int


class RemoteToolView(BaseModel):
    """Narzędzie udostępniane przez zdalny serwer MCP (wynik odkrywania)."""

    name: str
    description: str


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


class DoctorFinding(BaseModel):
    """Jedno ustalenie diagnozy instalacji (odpowiednik ``launcher.doctor.Ustalenie``).

    Pola są celowo tekstowe i gotowe do pokazania: konsola ma je WYŚWIETLIĆ, a nie
    interpretować. Interpretacja (co jest problemem, jak to naprawić) należy do jednego
    miejsca — modułu diagnozy — żeby CLI i konsola nie rozjechały się w ocenie.

    Attributes:
        id: Stabilny identyfikator kontroli (np. ``model-husarz-local-u-dostawcy``).
        state: ``ok`` / ``problem`` / ``nieznany``. Stan ``nieznany`` znaczy „nie dało
            się sprawdzić" i NIGDY nie jest zaokrąglany do ``ok``.
        severity: ``blokujaca`` / ``ostrzezenie`` / ``informacja``.
        description: Co ustalono, jednym zdaniem.
        remedy: Co operator ma zrobić. Pusty dla stanu ``ok``.
    """

    id: str
    state: str
    severity: str
    description: str
    remedy: str = ""


class DoctorReport(BaseModel):
    """Wynik diagnozy instalacji dla konsoli WWW.

    Liczniki są policzone po stronie API z TEJ SAMEJ listy, którą zwracamy, więc panel
    nie może pokazać podsumowania przeczącego swojej własnej tabeli — błąd, który
    wersja CLI popełniła na pierwszym uruchomieniu.

    Attributes:
        findings: Ustalenia posortowane wg wagi (najpierw problemy blokujące).
        blocking: Liczba problemów blokujących.
        warnings: Liczba problemów nieblokujących.
        unknown: Liczba kontroli, których NIE DAŁO SIĘ wykonać.
    """

    findings: list[DoctorFinding]
    blocking: int
    warnings: int
    unknown: int
