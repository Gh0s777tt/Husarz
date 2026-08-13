# Roadmapa — Husarz

Realizacja etapami. Po KAŻDYM etapie: **testy → wpis do docs (+CHANGELOG) → commit**.
Legenda: ✅ ukończone · 🚧 w toku · ⬜ zaplanowane.

## ✅ Etap 0 — Szkielet + loader konfiguracji
- ✅ Struktura katalogów, `pyproject`, lint (ruff), format (black), typy (mypy strict), pre-commit (gitleaks).
- ✅ Loader konfiguracji z walidacją Pydantic i hierarchią nadpisań.
- ✅ Walidacja krzyżowa referencji + reguły profilu `airgap`.
- ✅ Przykładowe configi działające out-of-the-box (profil dev).
- ✅ Launcher CLI: `validate`/`version`.
- ✅ Testy: ładowanie i walidacja configów + niezmienniki bezpieczeństwa.

## ✅ Etap 1 — Router modeli
- ✅ Warstwa OpenAI-compat do vLLM/Ollama/SGLang; jeden klient + wstrzykiwalny transport.
- ✅ Rejestr modeli z `models.yaml`; wybór po tagach/agencie/jawnym modelu.
- ✅ Fallbacki (odporne na cykle) i kontrola kosztów (clamp `max_tokens`, rate limit).
- ✅ Konsumpcja `ModelSpec.api_key_ref` (dostawca sekretów) i `request_timeout_seconds`.
- ✅ Testy: selekcja, klient (mock transport), rate-limit, e2e fallback, integracja.

## ✅ Etap 2 — Rdzeń agentów i orkiestrator
- ✅ Ładowarka agentów z `config/agents/*.yaml`; klasy `Towarzysz`/`Pocztowy`.
- ✅ Orkiestrator „Husarz": pętla plan → deleguj → obserwuj → refleksja → synteza.
- ✅ Agenci wołani przez router (Bielik/Kopijnik/Zwiadowca itd.) na mockach modeli.
- ✅ Testy: e2e zadania wieloagentowego + integracja `build_orchestrator`.

## ✅ Etap 3 — Narzędzia + sandbox
- ✅ `file_edit`, `shell`, `git`, `run_tests`, `web` (allowlist domen), `rag`
  (in-memory; pgvector w produkcji) — executor/fetcher/backend wstrzykiwalne.
- ✅ `SandboxConfig` rozszerzone o `image`/`runtime_class`; `build_docker_argv`
  egzekwuje `--network none`, limity, `--cap-drop ALL`, montaż tylko workspace.
- ✅ Testy: izolacja sandbox (argv), konfinacja i deny-globi, blokada spoza
  allowlisty, dwuwarstwowy egress — wszystko bez Dockera/DB/sieci.

### Pozostałe z Etapu 3 (do domknięcia w środowisku z Dockerem/DB)
- ⬜ Realne wykonanie `DockerSandboxExecutor` + obraz `husarz-sandbox` (Etap 6).
- ⬜ Backend RAG pgvector + embeddingi; sekcja `MemoryConfig`/`StorageConfig`
  (referencje `postgres_dsn_ref`, `redis_dsn_ref`, `embeddings_model_id`, `vector_dim`).
- ⬜ Wypromować przełączniki narzędzi (`network`, `allow_push`) z opaque `config`
  na typowane pola per-kind.
- ⬜ Wpięcie pętli narzędziowej (function-calling) do agenta Towarzysz.

## 🚧 Etap 4 — Bezpieczeństwo (rdzeń decyzyjny ✅; warstwa sieciowa ⬜ Etap 5/6)
- ✅ Niemodyfikowalny audit log z łańcuchem skrótów (`husarz.security.audit`, `verify`).
- ✅ ROE-gate + agent Puszkarz (dry-run domyślnie, `--authorized` dla akcji aktywnych,
  blok celu spoza zakresu/poza oknem/bez aktywnego ROE, odmowa generowania ofensywy).
- ✅ RBAC (role→uprawnienia z wildcardami).
- ✅ Dostawcy sekretów File/SOPS/Vault (backendy wstrzykiwalne, testowalne).
- ✅ Testy: blokada celu spoza ROE, wymóg `--authorized`, audyt kompletny i tamper-evident.
- ⬜ Kryptograficzna weryfikacja podpisu ROE przez dostawcę sekretów (obecnie: obecność podpisu).
- 🚧 Uwierzytelnienie + przypisanie ról: **token Bearer + RBAC wpięte w API (Etap 5)**;
  pełny **OIDC** i **mTLS** — Etap 6.
- ⬜ Runtime egress deny-all na warstwie sieci + izolacja sandboxa (Etap 6).
- ⬜ Aktywować strategie routingu `cost`/`latency` (obecnie placeholdery; aktywne `tags`).

## ✅ Etap 5 — API + Launcher + Web
- ✅ REST API rdzenia (FastAPI): health, config, agents, models, tools, audit,
  usage, orchestrate, config/validate+runtime; router/audyt wstrzykiwalne.
- ✅ Launcher `husarz up --profile dev` (uvicorn; importy FastAPI/uvicorn leniwe).
- ✅ Konsola WWW (jednoplikowa, serwowana przez API): czat + panel konfiguracji +
  audyt + monitor. Pełny Next.js — ścieżka produkcyjna na przyszłość.
- ✅ **Uwierzytelnianie Bearer + RBAC** (token z sekretu `api_token_ref`; rola
  `api_role`); fail-closed launchera dla nasłuchu poza loopbackiem; `TrustedHost`.
- ✅ Escapowanie XSS w konsoli; mapowanie błędów routera na kody HTTP; spójne liczniki
  usage/audyt; atomowy łańcuch audytu (Lock); przebudowa orkiestratora po runtime.
- ✅ Testy: smoke API (TestClient, bez sieci), orkiestracja, walidacja configu, konsola,
  macierz RBAC, odporność i współbieżność (regresje z przeglądu).
- ⬜ WebSocket streaming odpowiedzi; pełny **OIDC** (przepływ tożsamości) + mTLS (Etap 6).

## ⬜ Etap 6 — Deploy i profile
- ⬜ docker-compose profile dev/prod/airgap; manifesty k8s + NetworkPolicy deny-all.
- ⬜ CI pełne (lint, testy, gitleaks, SCA).
- ⬜ Testy: profil airgap działa bez WAN; CI zielone.

## Pozostałe ustalenia
- Modele (GLM-5.2, Bielik v3, Hermes) pobierane lokalnie do `models/` (gitignored)
  na dysku z zapasem miejsca — dopiero od Etapu 1 (router) i realnego uruchomienia.
