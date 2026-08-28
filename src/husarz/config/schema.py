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
from typing import Any, Literal, TypeVar
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from husarz.config.evals import EvalSet
from husarz.config.net import is_local_endpoint, is_loopback_endpoint

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


class RunsConfig(_StrictModel):
    """Zbieranie METRYK przebiegów agenta (Etap 16). Domyślnie WYŁĄCZONE.

    To NIE jest telemetria. Telemetria oznacza wysyłanie danych na zewnątrz i jest w Husarzu
    twardo zakazana (``platform.telemetry_enabled``). Tu chodzi o lokalny plik pomiarowy na
    dysku operatora, którego nikt poza nim nie widzi — jedyny sposób, by odpowiedzieć na
    pytanie „czy ta zmiana promptu albo modelu poprawiła działanie agenta".

    Rekord niesie wyłącznie metryki (rodzaj tury, narzędzie, wynik, długości, tokeny) — nigdy
    treści promptów ani wyników narzędzi. Uzasadnienie: ``husarz.runs.records``.

    Attributes:
        enabled: czy zapisywać przebiegi. Opt-in, jak pętla narzędziowa i pamięć trwała.
        path: plik JSONL; ``None`` → ``data_dir/runs/runs.jsonl``.
    """

    enabled: bool = False
    path: Path | None = None


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
    # Lokalny pomiar jakości (Etap 16) — opt-in, NIE telemetria (nic nie opuszcza maszyny).
    runs: RunsConfig = Field(default_factory=RunsConfig)

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
    params: dict[str, Any] = Field(default_factory=dict)
    fallback: list[str] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _odrzuc_usuniete_pola(cls, dane: Any) -> Any:
        """Tłumaczy usunięte pola na czytelny komunikat zamiast „extra fields not permitted".

        ``weights_path`` istniało w schemacie i **nic go nie czytało** — sprawdzone
        przeszukaniem całego repozytorium: jedyne wystąpienie było w definicji pola.
        Wyglądało przy tym, jakby wskazywało silnikowi lokalne wagi, więc operator mógł
        je ustawić i uwierzyć, że coś z tego wynika. Martwe pole, które udaje działające,
        jest gorsze niż jego brak.

        Args:
            dane: Surowe dane wejściowe modelu.

        Returns:
            Dane bez zmian.

        Raises:
            ValueError: Gdy konfiguracja używa pola usuniętego.
        """
        if isinstance(dane, dict) and "weights_path" in dane:
            raise ValueError(
                "models.registry[...].weights_path zostało USUNIĘTE: pole nie było przez "
                "nic czytane, więc nie miało żadnego wpływu na działanie (mimo nazwy "
                "sugerującej wskazanie wag). Usuń je z konfiguracji. Skąd silnik bierze "
                "wagi, decyduje sam silnik — patrz ollama/README.md."
            )
        return dane


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

    @model_validator(mode="after")
    def _odrzuc_nieegzekwowany_limit_kosztu(self) -> CostControls:
        """Odrzuca `max_cost_per_task` — limit, którego NIC nie egzekwuje.

        Pomyłka była tu szczególnie prawdopodobna: OBA sąsiednie pola w tym samym bloku
        (`max_tokens_per_request`, `max_requests_per_minute`) są egzekwowane, więc operator
        miał wszelkie podstawy sądzić, że trzecie też. Nie było.

        Przyczyna jest ta sama, co przy `routing.strategy: cost`: `models.registry` nie
        przechowuje ceny modelu, więc kosztu w walucie nie ma z czego policzyć. Odrzucamy
        tak samo twardo jak tam — dwa różne zachowania dla jednej przyczyny byłyby
        niespójnością, którą ktoś odziedziczy.

        Raises:
            ValueError: Gdy limit kosztu jest ustawiony.
        """
        if self.max_cost_per_task is not None:
            raise ValueError(
                "routing.cost_controls.max_cost_per_task NIE JEST egzekwowane — żaden kod nie "
                "czyta tego pola, więc limit nie obowiązywał (choć oba sąsiednie limity w tym "
                "bloku obowiązują). Policzenie kosztu w walucie wymaga ceny modelu, której "
                "`models.registry` nie przechowuje. Usuń pole i użyj działających limitów: "
                "`max_tokens_per_request`, `max_requests_per_minute` oraz "
                "`security.auth.default_token_quota` (limit tokenów per konto)."
            )
        return self


# Strategie doboru modelu, które router NAPRAWDĘ realizuje. Reszta wartości `RoutingStrategy`
# to zapisany zamiar na kolejne etapy — i dopóki nim jest, konfiguracja ich NIE PRZYJMUJE.
#
# Powód jest ten sam, dla którego usunięto `weights_path`: ustawienie, które wygląda na
# działające i nie robi nic, jest gorsze niż jego brak. `selection.py` nie czyta pola
# `strategy` ani razu (sprawdzone przeszukaniem `src/`), więc `strategy: cost` dawało po cichu
# zachowanie `tags`, a operator miał prawo sądzić, że skonfigurował routing po koszcie.
# Dokumentacja mówiła o tym uczciwie — ale dokumentacja to najsłabsza z możliwych kontroli:
# nie czyta jej ten, kto edytuje YAML.
_ZAIMPLEMENTOWANE_STRATEGIE: frozenset[RoutingStrategy] = frozenset({RoutingStrategy.TAGS})


class RoutingConfig(_StrictModel):
    """Konfiguracja routera modeli."""

    strategy: RoutingStrategy = RoutingStrategy.TAGS
    # Domyślny model per agent (nazwa agenta -> id modelu lub "auto").
    agent_models: dict[str, str] = Field(default_factory=dict)
    rules: list[RoutingRule] = Field(default_factory=list)
    cost_controls: CostControls = Field(default_factory=CostControls)
    fallbacks_enabled: bool = True

    @model_validator(mode="after")
    def _tylko_zaimplementowane_strategie(self) -> RoutingConfig:
        """Odrzuca strategie, których router nie realizuje — zamiast cicho udawać `tags`.

        Fail-closed: lepiej nie wystartować z czytelnym komunikatem, niż działać inaczej,
        niż mówi konfiguracja. Wartości `cost`/`latency` zostają w enumie, bo są zapisanym
        zamiarem — ale ich wybór ma boleć teraz, a nie zaskoczyć przy pierwszym rachunku.

        Raises:
            ValueError: Gdy wybrano strategię jeszcze niezaimplementowaną.
        """
        if self.strategy not in _ZAIMPLEMENTOWANE_STRATEGIE:
            dostepne = ", ".join(sorted(s.value for s in _ZAIMPLEMENTOWANE_STRATEGIE))
            raise ValueError(
                f"routing.strategy='{self.strategy.value}' NIE jest jeszcze zaimplementowane — "
                f"router nie czyta tego pola i zachowałby się jak '{RoutingStrategy.TAGS.value}'. "
                f"Ustaw jedną z działających wartości ({dostepne}). Dobór po koszcie i opóźnieniu "
                f"wymaga danych o modelach, których `models.registry` dziś nie przechowuje — "
                f"pozycja jest w ROADMAP."
            )
        return self


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


# Silniki sandboxa, które kod NAPRAWDĘ realizuje. Reszta wartości `SandboxEngine` to
# zapisany zamiar albo — w przypadku `none` — obietnica, której świadomie NIE spełniamy.
#
# `none` jest odrzucane WSZĘDZIE, nie tylko w prod/airgap, i to nie z rygoryzmu: w tym
# kodzie NIE MA drogi wykonania narzędzia poza kontenerem. `build_tools` zawsze buduje
# `DockerSandboxExecutor`, więc `engine: none` po prostu nic nie robiło — a wartość
# w konfiguracji sugerowała operatorowi, że wyłączenie izolacji jest możliwe. Dodawanie
# takiej drogi byłoby poszerzeniem powierzchni ataku; usunięcie wartości jest tańsze
# i uczciwsze.
_ZAIMPLEMENTOWANE_SILNIKI: frozenset[SandboxEngine] = frozenset(
    {SandboxEngine.DOCKER, SandboxEngine.DOCKER_GVISOR}
)

# Pola USUNIĘTE ze `SandboxConfig`. Oba obiecywały konfigurowalność ograniczeń plikowych,
# której nie było: kontener dostaje DOKŁADNIE jeden montaż (workspace), a narzędzia plikowe
# przechodzą przez `resolve_within_workspace`. Konfinacja jest bezwarunkowa — nie ma czego
# wyłączać ani poszerzać, a pole sugerujące inaczej to fałszywe poczucie kontroli.
_USUNIETE_POLA_SANDBOXA: dict[str, str] = {
    "workspace_only": (
        "konfinacja do katalogu roboczego jest BEZWARUNKOWA (kontener dostaje dokładnie jeden "
        "montaż, a narzędzia plikowe i tak przechodzą przez `resolve_within_workspace`), więc "
        "pole nic nie przełączało. Usuń klucz."
    ),
    "path_allowlist": (
        "pole nie było przez nic czytane — dopisanie ścieżki NIE dawało do niej dostępu. "
        "Ograniczenia W OBRĘBIE katalogu roboczego ustawia się przez `deny_globs` "
        "w config/tools/file_edit.yaml. Usuń klucz."
    ),
}


class SandboxConfig(_StrictModel):
    """Ograniczenia sandboxa narzędzi."""

    @model_validator(mode="before")
    @classmethod
    def _odrzuc_usuniete_pola(cls, dane: Any) -> Any:
        """Czytelny komunikat zamiast „extra fields not permitted" dla usuniętych pól."""
        if not isinstance(dane, dict):
            return dane
        for pole, powod in _USUNIETE_POLA_SANDBOXA.items():
            if pole in dane:
                raise ValueError(f"security.sandbox.{pole} zostało USUNIĘTE: {powod}")
        return dane

    # Domyślnie `docker`, nie `docker+gvisor` — i to jest SPROSTOWANIE, nie osłabienie.
    # Domyślny `runtime_class` to `None`, więc zachowanie i tak zawsze było zwykłym runc;
    # deklaracja `docker+gvisor` po prostu tego nie opisywała. Wyszło, gdy nowy walidator
    # zgodności odrzucił WŁASNĄ wartość domyślną — najuczciwszy możliwy sygnał, że para
    # (silnik, runtime) była niespójna od początku. Dostarczona konfiguracja podnosi to
    # jawnie do `docker+gvisor` + `runsc`.
    engine: SandboxEngine = SandboxEngine.DOCKER
    network: bool = False  # brak sieci domyślnie
    cpu_limit: str = "1"
    memory_limit: str = "512m"
    timeout_seconds: int = 60
    command_allowlist: list[str] = Field(default_factory=list)
    # Obraz kontenera i klasa runtime (np. 'runsc' dla gVisor) — bez hardcode w executorze.
    image: str | None = None
    runtime_class: str | None = None
    # Dodatkowy hardening kontenera: użytkownik non-root, limit procesów, rootfs tylko-do-odczytu.
    run_as_user: str | None = "1000:1000"
    pids_limit: int | None = Field(default=512, ge=1)
    read_only_rootfs: bool = True

    @model_validator(mode="after")
    def _silnik_musi_zgadzac_sie_z_rzeczywistoscia(self) -> SandboxConfig:
        """Pilnuje, żeby deklarowany silnik odpowiadał temu, co naprawdę robi kontener.

        Dwie różne nieprawdy, obie sprawdzone uruchomieniem:

        1. **`engine: none` nic nie wyłączało.** `build_tools` zawsze buduje
           `DockerSandboxExecutor`, więc narzędzie i tak szło do kontenera. Wartość
           sugerowała możliwość, której w tym kodzie nie ma — i której świadomie nie
           dodajemy, bo byłaby poszerzeniem powierzchni ataku.
        2. **`engine` a `runtime_class` mogły się ROZJECHAĆ.** O gVisorze decyduje wyłącznie
           `runtime_class` (trafia do `docker run --runtime`); `engine` nie steruje niczym,
           a jest POKAZYWANY operatorowi — w linii startowej CLI i w `GET /api/config/summary`.
           Konfiguracja `engine: docker+gvisor` z pustym `runtime_class` dawała więc zwykłego
           runc, a operator czytał „docker+gvisor". Fałszywe zapewnienie o SILE izolacji.

        Raises:
            ValueError: Gdy silnik jest niezaimplementowany albo niezgodny z `runtime_class`.
        """
        if self.engine not in _ZAIMPLEMENTOWANE_SILNIKI:
            dostepne = ", ".join(sorted(s.value for s in _ZAIMPLEMENTOWANE_SILNIKI))
            powod = (
                "w tym kodzie NIE MA drogi wykonania narzędzia poza kontenerem — "
                "`build_tools` zawsze buduje executor Dockera, więc ta wartość niczego nie "
                "wyłączała. Izolacja `shell`/`git`/`run_tests` jest bezwarunkowa z założenia"
                if self.engine is SandboxEngine.NONE
                else "ten silnik nie jest zaimplementowany"
            )
            raise ValueError(
                f"security.sandbox.engine='{self.engine.value}': {powod}. "
                f"Ustaw jedną z działających wartości ({dostepne})."
            )
        # O gVisorze decyduje `runtime_class`, nie nazwa silnika — a nazwa jest pokazywana
        # operatorowi. Rozjazd między nimi to fałszywe zapewnienie o sile izolacji.
        if self.engine is SandboxEngine.DOCKER_GVISOR and not self.runtime_class:
            raise ValueError(
                "security.sandbox.engine='docker+gvisor' wymaga `runtime_class` (np. 'runsc') "
                "— to ONO trafia do `docker run --runtime`, a nie nazwa silnika. Bez niego "
                "kontener biegnie na zwykłym runc, a linia startowa i `GET /api/config/summary` "
                "meldowałyby gVisora. Ustaw `runtime_class: runsc` albo `engine: docker`."
            )
        if self.engine is SandboxEngine.DOCKER and self.runtime_class:
            raise ValueError(
                f"security.sandbox.engine='docker' z `runtime_class: {self.runtime_class}` — "
                f"kontener użyłby tego runtime'u, a operator czytałby „docker\". Jeśli chodzi "
                f"o gVisora, ustaw `engine: docker+gvisor`."
            )
        return self


class MtlsConfig(_StrictModel):
    """mTLS między usługami (referencje do sekretów, nie same materiały).

    **NIEZAIMPLEMENTOWANE (Etap 6).** Sekcja jest zapisanym zamiarem; ustawienie
    ``enabled: true`` jest ODRZUCANE przy starcie. Powód jest poważniejszy niż zwykłe
    „pole nic nie robi": konfiguracja przyjmująca `true` dawała operatorowi fałszywe
    poczucie, że kanał jest szyfrowany i klient uwierzytelniony — podczas gdy API nasłuchuje
    po zwykłym HTTP, a token Bearer jedzie jawnym tekstem.
    """

    enabled: bool = False
    ca_cert_ref: str | None = None
    cert_ref: str | None = None
    key_ref: str | None = None

    @model_validator(mode="after")
    def _odrzuc_wlaczenie_niezaimplementowanego(self) -> MtlsConfig:
        """Fail-closed: lepiej nie wystartować, niż wystartować z fałszywym poczuciem TLS-u.

        Raises:
            ValueError: Gdy mTLS jest włączony.
        """
        if self.enabled:
            raise ValueError(
                "security.mtls.enabled=true, ale mTLS NIE JEST zaimplementowany (Etap 6) — "
                "serwer nasłuchuje po zwykłym HTTP, bez szyfrowania i bez weryfikacji "
                "certyfikatu klienta, więc token Bearer szedłby jawnym tekstem. Ustaw "
                "`enabled: false` i zakończ TLS na reverse-proxy przed Husarzem."
            )
        return self


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
        # OIDC jest zapisanym zamiarem (Etap 6), nie funkcją. Przyjmowanie `true` dawałoby
        # operatorowi fałszywe poczucie, że tożsamość jest weryfikowana u dostawcy — podczas
        # gdy API uwierzytelnia wyłącznie tokenem Bearer i kontami lokalnymi.
        if self.oidc_enabled:
            raise ValueError(
                "security.auth.oidc_enabled=true, ale przepływ OIDC NIE JEST zaimplementowany "
                "(Etap 6) — API uwierzytelnia wyłącznie tokenem Bearer (`api_token_ref`) i "
                "kontami lokalnymi. Ustaw `oidc_enabled: false` i użyj jednego z tych "
                "mechanizmów."
            )
        if bool(self.seed_admin_username) != bool(self.seed_admin_password_ref):
            raise ValueError(
                "security.auth: seed_admin_username i seed_admin_password_ref muszą być "
                "ustawione RAZEM (albo oba, albo żadne)."
            )
        return self


class AuditConfig(_StrictModel):
    """Dopisujący dziennik audytu z łańcuchem skrótów.

    Nazwa „niemodyfikowalny" bywała w tym projekcie używana bez zastrzeżenia i była
    nieprecyzyjna: dziennik jest **tamper-evident**, nie niemodyfikowalny. Edycja wpisu
    jest wykrywana przez łańcuch, usunięcie końcówki — przez kotwicę (Etap 17n), a przed
    kimś, kto ma prawo zapisu i chce przekuć CAŁY łańcuch, chroni dopiero ``hmac_key_ref``.

    Attributes:
        enabled: Czy dziennik działa. W profilach prod/airgap nie da się wyłączyć.
        path: Ścieżka pliku JSONL. Obok powstaje kotwica (``<path>.kotwica``).
        immutable: Bramka PROFILU — prod/airgap nie wystartują z ``false``. Nie ustawia
            uprawnień pliku ani flag systemowych: niemodyfikowalność jest własnością
            konstrukcji dziennika, nie tego przełącznika.
        hash_chain: Łańcuch skrótów. Działa ZAWSZE — pole jest zaszłością i nic nie
            przełącza (sprawdzone uruchomieniem).
        hmac_key_ref: **Referencja** do klucza HMAC (nigdy sam klucz). Bez niego łańcuch
            to goły SHA-256: każdy, kto ma prawo zapisu do pliku, może przeliczyć go od
            nowa i podmienić historię tak, że ``verify()`` niczego nie zauważy. Z kluczem
            trzymanym POZA systemem plików staje się to niewykonalne bez tego klucza.
    """

    enabled: bool = True
    path: Path = Path("./audit/audit.log")
    immutable: bool = True
    hash_chain: bool = True  # łańcuch skrótów (tamper-evidence)
    hmac_key_ref: str | None = None

    @model_validator(mode="after")
    def _validate_hmac_ref(self) -> AuditConfig:
        """Pilnuje, żeby w konfiguracji była REFERENCJA, nie materiał klucza.

        Schematy ZEWNĘTRZNE tylko: klucz integralności audytu nie może pochodzić
        z zapisywalnego magazynu Husarza, bo ten magazyn jest częścią systemu, którego
        dziennik ma pilnować.

        Raises:
            ValueError: Gdy podano materiał zamiast referencji albo schemat wewnętrzny.
        """
        if self.hmac_key_ref is None:
            return self
        wartosc = self.hmac_key_ref.strip()
        if not wartosc.startswith(_EXTERNAL_REF_SCHEMES):
            raise ValueError(
                "security.audit.hmac_key_ref musi być referencją do sekretu ZEWNĘTRZNEGO "
                "(env:/file:/vault:/sops:), a nie samym materiałem klucza. Schemat 'husarz:' "
                "jest tu zabroniony: klucz integralności audytu nie może pochodzić z magazynu "
                "należącego do systemu, którego dziennik ma pilnować."
            )
        object.__setattr__(self, "hmac_key_ref", wartosc)
        return self


class EncryptionConfig(_StrictModel):
    """Szyfrowanie at-rest."""

    at_rest: bool = True
    algorithm: str = "AES-256-GCM"


class RoeSignatureConfig(_StrictModel):
    """Weryfikacja kryptograficznego podpisu ROE (dokumentu autoryzującego Puszkarza).

    ROE to jedyny artefakt uprawniający do aktywnych działań wobec konkretnych celów, więc
    jego integralność jest bramką bezpieczeństwa, nie formalnością. Domyślnie weryfikacja
    jest **włączona** (fail-closed): bez poprawnego podpisu ROE pozostaje nieaktywne.

    ``key_ref`` to REFERENCJA do sekretu (``env:``/``file:``/``vault:``/``sops:``), nigdy
    materiał klucza. Dla ``hmac-sha256`` wskazuje sekret współdzielony; dla ``ed25519`` —
    klucz PUBLICZNY (PEM albo base64 32 bajtów), który nie jest tajny, ale i tak trzymamy
    go poza plikiem konfiguracji, by wymiana klucza nie wymagała edycji configu.
    """

    # Wyłączenie jest możliwe TYLKO w profilu dev (patrz _cross_validate) — w prod/airgap
    # bramka bez weryfikacji podpisu byłaby autoryzacją opartą na dowolnym tekście.
    verify_signature: bool = True
    algorithm: Literal["hmac-sha256", "ed25519"] = "ed25519"
    key_ref: str | None = None

    @field_validator("key_ref")
    @classmethod
    def _key_is_reference(cls, value: str | None) -> str | None:
        """Odrzuca surowy materiał klucza — dopuszczalna jest wyłącznie referencja."""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not cleaned.startswith(_SECRET_REF_SCHEMES):
            raise ValueError(
                "security.roe.key_ref musi być referencją do sekretu "
                "(env:/file:/vault:/sops:/husarz:), a nie samym materiałem klucza."
            )
        return cleaned


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


class SecretStoreConfig(_StrictModel):
    """Zapisywalny magazyn sekretów — dla materiału, który Husarz DOSTAJE w czasie działania.

    Dotychczasowi dostawcy sekretów są wyłącznie do odczytu: operator sam umieszcza token
    w ENV, pliku, Vaulcie albo SOPS-ie. Nie obsługują przypadku, w którym token powstaje
    podczas pracy — wklejony w kreatorze połączeń albo zwrócony przez OAuth. Bez tego
    magazynu taki token musiałby wylądować w pliku konfiguracji (złamanie zasady „config
    nie zawiera materiału") albo tylko w pamięci procesu (utrata po restarcie).

    Magazyn zapisuje materiał ZASZYFROWANY (AES-256-GCM) w osobnym pliku i zwraca referencję
    ``husarz:<nazwa>``. Do konfiguracji trafia wyłącznie ta referencja — niezmiennik zostaje
    nienaruszony. Szczegóły i model zagrożeń: :mod:`husarz.security.secret_store`.

    Domyślnie **wyłączony** (deny-by-default): instalacja, która nie potrzebuje zapisywania
    sekretów, nie ma powierzchni zapisu. Włączenie WYMAGA ``key_ref`` — nie istnieje tryb
    „zapisz jawnie", bo plik z tokenami wyglądałby wtedy identycznie niezależnie od tego,
    czy cokolwiek chroni jego zawartość.

    Attributes:
        enabled: czy magazyn działa. Wymaga ``key_ref``.
        path: plik magazynu; ``None`` → ``data_dir/secrets/store.json``.
        key_ref: referencja do klucza głównego. Wyłącznie schemat ZEWNĘTRZNY
            (``env:``/``file:``/``vault:``/``sops:``) — klucz z samego magazynu byłby
            zamkniętym kręgiem, w którym nic nie da się odszyfrować.
    """

    enabled: bool = False
    path: Path | None = None
    key_ref: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> SecretStoreConfig:
        if self.key_ref is not None:
            cleaned = self.key_ref.strip()
            if not cleaned.startswith(_EXTERNAL_REF_SCHEMES):
                raise ValueError(
                    "security.secret_store.key_ref musi być referencją do sekretu "
                    "ZEWNĘTRZNEGO (env:/file:/vault:/sops:). Schemat 'husarz:' jest tu "
                    "zabroniony — magazyn nie może odblokowywać się własnym sekretem."
                )
        if self.enabled and not self.key_ref:
            raise ValueError(
                "security.secret_store.enabled=true wymaga key_ref (klucza głównego). "
                "Bez klucza magazyn nie powstanie — nie ma trybu zapisu jawnym tekstem."
            )
        return self


class DiagnosticsConfig(_StrictModel):
    """Diagnoza wystawiona przez API (``GET /api/doctor``).

    **Po co limit tempa.** Każde wywołanie diagnozy OTWIERA połączenia wychodzące do
    endpointów z konfiguracji — po jednym na endpoint, z limitem czasu. Bez ograniczenia
    tempa uprawnienie `diagnostics:read` byłoby dźwignią: żądanie tanie dla wywołującego,
    kosztowne dla instalacji i dla silników, do których Husarz się odzywa.

    Limit dobrany pod CZŁOWIEKA klikającego „Sprawdź ponownie", nie pod automat. Sześć
    wywołań na minutę to jedno na dziesięć sekund — swobodnie wystarcza operatorowi, który
    coś poprawił i chce zobaczyć skutek, a nie pozwala robić z endpointu generatora ruchu.

    Attributes:
        max_requests_per_minute: Ile wywołań ``GET /api/doctor`` na minutę. ``None``
            wyłącza limit — dopuszczalne świadomie (np. instalacja jednoosobowa na
            loopbacku), ale to REZYGNACJA z zabezpieczenia, nie jego brak.
    """

    max_requests_per_minute: int | None = Field(default=6, ge=1)


class SecurityConfig(_StrictModel):
    """Zbiorcza konfiguracja bezpieczeństwa."""

    egress: EgressConfig = Field(default_factory=EgressConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    mtls: MtlsConfig = Field(default_factory=MtlsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    encryption: EncryptionConfig = Field(default_factory=EncryptionConfig)
    tool_loop: ToolLoopConfig = Field(default_factory=ToolLoopConfig)
    roe: RoeSignatureConfig = Field(default_factory=RoeSignatureConfig)
    secret_store: SecretStoreConfig = Field(default_factory=SecretStoreConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
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


_SettingsT = TypeVar("_SettingsT", bound=BaseModel)


# --- Typowane ustawienia per rodzaj narzędzia --------------------------------
# Dotąd `ToolConfig.config` było nietypowaną mapą, walidowaną dopiero przy budowie
# narzędzia — a właściwie wcale. Skutek był gorszy niż literówki: dostarczana
# konfiguracja zawierała klucze, których NIKT nie czyta, w tym takie, które wyglądają
# jak kontrole bezpieczeństwa (`shell.network`, `cpu_limit`, `memory_limit`). Operator
# ustawiający `network: false` mógł sądzić, że wyłączył sieć narzędziu — a jedynym
# realnym sterowaniem jest `security.sandbox`. Modele poniżej (extra="forbid") zamieniają
# taki cichy no-op w błąd przy starcie.


class FileEditSettings(_StrictModel):
    """Ustawienia narzędzia ``file_edit``. Katalog roboczy pochodzi z ``platform.workspace_dir``."""

    deny_globs: list[str] = Field(default_factory=list)
    max_file_bytes: int = Field(default=5_000_000, ge=1)


class ShellSettings(_StrictModel):
    """Ustawienia narzędzia ``shell`` — świadomie PUSTE.

    Sieć, limity CPU/RAM/PID i timeout pochodzą WYŁĄCZNIE z ``security.sandbox`` (jedno
    źródło prawdy dla izolacji). Duplikat per narzędzie dawałby dwa miejsca do rozjechania,
    a przy kontroli bezpieczeństwa to jedno za dużo.
    """


class GitToolSettings(_StrictModel):
    """Ustawienia narzędzia ``git``. ``push`` wymaga jawnej zgody (i egressu)."""

    allow_push: bool = False


class RunTestsSettings(_StrictModel):
    """Ustawienia narzędzia ``run_tests``. Timeout i limity — z ``security.sandbox``."""

    command: str = "pytest -q"


class WebToolSettings(_StrictModel):
    """Ustawienia narzędzia ``web``. Metoda jest stała (GET) — pobieranie, nie wysyłanie."""

    max_bytes: int = Field(default=2_000_000, ge=1)
    timeout_seconds: int = Field(default=20, ge=1)


class PluginToolSettings(_StrictModel):
    """Ustawienia narzędzia ``kind: plugin`` — wiąże JEDEN konektor z ``config/plugins/``."""

    plugin: str = Field(min_length=1)
    max_output_bytes: int = Field(default=100_000, ge=1)


# Pola USUNIĘTE ze schematu wraz z powodem. Komunikat migracyjny mówi, co zniknęło i dlaczego —
# samo `extra="forbid"` dałoby „extra fields not permitted": prawdę, która niczego nie tłumaczy.
_USUNIETE_POLA_NARZEDZIA: dict[str, str] = {
    "requires_sandbox": (
        "pole nie było przez nic czytane (zero odwołań w `src/`) i — co gorsza — stawiało "
        "twierdzenie NIEPRAWDZIWE: `web` i `file_edit` deklarowały `true`, a oba działają "
        "W PROCESIE Husarza, nie w kontenerze. W sandboxie biegną WYŁĄCZNIE `shell`, `git` "
        "i `run_tests`, i to bezwarunkowo — nie ma czego włączać ani wyłączać. Usuń klucz; "
        "co chroni pozostałe narzędzia, opisuje docs/NARZEDZIA.md."
    ),
}


class ToolConfig(_StrictModel):
    """Definicja narzędzia dostępnego dla agentów."""

    name: str
    kind: str  # web | shell | file_edit | git | run_tests | rag | custom
    description: str = ""
    enabled: bool = True
    requires_egress: bool = False
    # Allowlista: domeny (web), komendy (shell), ścieżki (file_edit) itd.
    allowlist: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    # Sparsowane, TYPOWANE ustawienia (wynik walidacji `config` wobec modelu rodzaju).
    # Prywatne, bo nie jest polem wejściowym YAML-a — pochodzi wyłącznie z `config`.
    _settings: BaseModel | None = PrivateAttr(default=None)

    @model_validator(mode="before")
    @classmethod
    def _odrzuc_usuniete_pola(cls, dane: Any) -> Any:
        """Tłumaczy usunięte pola na czytelny komunikat zamiast „extra fields not permitted".

        Args:
            dane: Surowe dane wejściowe modelu.

        Returns:
            Dane bez zmian.

        Raises:
            ValueError: Gdy konfiguracja używa pola usuniętego.
        """
        if not isinstance(dane, dict):
            return dane
        for pole, powod in _USUNIETE_POLA_NARZEDZIA.items():
            if pole in dane:
                raise ValueError(f"tools[...].{pole} zostało USUNIĘTE: {powod}")
        return dane

    def settings_as(self, model: type[_SettingsT]) -> _SettingsT:
        """Zwraca typowane ustawienia narzędzia; ``model`` musi pasować do ``kind``.

        Args:
            model: klasa ustawień oczekiwana przez buildera danego rodzaju.

        Returns:
            Zwalidowany obiekt ustawień (nigdy ``None`` — walidacja zaszła przy starcie).

        Raises:
            ValueError: rodzaj narzędzia nie pasuje do żądanego modelu (błąd programisty,
                nie konfiguracji — rejestr builderów wiąże `kind` z modelem 1:1).
        """
        if not isinstance(self._settings, model):
            raise ValueError(
                f"Narzędzie '{self.name}': ustawienia rodzaju '{self.kind}' nie są "
                f"typu {model.__name__}."
            )
        return self._settings

    @model_validator(mode="after")
    def _validate_settings(self) -> ToolConfig:
        """Waliduje ``config`` wobec modelu właściwego dla ``kind`` (fail-fast przy starcie).

        Klucz o wartości ``null`` traktujemy jak brak (→ wartość domyślna ze schematu),
        spójnie z dotychczasowym zachowaniem builderów.
        """
        model = _TOOL_SETTINGS_MODELS.get(self.kind)
        if model is None:
            return self  # nieznany kind — błąd zgłosi build_tools (zachowanie bez zmian)
        provided = {key: value for key, value in self.config.items() if value is not None}
        try:
            self._settings = model(**provided)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in err['loc']) or '?'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ValueError(
                f"Narzędzie '{self.name}' (kind: {self.kind}) — błędna sekcja 'config': "
                f"{details}. Nieznany klucz oznacza ustawienie, którego NIKT nie czyta."
            ) from exc
        return self

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

# Schematy referencji do sekretów akceptowane w konfiguracji. `husarz:` wskazuje
# ZAPISYWALNY magazyn (husarz.security.secret_store) — jedyny schemat, pod który materiał
# trafia przez samego Husarza (kreator połączeń, OAuth), a nie ręką operatora.
_SECRET_REF_SCHEMES = ("env:", "file:", "vault:", "sops:", "husarz:")
# Klucz GŁÓWNY magazynu nie może pochodzić z magazynu — to zamknięty krąg, w którym
# nic nie da się odszyfrować. Dopuszczamy więc dla niego wyłącznie schematy zewnętrzne.
_EXTERNAL_REF_SCHEMES = ("env:", "file:", "vault:", "sops:")


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
                "(env:/file:/vault:/sops:/husarz:), a nie samą wartością."
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
                "encryption_key_ref musi być referencją do sekretu "
                "(env:/file:/vault:/sops:/husarz:)."
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


# Mapa `kind` -> model ustawień. Definiowana TUTAJ, bo `RagBackendConfig` powstaje po
# `ToolConfig`; walidator sięga po nią w czasie wywołania, nie definicji klasy.
# Nowy rodzaj narzędzia = builder w rejestrze + jedna pozycja poniżej (zasada open/closed).
_TOOL_SETTINGS_MODELS: dict[str, type[BaseModel]] = {
    "file_edit": FileEditSettings,
    "shell": ShellSettings,
    "git": GitToolSettings,
    "run_tests": RunTestsSettings,
    "web": WebToolSettings,
    "rag": RagBackendConfig,
    "plugin": PluginToolSettings,
}


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
    # --- Wywołanie zdalnych narzędzi (tools/call) — deny-by-default (ADR-0019) ------------
    allow_call: bool = False  # master-switch: bez tego 'call' jest odmawiane (list działa)
    call_allowlist: list[str] = Field(default_factory=list)  # dozwolone nazwy zdalnych narzędzi
    max_call_bytes: int = Field(default=64_000, ge=1)  # cap zserializowanych params PRZED egress

    @field_validator("call_allowlist", mode="after")
    @classmethod
    def _strip_call_allowlist(cls, value: list[str]) -> list[str]:
        # Normalizacja: runtime porównuje czystą nazwę narzędzia (name z koperty JSON-RPC),
        # więc wpisy z otaczającymi białymi znakami dawałyby cichą odmowę — przycinamy je tu.
        return [entry.strip() for entry in value]

    @model_validator(mode="after")
    def _validate(self) -> PluginConfig:
        if self.token_ref is not None and not self.token_ref.startswith(_SECRET_REF_SCHEMES):
            raise ValueError(
                f"Wtyczka '{self.name}': token_ref musi być referencją do sekretu "
                f"(env:/file:/vault:/sops:/husarz:), a nie samą wartością tokenu."
            )
        # Fail-closed: nie da się wystartować z 'otwartym' wywoływaniem. allow_call wymaga
        # jawnej enumeracji dozwolonych narzędzi (pusta lista + allow_call=false = kill-switch OK).
        if self.allow_call and not self.call_allowlist:
            raise ValueError(
                f"Wtyczka '{self.name}': allow_call=true wymaga niepustej call_allowlist "
                f"(jawna enumeracja dozwolonych zdalnych narzędzi — deny-by-default)."
            )
        if len(set(self.call_allowlist)) != len(self.call_allowlist) or any(
            not entry.strip() for entry in self.call_allowlist
        ):
            raise ValueError(
                f"Wtyczka '{self.name}': call_allowlist bez duplikatów i pustych wpisów."
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

    @field_validator("targets_cidr", "targets_domains", "out_of_scope")
    @classmethod
    def _strip_entries(cls, value: list[str]) -> list[str]:
        """Przycina białe znaki i kropkę końcową FQDN — runtime porównuje wpisy dosłownie.

        Bez tego wpis ``" 192.0.2.1 "`` albo ``"app.example.local."`` nie dopasowałby się
        do celu. Dla ``targets_*`` skutkiem byłoby ciche zawężenie (nieszkodliwe), ale dla
        ``out_of_scope`` — ciche ZNIKNIĘCIE wykluczenia, czyli poszerzenie uprawnień.
        """
        return [entry.strip().rstrip(".") for entry in value]

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
        # out_of_scope ma ODWROTNĄ polaryzację niż targets_*: wpis, który się nie dopasuje,
        # nie zawęża zakresu, tylko ODSŁANIA host, który miał być chroniony. Niewyrównany
        # CIDR ('192.0.2.5/29') albo pusty wpis byłby cichym no-opem — i to na dokumencie,
        # który operator podpisuje kryptograficznie. Dlatego walidujemy fail-closed.
        for entry in self.out_of_scope:
            if not entry:
                raise ValueError("out_of_scope: pusty wpis (usuń go lub podaj cel).")
            if "/" not in entry:
                continue  # domena albo pojedynczy adres — dopasowanie łańcuchowe/IP
            try:
                ipaddress.ip_network(entry, strict=True)
            except ValueError as exc:
                raise ValueError(
                    f"out_of_scope: '{entry}' nie jest poprawną, wyrównaną siecią CIDR — "
                    f"wykluczenie byłoby CICHO IGNOROWANE (dla pojedynczego hosta użyj "
                    f"samego adresu albo /32)."
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

        Uwaga: nie sprawdza okna czasowego (``is_active_at``) ANI kryptograficznej ważności
        podpisu — schemat nie ma dostępu do dostawcy sekretów i nie jest miejscem na operacje
        kryptograficzne. Realną weryfikację wykonuje ``RoeGate`` przez weryfikator zbudowany
        przez ``husarz.security.build_roe_verifier`` (ADR-0021); tutaj sprawdzamy jedynie, czy
        pole podpisu w ogóle wypełniono — to warunek KONIECZNY, nie wystarczający.
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


class BootstrapConfig(_StrictModel):
    """Pobieranie wag modeli przy pierwszym uruchomieniu (config/bootstrap.yaml).

    **Domyślnie WYŁĄCZONE.** Husarz nie ściąga niczego, dopóki operator tego nie włączy
    i nie potwierdzi konkretnego pobrania — zgodnie z zasadą suwerenności danych.

    **Czego ten mechanizm NIE robi:** nie pobiera ani nie instaluje SILNIKA. Wagi ściąga
    silnik operatora (``POST /api/pull`` do Ollamy), a Husarz jedynie o to prosi i pilnuje
    zgody. Dzięki temu nie dotykamy cudzego kodu wykonywalnego, sum kontrolnych binarek ani
    ścieżek instalacyjnych per system. Instalacja silnika należy do menedżera pakietów.

    **Dlaczego ``sources`` jest OSOBNE od ``security.egress.allowlist``.** Zgoda na czytanie
    manifestu z rejestru modeli nie może po cichu otwierać tej domeny narzędziu ``web``,
    wtyczkom MCP ani agentom. Dwie listy = dwie różne decyzje operatora.

    Attributes:
        enabled: Czy komenda ``husarz bootstrap`` w ogóle działa.
        registry: Baza URL rejestru, z którego czytamy MANIFEST (rozmiar przed pobraniem).
            Sam manifest to kilkaset bajtów; wag Husarz nie pobiera. ``None`` = brak
            możliwości podania rozmiaru, czyli brak zgody opartej na faktach — komenda
            wtedy odmawia zamiast zgadywać.
        sources: Allowlista hostów dla zapytań o manifest. Pusta = nic nie wolno.
    """

    enabled: bool = False
    registry: str | None = None
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> BootstrapConfig:
        # Ta sama kontrola co dla `security.egress.allowlist`: wpis musi być czystą nazwą
        # hosta. Pusty wpis stałby się częściowym wildcardem (`host.endswith('.')`).
        normalized: list[str] = []
        for entry in self.sources:
            host = entry.strip().lower()
            if not host:
                raise ValueError("bootstrap.sources zawiera pusty wpis (usuń go).")
            if "/" in host or "@" in host or ":" in host or host != entry.strip():
                raise ValueError(
                    f"bootstrap.sources['{entry}'] musi być samą nazwą hosta "
                    f"(bez schematu/portu/ścieżki/poświadczeń)."
                )
            normalized.append(host)
        object.__setattr__(self, "sources", normalized)
        # Fail-closed: włączony bootstrap bez rejestru albo bez allowlisty jest atrapą,
        # która odmówi przy pierwszym użyciu. Lepiej powiedzieć to przy starcie.
        if self.enabled and not self.registry:
            raise ValueError(
                "bootstrap.enabled=true wymaga bootstrap.registry — bez rejestru nie da się "
                "podać rozmiaru PRZED pobraniem, a zgoda bez rozmiaru nie jest zgodą."
            )
        if self.enabled and not self.sources:
            raise ValueError(
                "bootstrap.enabled=true wymaga bootstrap.sources (allowlista hostów). "
                'Pusta lista znaczy „nic nie wolno" i komenda i tak by odmówiła.'
            )
        return self


class HusarzConfig(_StrictModel):
    """Kompletna, zwalidowana konfiguracja platformy Husarz."""

    platform: PlatformConfig = Field(default_factory=PlatformConfig)
    models: ModelsConfig
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    bootstrap: BootstrapConfig = Field(default_factory=BootstrapConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    plugins: dict[str, PluginConfig] = Field(default_factory=dict)
    roe: dict[str, RoeConfig] = Field(default_factory=dict)
    # Zestawy ewaluacyjne (Etap 16) — deterministyczny pomiar poprawności routingu
    # i bramki narzędziowej. Puste = brak pomiarów; nie wpływa na runtime.
    evals: dict[str, EvalSet] = Field(default_factory=dict)

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
            # `engine: none` NIE jest tu sprawdzany, bo walidator pola `SandboxConfig`
            # odrzuca tę wartość w KAŻDYM profilu — jest ściśle silniejszy. Sprawdzone:
            # nawet podstawienie przez `model_copy` nie omija go, bo Pydantic rewaliduje
            # zagnieżdżony model. Zostawienie martwej gałęzi „na wszelki wypadek" byłoby
            # tym samym, co reszta pól usuniętych w Etapie 17m: kodem, który wygląda na
            # działającą kontrolę.
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
            # ROE autoryzuje AKTYWNE działania Puszkarza wobec konkretnych celów. Bez
            # weryfikacji podpisu „autoryzacją" jest dowolny tekst w polu signature, czyli
            # każdy, kto edytuje plik, może poszerzyć zakres ataku. Wymagamy tego jednak
            # tylko wtedy, gdy istnieje ZLECENIE ZE ZGODĄ (consent=true) — szablon bez zgody
            # jest nieszkodliwy i nie ma sensu żądać klucza od wdrożeń, które nie prowadzą
            # testów. W profilu dev zostawiamy pełną elastyczność.
            consented = sorted(name for name, roe in self.roe.items() if roe.consent)
            if consented:
                zlecenia = ", ".join(consented)
                if not self.security.roe.verify_signature:
                    errors.append(
                        f"Profil '{profile_name}' z aktywnym ROE ({zlecenia}) wymaga "
                        f"weryfikacji podpisu (security.roe.verify_signature=true)."
                    )
                elif not self.security.roe.key_ref:
                    errors.append(
                        f"Profil '{profile_name}' z aktywnym ROE ({zlecenia}): weryfikacja "
                        f"podpisu wymaga security.roe.key_ref (referencji do klucza)."
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
            # 8) Wtyczki MCP: w airgap włączona wtyczka MUSI być LOOPBACK (nie tylko „lokalna").
            #    Ściślej niż modele — runtime konektora i tak przepuszcza tylko loopback dla
            #    hostów spoza allowlisty (w airgap pusta), więc start i runtime są spójne, a
            #    dane wtyczki gwarantowanie nie opuszczają hosta (M2 z audytu ADR-0019).
            for plugin_name, plugin_cfg in self.plugins.items():
                if plugin_cfg.enabled and not is_loopback_endpoint(plugin_cfg.endpoint):
                    errors.append(
                        f"Profil airgap: wtyczka '{plugin_name}' ma nielokalny endpoint "
                        f"'{plugin_cfg.endpoint}'. W airgap wtyczka MCP musi być loopback."
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

        # 9) Narzędzie kind=plugin MUSI wskazywać ISTNIEJĄCY konektor przez config.plugin.
        #    Łapiemy tylko literówkę referencji na starcie (fail-closed na realny błąd config).
        #    enabled/allow_call konektora to runtime kill-switche (łagodna degradacja do ok=False),
        #    więc NIE wymagamy ich tutaj (parytet z git.allow_push — patrz ADR-0019).
        for tool_name, tool_cfg in self.tools.items():
            if tool_cfg.kind != "plugin" or not tool_cfg.enabled:
                continue
            ref = str(tool_cfg.config.get("plugin") or "").strip()
            if not ref:
                errors.append(
                    f"Narzędzie '{tool_name}' (kind plugin) wymaga config.plugin "
                    f"(nazwa konektora z config/plugins/)."
                )
            elif ref not in self.plugins:
                errors.append(
                    f"Narzędzie '{tool_name}' odwołuje się do nieznanej wtyczki '{ref}' "
                    f"(brak w config/plugins/)."
                )

        if errors:
            raise ValueError("Błędy walidacji krzyżowej konfiguracji:\n- " + "\n- ".join(errors))
        return self
