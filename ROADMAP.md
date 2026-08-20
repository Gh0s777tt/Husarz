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
- ✅ Typowane ustawienia per-kind (Etap 3b): `ToolConfig.config` walidowany przy STARCIE
  wobec modelu rodzaju (`extra="forbid"`). Przy okazji wyszło, że dostarczany config miał
  10 martwych kluczy — w tym `shell.network`/`cpu_limit`/`memory_limit`, wyglądające jak
  kontrole bezpieczeństwa, a ignorowane (realne sterowanie: `security.sandbox`). Loader
  pomija sidecary `._*` i daje czytelny błąd dla plików nie-UTF-8. Testy: +22.
- ✅ Wpięcie pętli narzędziowej (function-calling) do agenta Towarzysz — Etap 13 (ADR-0016).

## 🚧 Etap 4 — Bezpieczeństwo (rdzeń decyzyjny ✅; warstwa sieciowa ⬜ Etap 5/6)
- ✅ Niemodyfikowalny audit log z łańcuchem skrótów (`husarz.security.audit`, `verify`).
- ✅ ROE-gate + agent Puszkarz (dry-run domyślnie, `--authorized` dla akcji aktywnych,
  blok celu spoza zakresu/poza oknem/bez aktywnego ROE, odmowa generowania ofensywy).
- ✅ RBAC (role→uprawnienia z wildcardami).
- ✅ Dostawcy sekretów File/SOPS/Vault (backendy wstrzykiwalne, testowalne).
- ✅ Testy: blokada celu spoza ROE, wymóg `--authorized`, audyt kompletny i tamper-evident.
- ✅ Kryptograficzna weryfikacja podpisu ROE (Etap 4b, ADR-0021): kanoniczny payload liczony
  z EFEKTYWNEGO configu (wykrywa też nadpisania runtime), `hmac-sha256` (stdlib) i `ed25519`
  (klucz prywatny poza maszyną), downgrade-guard, fail-closed w każdym rozgałęzieniu,
  wymagane w `prod`/`airgap` przy aktywnym zleceniu. Narzędzie operatora: `husarz roe sign|verify`.
  Testy: +49. Utwardzenia z adwersaryjnego przeglądu (12 findingów): domknięty FAIL-OPEN
  w `out_of_scope` (niewyrównany CIDR cicho znikał = poszerzenie zakresu), normalizacja wpisów
  zakresu, **kotwica profilu** (`platform.profile` nie do nadpisania przez `/api/config/runtime`
  ani domyślnie przez `husarz up`), kolizja `HUSARZ_SECURITY__ROE__*` w loaderze, widoczność
  stanu weryfikacji w `validate`, audyt przy błędzie weryfikatora.
- ✅ Wpięcie `RoeGate` w runtime (Etap 4c): `RoeRuntime` z configu (bramka per zlecenie +
  weryfikator podpisu) wpięty w orkiestrator. Agent `roe_required` jest delegowany WYŁĄCZNIE
  pod zleceniem ze zgodą, ważnym podpisem i w oknie czasowym — inaczej odmowa ze śladem
  w audycie. Bez nowych zdolności ofensywnych: Puszkarz nadal nie ma narzędzi (L0), działa
  w dry-run i dostaje notatkę kontekstową o tym trybie. Testy: +11.
- ⬜ Pozostaje: autoryzacja NA CEL w przepływie (dziś `RoeGate.evaluate` gotowe, ale Puszkarz
  nie wykonuje akcji); klucze prywatne z hasłem; rotacja/wersjonowanie kluczy.
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

## ✅ Etap 6 — Deploy i profile
- ✅ Obrazy: `husarz-api` (Dockerfile, non-root, chudy runtime) + `husarz-sandbox`
  (obraz narzędzi uruchamiany z `--network none`); `.dockerignore` bez wag/sekretów.
- ✅ docker-compose profile dev/prod/airgap (dev samowystarczalny; prod = Caddy TLS
  dla `HUSARZ_PUBLIC_HOST`; airgap bez WAN, tylko loopback).
- ✅ Manifesty k8s (Kustomize): **NetworkPolicy deny-all** + wąskie reguły (bez
  `0.0.0.0/0`), Deployment hardened (non-root, read-only rootfs, drop ALL), Ingress TLS.
- ✅ CI pełne (GitHub + GitLab): lint, format, typy, testy, gitleaks, **pip-audit
  (SCA)**, hadolint + build obrazu; `--allow-insecure` jako jawny opt-out launchera.
- ✅ Testy: niezmienniki wdrożeń (deny-all, non-root, loopback, brak WAN w airgapie,
  placeholdery sekretów) parsowane bez klastra/Dockera.
- ⬜ Realne uruchomienie na klastrze (CNI+NetworkPolicy, gVisor, Vault unseal) —
  środowisko docelowe; pgvector/RAG (pamięć długoterminowa, przyszły etap).

## ✅ Czat lokalny (Ollama) + customowy model
- ✅ `ollama/Husarz.Modelfile` (persona hetmana, czat+kod), `models.chat` + model
  `husarz-local`; `POST /api/chat` (tryb bezpośredni); konsola z Markdown/kodem bez CDN.
- ✅ Weryfikacja end-to-end na REALNYM modelu (`qwen2.5-coder:7b` → `husarz`): czat, pełna
  orkiestracja wieloagentowa, nadpisanie runtime, łańcuch audytu, liczniki `/api/usage`.
  Potwierdzone, że Ollama raportuje `usage` (rozliczanie tokenów działa poza atrapami).
- ✅ **Pętla narzędziowa zweryfikowana na realnym modelu** — pierwszy `tool.call` w dzienniku
  (`tool=rag`, `action=search`, `ok=true`). `tool_loop_enabled: false` pozostaje domyślne
  (deny-by-default, ADR-0016); weryfikacja na configu roboczym.
- ✅ **Rozliczalność wywołań narzędzi widoczna z zewnątrz** — `GET /api/audit` wystawia
  `detail` przez allowlistę deny-by-default (`husarz.api.audit_view`), konsola ma kolumnę
  *Szczegóły*. `args`/`bytes`/`pinned_ip` zostają wyłącznie w dzienniku na dysku.
- ✅ Odświeżanie zrzutów ekranu skryptem `scripts/screenshots.py` (Czat/Agenci/Audyt/Monitor)
  + procedura i ostrzeżenie o przeglądzie przed commitem w `CONTRIBUTING.md`.
- ✅ Domknięty brakujący krok w README: orkiestracja na samej Ollamie wymaga przypisania
  agentów do `husarz-local` (dostarczony `routing.yaml` celuje w modele vLLM).

## ✅ Etap 14 — Pamięć długoterminowa (RAG)
- ✅ `husarz.memory.EmbeddingRagBackend` (wektorowa) za NIEZMIENIONYM `RagBackend`;
  wstrzykiwalne szwy `Embedder` + `VectorStore`. Domyślny backend słowny `memory`
  (zero regresji), wektorowy `embedding` opt-in.
- ✅ `OllamaEmbedder` (lokalny, egress-gated per call, walidacja dim) + `FakeEmbedder`
  (testy). `InMemoryVectorStore` (cosine, namespace, cap+FIFO, dedup). Zero nowych deps rdzenia.
- ✅ Izolacja cross-agent (rozłączne kolekcje — walidacja), airgap na endpoint embeddera,
  wynik `search` ogradzany w pętli. Config typowany (`RagBackendConfig`). Testy: +25 (offline).
  Docs: ADR-0017.
## ✅ Etap 14b — Trwałość + szyfrowanie at-rest pamięci
- ✅ `SqliteVectorStore` (stdlib `sqlite3`, plik `data_dir/memory/<collection>.db`) za
  NIEZMIENIONYM `VectorStore` — pamięć przeżywa restart. Namespace, dedup, FIFO, lock.
- ✅ Szyfrowanie at-rest CAŁEGO rekordu (tekst+metadane+wektor) `AesGcmCipher` (AES-256-GCM,
  nonce/rekord, `AAD=namespace` anti-swap; extra `husarz[memory]`). `IdentityCipher` tylko dev.
- ✅ Przewleczenie `SecretsProvider`+`data_dir` do produkcji (`cli→create_app→build_tool_loop→
  build_rag_backend`) — `encryption_key_ref` realnie rozwiązywany. Bramki fail-closed
  (brak klucza/globalny at_rest / brak praw zapisu). Testy: +13 (offline). Docs: ADR-0018.
- ⬜ Przyszłe adaptery za `RagBackend`/`VectorStore`: pgvector (serwer), mem0/graphiti;
  KMS/rotacja klucza (obecnie DEK=SHA-256 sekretu, KDF-lite).

## ✅ Etap 15 — Pinowanie IP (anty-DNS-rebinding)
- ✅ `husarz.ssrf` — WSPÓLNA warstwa anty-SSRF dla `web` i wtyczek MCP (koniec trzech kopii
  logiki). Czysty stdlib (bez `httpx`), resolver wstrzykiwalny → testy w pełni offline.
- ✅ Pin: nazwa rozwiązywana RAZ, KAŻDY adres sprawdzany, jeden przypinany; transport łączy
  się z literałem IP, a `Host` + **SNI/weryfikacja certu** idą po nazwie (`verify=True` w mocy).
  Domyka okno TOCTOU zapisane jako ryzyko rezydualne w ADR-0015/0016/0019.
- ✅ Fail-closed: pusty DNS / jakikolwiek adres wewnętrzny (także mieszane A/AAAA) / śmieć
  z resolvera / URL bez hosta → `EgressError`. Odmowa egress nie powoduje nawet zapytania DNS.
- ✅ Domknięte przy okazji: loopback przez NAZWĘ w `web` (dotąd blokowany tylko jako literał);
  `HttpxFetcher` czyta strumieniowo z sufitem bajtowym + deadline (było: pobierz-potem-utnij).
- ✅ Kontrakt „narzędzie nie rzuca" także dla chorych URL-i (port spoza 0–65535 → `ok=False`).
- ✅ Utwardzenia z adwersaryjnego przeglądu (3 soczewki, 18 potwierdzonych findingów): jawna
  lista sieci deny (CGNAT 100.64/10, IPv6 site-local, 6to4/Teredo/NAT64 — stdlib ich NIE zna),
  `*.localhost` weryfikowany przez DNS (nie po sufiksie), `trust_env=False` (proxy z ENV omijało
  pin!), `UnicodeError` z kodeka idna, `.local`/`.internal` bez obejścia allowlisty egress,
  brak wycieku rozwiązanego adresu do modelu, chunkowany odczyt, walidacja schematu, 3xx MCP.
- ✅ Testy: +118 (offline), w tym pierwsze testy REALNYCH transportów httpx (`MockTransport`).
  Docs: ADR-0020, BEZPIECZENSTWO/NARZEDZIA/WTYCZKI/ARCHITEKTURA.
- ⬜ Obsługa przekierowań (dziś `follow_redirects=False` — przekierowanie omijałoby
  walidację i pin; ewentualne wsparcie musiałoby przypinać KAŻDY skok osobno).

## ✅ Etap 15b — `husarz.git` na wspólnej warstwie anty-SSRF
- ✅ Trzecia (ostatnia) ścieżka wychodząca przeszła na `husarz.ssrf`. Poprzednia walidacja
  sprawdzała tylko LITERAŁY i NIE rozwiązywała nazw — ścieżka niosąca token PAT z prawem
  zapisu do repozytoriów nie miała żadnej ochrony przed rebindingiem ani pinu.
- ✅ Nowa oś polityki `allow_lan` (obok `allow_loopback`): Git blokuje loopback, ale dopuszcza
  prywatną sieć operatora (samodzielnie hostowany GitLab = scenariusz suwerenności). Luz WĄSKI
  i jawny (RFC 1918 + ULA), NIE przez `ipaddress.is_private` (obejmuje loopback/link-local).
- ✅ `HttpxGitTransport` z parytetem: `trust_env=False`, cap rozmiaru + deadline, generyczny
  błąd (dotąd echował URL i wnętrzności httpx do audytu/API).
- ✅ Bezpiecznik offline w `tests/conftest.py` (blokada `socket.getaddrinfo`) — wykrył
  5 testów po cichu wychodzących na sieć.
- ✅ Utwardzenia z adwersaryjnego przeglądu (3 soczewki, 8 potwierdzonych findingów): domknięty
  **fail-open kill-switch** (`git_service_factory` — polityka egress obowiązywała dopiero po
  restarcie), 3xx dostawcy jako błąd, pusty separator `?`/`#` w `api_base`, chunkowany odczyt
  także w transporcie MCP, usunięte martwe pole, sprostowany ADR-0011.
- ✅ Testy: +19. Docs: ADR-0020, ADR-0011 (nota), GIT.md, BEZPIECZENSTWO.md, CHANGELOG (nota
  migracyjna: Git blokuje teraz CGNAT/Tailscale i inne zakresy, które wcześniej przechodziły).

## ✅ Etap 15c — Embedder pamięci i router modeli na wspólnej warstwie
- ✅ Dwie ostatnie ścieżki wychodzące przeszły na `husarz.ssrf`. Po tym etapie **wszystkie
  pięć** dróg wyjścia na sieć (`web`, wtyczki MCP, Git, embedder RAG, router modeli) korzysta
  z JEDNEJ implementacji anty-SSRF — różnią się tylko dwiema flagami polityki.
- ✅ Polaryzacja `allow_loopback=True, allow_lan=True` (własne modele operatora), ale metadane
  chmury, CGNAT, zakresy zarezerwowane i tunele osadzające IPv4 nadal zablokowane — tam
  wylądowałby klucz API modelu albo wektor embeddingu (odwracalny do PII).
- ✅ Poprawka wycieku: `HttpxTransport` routera echował pełny URL i wnętrzności httpx do
  `ModelBackendError` → odpowiedzi API i audytu. Komunikat generyczny (parytet z resztą).
- ✅ Transporty embeddera i routera: `trust_env=False`, chunkowany odczyt z sufitem, `verify=True`.
- ✅ Testy: +4 niezmienniki bezpieczeństwa; łącznie 797. Docs: ADR-0020, BEZPIECZENSTWO.md,
  ARCHITEKTURA.md.

## ✅ Etap 13 — Pętla narzędziowa (function-calling)
- ✅ Pętla ReAct (`agents/tool_loop.py`) — pierwszy egzekutor narzędzi; prompt-based
  (przenośne na lokalne modele), zero zmian w routerze. Dispatch (`tools/dispatch.py`)
  z jawną tabelą akcji (bez `getattr`).
- ✅ Autoryzacja per-wywołanie (deny-by-default): opt-in per agent `tool_loop_enabled`,
  L0 ROE-exclude, L1 allowlista, L2 dispatch, L3 bramki narzędzi; audyt każdego wywołania.
- ✅ Limity: `max_iterations` + `security.tool_loop` (`max_result_bytes`, `max_total_calls`,
  `max_plan_steps`); ogrodzenie wyniku (`husarz/fencing.py`). Wpięcie w orkiestrator/API.
- ✅ Testy: +35 (offline). Docs: ADR-0016.
- ✅ Pinowanie IP dla `web`/`plugin` (domknięcie TOCTOU rebindingu) — Etap 15 (ADR-0020).
- ✅ Korelacja principal↔wywołanie w audycie (Etap 13c): wpisy niosą i „kto wykonał"
  (agent), i „na czyje żądanie" (`user:<id>` / `token:<rola>`), objęte łańcuchem skrótów,
  ze zgodnością wsteczną starych dzienników. Testy: +8.
- ⬜ Natywny adapter `tool_calls` (function-calling API).

## ✅ Etap 13b — Wywołanie narzędzi wtyczki MCP (`tools/call`)
- ✅ `kind: plugin` (narzędzie agenta) wiąże JEDEN konektor przez `config.plugin`; akcje
  `list` (odkrywanie) i `call` (wywołanie). `McpClient.call_tool` + `RemoteCallResult`;
  `PluginService.call` z bramami. Nazwa zdalna jako argument (bez `getattr`).
- ✅ Deny-by-default: `allow_call` (master-switch) + `call_allowlist` (fail-closed) +
  `max_call_bytes`; odkrywanie ≠ wywołanie; bramy PRZED egress. Airgap na starcie: loopback.
- ✅ Wynik NIEZAUFANY (bez SSRF-by-proxy, ogrodzony); token tylko w nagłówku; `arguments`
  VERBATIM; audyt `{bytes, sha256}`. Utwardzenia z krytyki (M1/M2/S4/S5). Przewleczenie
  `plugin_service` do pętli. Testy: +30 (offline). Docs: ADR-0019, WTYCZKI.md.
- ✅ TOCTOU DNS-rebinding (ryzyko rezydualne z ADR-0019) — domknięte w Etapie 15 (ADR-0020).

## ✅ Etap 12 — System wtyczek (rejestr narzędzi + konektor MCP)
- ✅ 12a: `ToolProviderRegistry` (open/closed) zastępuje `if/elif` w `build_tools`;
  nowy rodzaj = builder + `register`, bez zmian w rdzeniu. Wyłącznie first-party
  (bez `entry_points`). Testy: +6. Docs: ADR-0014.
- ✅ 12b: pakiet `husarz.plugins` — konektor MCP (HTTP JSON-RPC, transport wstrzykiwalny),
  **odkrywanie** narzędzi (`tools/list`); anty-SSRF (loopback OK, metadane/IPv4-mapped
  blok), token jako referencja, audyt, RBAC `plugin:read`, `config/plugins/*.yaml`.
  API `/api/plugins*` + zakładka Wtyczki. Testy: +37. Docs: WTYCZKI.md, ADR-0015.
- ✅ 13b: wywoływanie narzędzi (`tools/call`) w pętli — patrz Etap 13b (ADR-0019).
- ⬜ Transport stdio; pełny handshake MCP (`initialize`, streaming/SSE, `resources`).

## ✅ Etap 11 — Zdjęcia w czacie (modele wizyjne)
- ✅ `POST /api/chat` z `images` (base64) dla modeli `vision: true`; obraz jako część
  multimodalna OpenAI-compat (`image_url` z data-URI) — bez pobierania z URL (brak SSRF).
- ✅ Sniff typu z magic-bytes (png/jpeg/gif/webp; nie ufa MIME), `sanitize_images`
  (limity liczby/rozmiaru, re-enkodowanie); bramka `vision` na modelu, inaczej `400`.
- ✅ Config: `ModelSpec.vision`, `chat.images`, model `husarz-vision`, `max_request_bytes` 12 MB.
- ✅ Konsola: 📎 przyjmuje obrazy (chip 🖼). Testy: +23. Docs: API.md, ADR-0013.
- ✅ Hardening po przeglądzie (5 findingów): bramka vision na fallbackach routera,
  limit ciała odporny na chunked (bez OOM), obrazy tylko na wiadomości `user`.
- ⬜ OCR/kadrowanie po stronie serwera; galeria miniatur w konsoli.

## ✅ Etap 10 — Pobierany launcher
- ✅ `husarz-app` (serwer + auto-otwarcie konsoli; deleguje do `husarz up --open`);
  frozen → config/prompts z bundla PyInstaller.
- ✅ Pakowanie PyInstaller (`packaging/husarz.spec`), extra `[package]`.
- ✅ CI `release.yml` — binarki Windows/Linux/macOS jako artefakty + GitHub Release (tag v*).
- ✅ Testy: opener, --open, delegacja. Docs: LAUNCHER.md, ADR-0012.
- ✅ Binarka zweryfikowana na ŻYWO (macOS arm64, 22 MB): start poza repo bez konfiguracji,
  konsola, czat z lokalnym modelem, nadpisanie runtime — wszystko na domyślnych ustawieniach.
- ✅ Kontrola kolizji portu przy starcie (`husarz.launcher.diagnostics`) — launcher i vLLM
  z dostarczonego configu dzielą port domyślny 8000. Ostrzega, nie blokuje.
- ✅ `flush=True` na komunikacie startowym — poza terminalem `stdout` jest buforowany
  blokowo i cały komunikat (z ostrzeżeniami) znikał z logów.
- ⬜ Podpis kodu/notaryzacja (operator); desktop Tauri (auto-update, tray).
- ⬜ **Bootstrap modelu przy pierwszym uruchomieniu** — dziś po pobraniu binarki czat bez
  Ollamy zwraca `502 Backend modelu zawiódł` bez żadnej podpowiedzi. Kierunek: `husarz doctor`
  (jedno źródło dla CLI i konsoli) → pobranie silnika z archiwum standalone → `pull` bazy →
  `create` modelu, z ekranem zgody podającym liczbę GB PRZED pobraniem, wąską allowlistą
  `bootstrap.sources` (osobną od `security.egress.allowlist`, żeby nie otwierać domeny
  narzędziu `web`) i degradacją do trybu offline w profilu `airgap`.

## ✅ Etap 9 — Integracje Git (GitHub/GitLab) + tworzenie PR
- ✅ `husarz.git`: klienci GitHub/GitLab nad wstrzykiwalnym transportem (lista repo,
  utworzenie PR/MR); magazyn połączeń (File/mem); token jako referencja do sekretu.
- ✅ Bramka egress (deny-all) na hoście dostawcy; RBAC `git:read`/`git:write`/`git:pr`.
- ✅ API `/api/git/*`; sekcja configu `git` (`config/git.yaml`); zakładka Połączenia.
- ✅ Testy: klienci (mock transport), egress, magazyn, GitService, API. Docs: GIT.md, ADR-0011.
- ⬜ Pełny OAuth (rejestracja aplikacji + callback; tokeny szyfrowane at-rest dla
  trybu hostowanego); commit plików+push przez API (agent Kopijnik); przegląd PR.

## ✅ Etap 8 — Załączniki do czatu
- ✅ `husarz.attachments`: pliki/foldery jako kontekst; limity (liczba/rozmiar),
  czyszczenie nazw, odrzucanie binariów, ogrodzony blok anty-prompt-injection.
- ✅ `POST /api/chat` z `attachments`; sekcja configu `chat` (`config/chat.yaml`).
- ✅ Konsola: 📎 pliki / 📁 folder (FileReader, webkitdirectory), chipy załączników.
- ✅ Testy: sanityzacja + integracja z /api/chat. Docs: API.md, ADR-0010.
- ✅ Zdjęcia (model wizyjny llava/qwen2-vl) — zrealizowane w Etapie 11.
- ⬜ Chunkowanie/RAG dużych folderów (pamięć długoterminowa — kandydat MemPalace/pgvector).

## ✅ Etap 7 — Konta, sesje i limity tokenów
- ✅ `husarz.accounts`: hasła `scrypt` (bez zależności), magazyn wstrzykiwalny,
  `AccountService` (rejestracja gated, sesje+TTL, logout, limit/zużycie tokenów).
- ✅ API: `/api/auth/register|login|logout|me`; Bearer = sesja LUB token maszynowy;
  RBAC per użytkownik; limit tokenów → HTTP 402; zużycie z pola `usage` (czat).
- ✅ Konsola: modal logowania/rejestracji, pasek użytkownika (nazwa, rola, model,
  zużyte/limit tokenów), wylogowanie. Seed-admin z sekretu (fail-closed).
- ✅ Testy: hasła, rejestracja/logowanie/sesje/limity, API kont (sesja jako Bearer,
  402, RBAC), seed-admin. Docs: KONTA.md, ADR-0009.
- ✅ Rozliczanie tokenów orkiestracji (Etap 7b): `/api/orchestrate` sprawdzał limit, ale go
  NIE naliczał — konto z kwotą mogło orkiestrować bez końca na najdroższym endpoincie.
  `UsageMeter` sumuje zużycie ze wszystkich faz (plan, delegacje, iteracje pętli, refleksja,
  synteza) i endpoint nalicza je na koncie. Testy: +8.
- ⬜ Sesje współdzielone
  (Redis) do skalowania; płatności/subskrypcje; integracje Git/VS Code; załączniki.

## ⬜ Etap 16 — Warstwa pomiaru jakości (ewaluacja)

Trzy niezależne analizy zewnętrznych narzędzi (ART, OmniRoute — patrz
[ADR-0022](docs/adr/0022-zewnetrzne-narzedzia-agentowe.md)) wskazały ten sam brak: Husarz
nie ma **żadnego** sposobu, by zmierzyć, czy zmiana promptu, routingu albo modelu poprawiła
działanie agenta. Bez tej liczby każda dalsza optymalizacja jest zgadywaniem.

- ⬜ Materializacja przebiegu agenta (`RunRecord`): `run_id`, tożsamość agenta, numer
  iteracji, wywołania narzędzi z `ok`/`exit_code`, powód terminacji, zużycie tokenów.
  Granulacja per wiadomość — inaczej późniejsze użycia wymagają ponownego instrumentowania.
- ⬜ `config/evals/<zestaw>.yaml` (przez `_MULTI_DIRS` w loaderze) + podkomenda `husarz eval`.
- ⬜ Cztery weryfikatory **deterministyczne**: `exit_code` z `run_tests`; udział odpowiedzi
  niesparsowalnych; zgodność routingu (`resolve_agent_model`/`select_candidates` — czysta
  funkcja); blokady bezpieczeństwa (`tool.deny`, odmowy ROE, egress). **Trzy z czterech
  działają na atrapie — bez modelu, bez GPU, bez sieci — więc wchodzą do CI od razu.**
- ⬜ Budżet okna kontekstu — dziś clampujemy `max_tokens`, ale nie sprawdzamy, czy prompt
  mieści się w oknie modelu. Przy 7B i pętli narzędziowej to realny problem (zaobserwowany:
  agent wyczerpał limit iteracji).
- ⬜ (hipoteza, poza zakresem etapu) Sędzia LLM z relatywnym ocenianiem grupowym. Poprzedzony
  pomiarem, czy grupa przebiegów mieści się w oknie modelu, który startuje na tym sprzęcie.
  Twardy sygnał deterministyczny NIGDY nie jest nadpisywany oceną sędziego.

## Pozostałe ustalenia
- Modele (GLM-5.2, Bielik v3, Hermes) pobierane lokalnie do `models/` (gitignored)
  na dysku z zapasem miejsca — dopiero od Etapu 1 (router) i realnego uruchomienia.
