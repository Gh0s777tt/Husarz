# Changelog

Wszystkie istotne zmiany w projekcie Husarz. Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

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
