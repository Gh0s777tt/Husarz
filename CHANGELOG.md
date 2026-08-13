# Changelog

Wszystkie istotne zmiany w projekcie Husarz. Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Dodane (Etap 7 — konta, sesje i limity tokenów)
- Pakiet `husarz.accounts`: hashowanie haseł `scrypt` (biblioteka standardowa, bez
  zależności), magazyn kont wstrzykiwalny (`InMemory`/`File` JSON), `AccountService`
  (rejestracja gated, logowanie z sesją, TTL, `logout`, limit i zużycie tokenów).
- API kont: `POST /api/auth/register`, `/login`, `/logout`, `GET /api/auth/me`
  (rola, aktywny model czatu, `tokens_used`/`token_quota`/`tokens_remaining`).
- Uwierzytelnianie Bearer rozszerzone: akceptuje token **sesji użytkownika** oraz
  statyczny token maszynowy → `Principal(role, user_id, username)`; RBAC per użytkownik.
- Limit tokenów: `POST /api/chat` i `/api/orchestrate` zwracają **HTTP 402** po
  wyczerpaniu; zużycie doliczane z pola `usage` odpowiedzi modelu (czat).
- Konfiguracja `security.auth`: `allow_registration`, `default_user_role`,
  `default_token_quota`, `session_ttl_minutes`, `accounts_path`, seed-admin (hasło z
  referencji do sekretu). Launcher aktywuje konta i traktuje je jako uwierzytelnianie
  (nasłuch nie-loopback dozwolony z kontami).
- Konsola: modal logowania/rejestracji, pasek użytkownika (nazwa, rola, **model**,
  zużyte/limit tokenów), wylogowanie; token sesji jako Bearer (localStorage).
- `models.chat` prezentowany jako aktywny model czatu w `/api/auth/me`.
- Testy: hasła (scrypt), rejestracja/logowanie/sesje/wygasanie/limity, API kont
  (sesja jako Bearer, 402, RBAC viewer), seed-admin fail-closed.
- Dokumentacja: `docs/KONTA.md`, ADR-0009.

### Dodane (Czat lokalny + customowy model Ollama)
- **Customowy model Ollama** `husarz` (`ollama/Husarz.Modelfile`): persona hetmana
  (PL, czat + kodowanie) zaszyta w `SYSTEM`, baza wymienna przez `FROM` (domyślnie
  `qwen2.5-coder:7b`). Instrukcja: `ollama/README.md`.
- **Tryb bezpośredniego czatu** `POST /api/chat` — rozmowa z jednym modelem (szybka,
  konwersacyjna + kodowanie), obok ciężkiej orkiestracji wieloagentowej. Model z
  `models.chat` (nowe pole configu) lub `models.default`. Błędy routera mapowane na
  429/502/503; licznik `usage.chats`.
- **Model lokalny w rejestrze**: `config/models.yaml` → `husarz-local` (backend
  `ollama`, endpoint `http://localhost:11434/v1`), ustawiony jako `models.chat`.
- **Konsola — czat jak w nowoczesnym asystencie**: dymki (użytkownik/asystent),
  własny mini-renderer Markdown (nagłówki, listy, **pogrubienia**, `inline code`,
  bloki kodu ```lang``` z przyciskiem „kopiuj"), przełącznik Czat/Orkiestracja,
  historia rozmowy, Enter=wyślij. Bez zależności z CDN (airgap-safe), motyw husarski.
- `create_app`: router jest teraz przebudowywalny (`router_factory`) i dostępny dla
  `/api/chat`; przebudowa po nadpisaniu configu w runtime obejmuje router+orkiestrator.
- Testy: `/api/chat` (odpowiedź, licznik, walidacja pustych wiadomości, brak routera).
- Poprawki z przeglądu: porażka czatu audytowana jako `chat.error` (nie
  `orchestrate.error`); spójny snapshot (config, router) pod zamkiem w `/api/chat`
  i atomowa podmiana w `/api/config/runtime` (koniec przejściowego 503 przy
  równoległym przeładowaniu); testy mapowania błędów czatu (429/503/502) + RBAC
  (viewer bez `agent:run`); uwaga o stop-tokenach/parametrach w `ollama/`.

### Dodane (Etap 6 — deploy i profile)
- Obrazy: `Dockerfile` (`husarz-api`, wieloetapowy, non-root, healthcheck) oraz
  `docker/husarz-sandbox.Dockerfile` (obraz narzędzi); `.dockerignore` bez wag/sekretów.
- Docker Compose w profilach: `docker-compose.yaml` (dev, loopback) + nakładki
  `deploy/compose/{base,prod,airgap}.yml`. Prod = proxy Caddy z TLS dla
  `${HUSARZ_PUBLIC_HOST}` (domyślnie `husarzai.pl`), usługi danych w sieci wewnętrznej;
  airgap = brak WAN, dostęp tylko przez loopback.
- Manifesty Kubernetes (`deploy/k8s/`, Kustomize): NetworkPolicy **default-deny-all**
  + wąskie reguły (ingress z nginx, DNS, egress API→dane; brak `0.0.0.0/0`),
  Deployment hardened (runAsNonRoot, readOnlyRootFilesystem, drop ALL caps, seccomp),
  Service ClusterIP, Ingress TLS (cert-manager), ConfigMap (referencje) + szablon Secret.
- Launcher: flaga `--allow-insecure` (jawny opt-out fail-closed dla kontenerów).
- CI: dodane `pip-audit` (SCA) i `hadolint` + build obrazu w GitHub Actions; nowy
  `.gitlab-ci.yml` (lustro pipeline'u dla GitLaba).
- Testy bezpieczeństwa: `tests/security/test_deploy_invariants.py` — parsowanie
  compose/k8s i egzekwowanie niezmienników (deny-all, non-root, loopback, brak WAN).
- Dokumentacja: `docs/DEPLOY.md`, ADR-0008; aktualizacja README/ROADMAP/deploy.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 6)
- **Blocker: prod/airgap nie startowały** — base compose wstrzykiwał wartość tokenu,
  ale nie referencję; dodano `HUSARZ_SECURITY__AUTH__API_TOKEN_REF=env:HUSARZ_API_TOKEN`,
  więc launcher rozwiązuje token i nie odmawia nasłuchu `0.0.0.0`.
- **Hardening kontenera Compose** (dev+prod+airgap): `read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, `user 1000:1000`, `tmpfs /tmp` — lustro securityContext z k8s.
- **Redis z hasłem** (`--requirepass`, `HUSARZ_REDIS_PASSWORD`) — spójnie z Postgres/MinIO.
- **Obrazy przypięte** (koniec `:latest`): `vault:1.18`, `minio:RELEASE.*`, `husarz-api:0.1.0`;
  `pull_policy: never` na wszystkich usługach airgap.
- **CI naprawione**: GitLab `docker-build` dostał `DOCKER_HOST`/`DOCKER_TLS_CERTDIR`
  (dind); dodano `.hadolint.yaml` (świadome ignorowanie DL3008/DL3013).
- **Vault**: sprostowany komentarz — domyślny obraz startuje w `-dev`; prod wymaga
  własnego `command: [server]` + config + unseal.
- Testy: +9 regresji (hardening compose, e2e rozwiązanie tokenu prod, hasło Redis,
  pin obrazów, PSA `restricted`, sondy `/api/health`, brak `--allow-insecure` w k8s).

### Dodane (Etap 5 — API + launcher + konsola WWW)
- Pakiet `husarz.api`: `create_app(config, ...)` (FastAPI) z endpointami health,
  config/summary, agents, models, tools, audit (+`verify`), usage, orchestrate,
  config/validate+runtime. Router modeli i audyt są wstrzykiwalne (testy bez sieci).
- Konsola WWW: jednoplikowa (`api/static/console.html`, vanilla JS, theme-aware)
  serwowana pod `/` — czat, panel konfiguracji (walidacja nadpisań), agenci, audyt, monitor.
- Launcher `husarz up --profile dev --host --port` (uvicorn; importy FastAPI/uvicorn leniwe).
- Zależności: `fastapi`, `uvicorn`.
- Testy: smoke API przez `TestClient` (bez serwera/sieci), orkiestracja, walidacja
  configu, serwowanie konsoli.
- Dokumentacja: `docs/API.md`, ADR-0007; aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 5)
- **Uwierzytelnianie API (blocker):** token Bearer + RBAC na wszystkich endpointach
  poza `/api/health`. Token pochodzi z **sekretu** (`security.auth.api_token_ref`,
  `env:`/`file:`), nigdy z configu; rola z `security.auth.api_role`. Macierz:
  `config:read` (podgląd), `audit:read` (audyt), `agent:run` (orkiestracja),
  `config:write` (nadpisania runtime — tylko admin). Wstrzykiwalne do `create_app`.
- **XSS w konsoli (blocker):** wszystkie dane z API renderowane w tabelach (agenci,
  audyt) są escapowane HTML (`esc()`); dodano pole tokenu (nagłówek Bearer).
- **Fail-closed launchera:** `husarz up` odmawia nasłuchu poza loopbackiem bez tokenu
  (kod 2); `TrustedHostMiddleware` dla loopbacku (obrona przed DNS-rebindingiem).
- **Odporność `/api/orchestrate`:** błędy routera mapowane na `429`/`502`/`503`
  (nie gołe `500`); treść błędu nie wycieka.
- **Spójność liczników:** `usage.orchestrations` liczy próby (spójnie z audytem) +
  `failures`; inkrementy i `AuditLog.record` serializowane `Lock`-iem (endpointy biegną
  w puli wątków — koniec fałszywego alarmu `verify` przy współbieżności).
- **Przebudowa orkiestratora** po `POST /api/config/runtime` (koniec działania na
  starej konfiguracji); `GET /api/audit?limit` walidowany `0..10000` (`0` → pusto).
- Testy: +20 regresji (macierz RBAC, mapowanie błędów, liczniki, przebudowa,
  limit audytu, malformed body, fail-closed launchera, atomowość łańcucha pod wątkami).

### Dodane (Etap 4 — bezpieczeństwo/ROE)
- Pakiet `husarz.security`:
  - **Audit log** niemodyfikowalny z łańcuchem skrótów (`AuditLog`, `verify`,
    zapis append-only, zegar wstrzykiwalny); `build_audit_log(security)`.
  - **ROE-gate** (`RoeGate`): twarda bramka Puszkarza — aktywność ROE, okno czasowe,
    zakres (CIDR/domeny + `out_of_scope`), techniki, tryb; **dry-run domyślnie**,
    akcja aktywna wymaga `authorized=True`. Każda decyzja audytowana.
  - **Puszkarz** (`Puszkarz`): odmowa wytwarzania narzędzi ofensywnych (z propozycją
    działania defensywnego); akcje na celach wyłącznie przez ROE-gate.
  - **RBAC** (`Rbac`): role→uprawnienia z wildcardami `*` / `obszar:*`.
- Dostawcy sekretów: `FileSecretsProvider` (konfinacja), `SopsSecretsProvider`,
  `VaultSecretsProvider` (backendy wstrzykiwalne — testowalne bez sops/Vault).
- Testy (łącznie 274): łańcuch skrótów + wykrywanie manipulacji, ROE-gate (dry-run,
  `--authorized`, blok spoza zakresu/okna, techniki), odmowa ofensywy Puszkarza,
  RBAC, dostawcy sekretów + osobne niezmienniki bezpieczeństwa.
- Dokumentacja: ADR-0006; aktualizacja ARCHITEKTURA/BEZPIECZENSTWO/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 4)
- **ROE-gate:** techniki porównywane bez rozróżniania wielkości liter/spacji (koniec
  obejścia `SQLI` vs `sqli`); `RoeScope.targets_cidr` wymaga wyrównanego CIDR (strict —
  koniec cichego poszerzania zakresu); okno ROE i `now` normalizowane do UTC (koniec
  `TypeError` naive vs aware); wstrzykiwalny `signature_verifier`; `engagement_id/owner/
  authorized_by` wymagają niepustych wartości; host celu obsługuje `scheme://`.
- **Audyt:** zapis do pliku PRZED mutacją pamięci (brak rozjazdu przy błędzie I/O);
  deep-copy `detail` (niezmienność po zahashowaniu); nieserializowalny `detail` → `AuditError`;
  opcjonalny **HMAC** (`hmac_key`) dla odporności na zmotywowanego edytora; `AuditLog.load()`
  + `verify()` z pliku; `build_audit_log` odtwarza łańcuch po restarcie (ciągłość).
- **Puszkarz:** rozszerzone markery + **kontekst defensywny** (mniej fałszywych pozytywów,
  np. reguły YARA); audyt loguje **skrót** żądania, nie surową treść (ochrona PII/sekretów).
- **Sekrety:** SOPS/Vault fail-closed przy błędzie backendu (bez propagacji wyjątku
  mogącego nieść odszyfrowaną treść).
- +20 testów regresyjnych (razem 294).

### Dodane (Etap 3 — narzędzia + sandbox)
- Pakiet `husarz.tools`: `file_edit`, `shell`, `git`, `run_tests`, `web`, `rag`.
  - Konfinacja plików do workspace + deny-globi (`resolve_within_workspace`,
    matcher `**` zgodny z Py 3.11); allowlisty komend (shell), podkomend (git,
    `push` tylko przy `allow_push`), domen (web).
  - Sandbox: `SandboxSpec` + `build_docker_argv` (twarda izolacja: `--network none`,
    limity CPU/RAM, `--cap-drop ALL`, `no-new-privileges`, montaż tylko workspace,
    `--runtime runsc` dla gVisor); `SandboxExecutor` wstrzykiwalny.
  - web: dwuwarstwowy egress (allowlista domen narzędzia + globalny `security.egress`);
    `Fetcher` wstrzykiwalny. rag: `RagBackend` wstrzykiwalny (`InMemoryRagBackend`).
  - `build_tools(config, workspace, ...)` — ładowarka z `config/tools/*.yaml`.
- `SandboxConfig` rozszerzone o `image` i `runtime_class` (bez hardcode obrazu).
- Testy (łącznie 217): konfinacja/deny-globi, argv sandboxa, shell/git/run_tests
  na mockowym executorze, web (allowlista + egress) na mockowym fetcherze, rag
  in-memory, ładowarka, osobne testy bezpieczeństwa — wszystko bez Dockera/DB/sieci.
- Dokumentacja: `docs/NARZEDZIA.md`, ADR-0005; aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 3)
- **Hardening sandboxa:** `build_docker_argv` dodaje `--user` (non-root), `--read-only`
  (rootfs) + `--tmpfs /tmp`, `--pids-limit`; opcjonalny montaż workspace `:ro`; odrzuca
  obraz zaczynający się od `-`. Executor nazywa kontener i po timeout robi `docker rm -f`
  (koniec osieroconych kontenerów). Pola `SandboxConfig.run_as_user/pids_limit/read_only_rootfs`.
- **Ochrona SSRF w web:** narzędzie web odrzuca literalne adresy wewnętrzne/zarezerwowane
  (loopback/RFC1918/link-local — metadane chmury) niezależnie od allowlisty i egress.
- `file_edit`: limit `max_bytes` egzekwowany także przy odczycie; `metadata.bytes` liczy
  bajty UTF-8. Deny-globi są teraz case-insensitive (koniec obejścia `SECRET.ENV`).
- Loader: jawny `null` w `config` narzędzia nie wywraca ładowania (fallback do domyślnych).
- Testy: +27 (razem 240, 1 skip symlink na Windows) — hardening argv, SSRF, read deny-glob/
  traversal, propagacja limitów web, okablowanie loadera, extra_args, symlink escape.

### Dodane (Etap 2 — rdzeń agentów i orkiestrator „Husarz")
- Pakiet `husarz.agents`: `BaseAgent`, `Towarzysz`, `Pocztowy`, `AgentResult`,
  protokół `SupportsComplete` oraz `build_agents(config, prompts_dir)` — ładowarka
  agentów z `config/agents/*.yaml` + prompty z `prompts/*.md` (agent wyłączony
  pomijany, brak promptu = czytelny błąd).
- Pakiet `husarz.orchestrator`: hetman `Orchestrator` z pętlą plan → deleguj →
  obserwuj → refleksja → synteza; `build_orchestrator(config, router, prompts_dir)`;
  odporne parsowanie planu/refleksji (`parse_plan`/`parse_reflection`); znaczniki
  i instrukcje faz. Nieznany agent w planie jest pomijany z adnotacją.
- Testy (łącznie 151 zielone): agent + kontekst, ładowarka (repo + brak promptu +
  wyłączony), parsowanie planu/refleksji, e2e wieloagentowe na skryptowanym
  routerze, integracja `build_orchestrator` na realnej konfiguracji i promptach.
- Dokumentacja: `docs/ORKIESTRATOR.md`, ADR-0004; aktualizacja ARCHITEKTURA/AGENCI/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 2)
- **Blocker (bezpieczeństwo):** path traversal w ładowaniu promptu — `prompt_file`
  walidowany wzorcem `^[A-Za-z0-9._-]+\.md$` w schemacie + konfinacja ścieżki w loaderze.
- **Blocker (correctness):** obserwacje trafiają teraz jako kontekst do kroków z refleksji
  (wcześniej kanał `context` był martwy — kroki działały „na ślepo").
- **Izolacja treści niezaufanej:** obserwacje agentów są ogradzane i oznaczane jako dane
  (nie instrukcje) w promptach hetmana; kontekst agenta trafia do wiadomości user, a nie
  do system promptu (koniec inwersji zaufania). Flaga `security.prompt_injection_filters`
  jest teraz realnie egzekwowana (steruje izolacją).
- **ROE-gate na poziomie orkiestracji:** agent z `roe_required` nie jest delegowany bez
  aktywnego ROE (pełny ROE-gate runtime: Etap 4).
- **Parser planu/refleksji:** odporne wyłuskiwanie JSON (`raw_decode`, obce nawiasy/wiele
  obiektów), brak wyjątku na `RecursionError`, `done` odporne na string `"false"` i na brak
  klucza (domyślne wg obecności kroków), kroki tylko z niepustych pól tekstowych.
- **Router:** pole `model` z pliku agenta działa jako fallback po `routing.agent_models`.
- +29 testów regresyjnych (łącznie 180).

### Dodane (Etap 1 — router modeli)
- Pakiet `husarz.router` — warstwa OpenAI-compat (vLLM/Ollama/SGLang):
  - `select_candidates` — wybór modelu po tagach/agencie/jawnym modelu + rozwijanie
    łańcuchów fallback (odporne na cykle, tylko modele włączone).
  - `OpenAICompatClient` + wstrzykiwalny `Transport` (`HttpxTransport` w produkcji);
    `MockClient` dla backendu `mock` — testy bez sieci.
  - `ModelRouter.complete()` — selekcja → limity → wywołanie z fallbackiem przy błędzie.
  - Kontrola kosztów: clamp `max_tokens_per_request` + `RateLimiter` (token bucket,
    wstrzykiwalny zegar) dla `max_requests_per_minute`.
  - Klucz API z `ModelSpec.api_key_ref` rozwiązywany przez dostawcę sekretów.
- Zależność `httpx` (klient HTTP warstwy OpenAI-compat).
- Testy: selekcja, klient (mock transport), rate-limit, e2e fallback, integracja
  na realnej konfiguracji repo (łącznie 103 testy zielone).
- Dokumentacja: `docs/ROUTER.md`, ADR-0003 (router modeli); aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 1)
- **Bramka egress routera** (`husarz.router.egress`): deny-all na ścieżce wywołania
  modelu — zdalny host spoza `security.egress.allowlist` jest pomijany (endpointy
  lokalne/prywatne zawsze dozwolone). Wspólny helper `husarz.config.net`.
- Klient: kanoniczne `model`/`messages` nie mogą być nadpisane przez `params`/`extra`;
  `extra` nie obchodzi już clampa `max_tokens` (kontrola kosztów); `content=null`/nie-tekst
  → jasny `ModelBackendError`; `response.json()` (ValueError) opakowany w `TransportError`;
  brakujący sekret `api_key_ref` → fail-closed; klucz API `strip()`-owany.
- Selekcja: usunięto błąd obcięcia łańcucha fallback (ochrona przed cyklami przez
  zbiór odwiedzonych); reguła z pustym `match_tags` nie jest już łapaczem wszystkiego.
- Walidacja: `CostControls.*` i `ModelSpec.request_timeout_seconds` wymuszają `>= 1`
  (koniec cichego wyłączenia limitu przez 0); walidacja ról wiadomości.
- +29 testów regresyjnych (razem 132 zielone).

### Dodane (Etap 0 — szkielet + loader konfiguracji)
- Struktura repozytorium (src-layout: `src/husarz/{config,core,router,orchestrator,agents,tools,memory,security,api,launcher}`).
- System konfiguracji „zero hardcode":
  - Schematy Pydantic v2 dla wszystkich sekcji (`platform`, `models`, `routing`,
    `security`, `agents`, `tools`, `roe`) z surową walidacją (`extra="forbid"`).
  - Loader z hierarchią nadpisań: defaults → `config/*.yaml` → ENV (`HUSARZ_*`) → runtime.
  - Walidacja krzyżowa (referencje modeli/narzędzi, reguły profilu `airgap`).
  - Czytelne błędy po polsku zamiast crasha.
  - Interfejs dostawców sekretów (`none`/`env`; Vault/SOPS — zaślepki na Etap 4).
- Przykładowa konfiguracja działająca out-of-the-box (profil `dev`):
  3 modele (GLM-5.2, Bielik v3, Hermes), 7 agentów Chorągwi, 6 narzędzi, szablon ROE.
- Prompty systemowe agentów w `prompts/*.md`.
- Launcher CLI: `husarz validate` / `husarz version` (`up` — zaślepka na Etap 5).
- Narzędzia jakości: `pyproject.toml` (ruff, black, mypy `strict`, pytest),
  `.pre-commit-config.yaml` (gitleaks, ruff, black), `.gitleaks.toml`, `.gitignore`, `.env.example`.
- Testy: jednostkowe (loader, schemat, ENV, walidacja krzyżowa, sekrety, CLI)
  oraz bezpieczeństwa (niezmienniki domyślnej konfiguracji). Wszystkie zielone.
- Dokumentacja: README, SECURITY, CONTRIBUTING, docs/{ARCHITEKTURA,AGENCI,BEZPIECZENSTWO},
  ADR-0001 (układ repo), ADR-0002 (hierarchia konfiguracji), ROADMAP, CLAUDE.md.
- CI (GitHub Actions): lint + typy + testy + gitleaks.

### Poprawione (adwersaryjny przegląd wieloagentowy Etapu 0)
- Loader: walidacja narzędzi agenta działa też przy pustym rejestrze narzędzi
  (każde odwołanie = błąd), a nie jest wtedy pomijana.
- Loader: obca zmienna `HUSARZ_*` (np. `HUSARZ_HOME`) jest ignorowana zamiast
  wywracać start (przyjmowane tylko znane sekcje).
- Loader: nadpisania ENV zachowują wielkość liter w kluczach map (id modelu,
  nazwa agenta), więc identyfikatory z wielkimi literami da się nadpisać.
- Loader: wykrywanie duplikatów kluczy odporne na klucze liczbowe (normalizacja `str`).
- Loader: wymóg rejestru modeli sprawdzany po scaleniu warstw — modele mogą
  pochodzić z ENV/runtime zgodnie z zadeklarowaną hierarchią.
- Loader: docstring coercji ENV zgodny z faktycznym (bezpiecznym) zachowaniem.
- CLI: profile podkomendy `up` pochodzą z enuma `Profile` (koniec duplikacji listy).
- Docs: `README` (pełne wyjście `validate`) i `ARCHITEKTURA` (reguły `prefer`/`auto`)
  zsynchronizowane z kodem.

### Bezpieczeństwo
- Domyślne niezmienniki: deny-all egress, sandbox bez sieci, audit log
  niemodyfikowalny, szyfrowanie at-rest, zero telemetrii — pokryte testami.
- `models/`, `.env` i sekrety w `.gitignore`; `gitleaks` skonfigurowany.
- **Hardening po przeglądzie**: bazowa linia bezpieczeństwa dla profili `prod`
  i `airgap` (sandbox włączony, audyt włączony i niemodyfikowalny, szyfrowanie
  at-rest — nie można ich cicho wyłączyć); profil `airgap` wymusza lokalne
  endpointy modeli; `ROE.is_active_at()` egzekwuje okno czasowe, a `is_active`
  wymaga niepustej referencji podpisu; `ModelSpec.api_key_ref` (klucz API jako
  referencja do sekretu, nie w `params`); allowlista `gitleaks` zawężona
  (koniec ślepej plamy na `docs/` i `prompts/`).

[Unreleased]: https://github.com/Gh0s777tt/Husarz/commits/main
