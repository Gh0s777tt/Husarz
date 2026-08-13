# ADR-0005: Narzędzia i sandbox (executor/fetcher/backend wstrzykiwalne)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 3

## Kontekst

Narzędzia agentów (edycja plików, shell, git, testy, web, RAG) muszą egzekwować
twarde wymagania bezpieczeństwa: konfinacja do workspace, allowlisty komend/domen,
sandbox bez sieci, deny-all egress. Jednocześnie środowisko deweloperskie bywa bez
Dockera/gVisor (Windows) i bez bazy — a testy nie mogą wykonywać sieci ani wymagać
tych zależności.

## Decyzja

### Rozdzielenie „polityki" od „wykonania"

Cała logika bezpieczeństwa (konfinacja ścieżek, allowlisty, budowa `docker run`,
dwuwarstwowy egress) jest **czysta i testowalna**. Faktyczne wykonanie jest za
protokołami wstrzykiwanymi do narzędzi:

- `SandboxExecutor` — produkcyjny `DockerSandboxExecutor`, w testach mock,
- `Fetcher` (web) — produkcyjny `HttpxFetcher`, w testach mock,
- `RagBackend` — produkcyjny pgvector (później), teraz `InMemoryRagBackend`.

Dzięki temu 100% testów działa bez Dockera, bazy i sieci.

### Sandbox: izolacja w argumentach `docker run`

`build_docker_argv` (czysta funkcja) egzekwuje: `--network none` gdy brak sieci,
limity CPU/RAM, `--cap-drop ALL`, `no-new-privileges`, montaż wyłącznie workspace,
`--runtime runsc` dla gVisor. Obraz i klasa runtime pochodzą z konfiguracji
(`security.sandbox.image/runtime_class`) — bez hardcode.

### Konfinacja plików z obsługą `**`

`resolve_within_workspace` rozwiązuje ścieżkę i wymusza `is_relative_to(workspace)`
oraz deny-globi. Matcher globów obsługuje `**` własną, rekurencyjną implementacją
(zgodną z Pythonem 3.11 — bez `PurePath.full_match` z 3.13).

### Dwuwarstwowy egress dla web

`WebTool` dopuszcza ruch tylko gdy zezwala allowlista domen narzędzia **i**
globalna polityka `security.egress` (ta sama `check_endpoint_allowed`, co router).

## Konsekwencje

- (+) Twarde niezmienniki bezpieczeństwa testowane bez środowiska produkcyjnego.
- (+) Nowe narzędzie/limit = konfiguracja, nie kod (zero hardcode).
- (+) Spójna definicja egress między routerem a narzędziem web.
- (−) Realne wykonanie (Docker+gVisor, pgvector) weryfikowane dopiero w środowisku
  z tymi zależnościami (Etap 6 / integracja) — świadomy kompromis na Windows.

## Alternatywy odrzucone

- **Bezpośrednie `subprocess`/`httpx` w narzędziach**: brak izolacji testów,
  wymóg Dockera/sieci w CI jednostkowym, trudniejsze wymuszenie deny-all egress.
- **`PurePath.full_match` do deny-globów**: dostępne dopiero od Pythona 3.13,
  więc złamałoby wsparcie Pythona 3.11 z matrycy CI.
