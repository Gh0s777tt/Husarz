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

## ⬜ Etap 3 — Narzędzia + sandbox
- ⬜ `file_edit`, `shell` (Docker+gVisor, allowlist), `git`, `run_tests`,
  `web` (allowlist domen), `rag` (pgvector).
- ⬜ Rozszerzyć `SandboxConfig` o `image`/`runtime_class`/`pull_policy` (wymagane
  gdy `engine != none`) — inaczej egzekutor narzędzi musiałby hardcodować obraz.
- ⬜ Dodać sekcję `MemoryConfig`/`StorageConfig` (referencje `postgres_dsn_ref`,
  `redis_dsn_ref`, `embeddings_model_id`, `vector_dim`) zamiast hardcode DSN.
- ⬜ Wypromować przełączniki narzędzi istotne dla bezpieczeństwa (`network`,
  `allow_push`) z opaque `config` do typowanych pól per-kind (walidacja literówek).
- ⬜ Testy: izolacja sandbox, blokada spoza allowlisty, brak sieci gdy zabroniona.

## ⬜ Etap 4 — Bezpieczeństwo
- ⬜ Egress deny-all (runtime), mTLS, OIDC+RBAC, audit log niemodyfikowalny (hash-chain), filtry I/O.
- ⬜ ROE-gate + agent Puszkarz (dry-run domyślnie, integracja narzędzi, bez generowania exploitów).
  ROE-gate używa `RoeConfig.is_active_at(now)` (okno czasowe) i weryfikuje podpis
  kryptograficznie przez dostawcę sekretów (nie tylko obecność referencji).
- ⬜ Dostawcy sekretów Vault i SOPS/age.
- ⬜ Runtime egzekwuje dwuwarstwowy egress (allowlisty narzędzi ⊆ globalna allowlista)
  oraz izolację sandboxa; audyt każdego `runtime_override` sekcji `security`.
  (Router ma już bramkę egress na poziomie aplikacji — Etap 1; tu domykamy warstwę sieciową.)
- ⬜ Aktywować strategie routingu `cost`/`latency` (obecnie placeholdery; aktywne `tags`).
- ⬜ Testy: blokada celu spoza ROE, wymóg `--authorized`, audyt kompletny.

## ⬜ Etap 5 — API + Launcher + Web
- ⬜ REST/WS API rdzenia (FastAPI); launcher `husarz up --profile dev`.
- ⬜ UI: czat + panel konfiguracji + audyt + monitor tokenów.
- ⬜ Testy: smoke API, start/stop launchera, edycja configu z panelu.

## ⬜ Etap 6 — Deploy i profile
- ⬜ docker-compose profile dev/prod/airgap; manifesty k8s + NetworkPolicy deny-all.
- ⬜ CI pełne (lint, testy, gitleaks, SCA).
- ⬜ Testy: profil airgap działa bez WAN; CI zielone.

## Pozostałe ustalenia
- Modele (GLM-5.2, Bielik v3, Hermes) pobierane lokalnie do `models/` (gitignored)
  na dysku z zapasem miejsca — dopiero od Etapu 1 (router) i realnego uruchomienia.
