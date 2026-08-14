# Changelog

Wszystkie istotne zmiany w projekcie Husarz. Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Dodane (Etap 15 — pinowanie IP / domknięcie TOCTOU DNS-rebindingu)
- Nowy moduł `husarz.ssrf` — WSPÓLNA warstwa anty-SSRF dla ścieżek wychodzących (`web`,
  wtyczki MCP): klasyfikacja hostów (literał/loopback/nazwa), `resolve_and_pin`, `pin_fields`
  i `PinnedTarget`. Bez zależności od `httpx` (czysty stdlib) → w pełni testowalny offline;
  resolver DNS wstrzykiwalny. Koniec trzech rozjeżdżających się kopii tej logiki.
- **Pinowanie IP**: nazwa rozwiązywana DOKŁADNIE RAZ, KAŻDY zwrócony adres sprawdzany, jeden
  adres przypinany. Transport łączy się z literałem IP, a nagłówek `Host` i **SNI/weryfikacja
  certyfikatu** idą po ORYGINALNEJ nazwie (`extensions={"sni_hostname": ...}` → `server_hostname`
  w `start_tls`), więc `verify=True` pozostaje w mocy — pin ZAWĘŻA powierzchnię ataku, nie
  osłabia TLS. Domyka ryzyko rezydualne z ADR-0015/0016/0019. Docs: ADR-0020.
- Fail-closed w każdym rozgałęzieniu: pusta odpowiedź DNS, JAKIKOLWIEK adres wewnętrzny
  (także w mieszanych A/AAAA), niesparsowalny wynik resolvera lub URL bez hosta → `EgressError`.
  Świadomie NIE wybieramy „czystego" adresu z odpowiedzi zawierającej adres wewnętrzny.
- Kolejność bram taka, by ODMOWA nie kosztowała nawet zapytania DNS: schemat/userinfo →
  literał wewnętrzny → loopback → https → allowlista egress → dopiero DNS + pin.
- Publiczna nazwa NIE może rozwiązać się na loopback (ochrona przed zatrutym DNS kierującym
  token Bearer wtyczki do usługi na maszynie operatora). Loopback intencjonalny konfiguruje
  się literałem `127.0.0.1`/`localhost` — idzie osobną gałęzią, bez DNS.
- Kontrakt „narzędzie NIGDY nie rzuca" utrzymany także dla chorych URL-i: port spoza
  0–65535 lub nieliczbowy (`https://host:99999/x`) daje `EgressError` → `ToolResult(ok=False)`,
  a nie surowy `ValueError` ze stdlib wywracający pętlę agenta (`safe_port`, sprawdzany PRZED
  rozwiązaniem nazwy).
- Testy: +118 (offline; `tests/unit/test_ssrf.py`, `tests/security/test_ssrf_pinning.py`) —
  w tym testy REALNYCH transportów httpx przez `MockTransport` (dotąd produkcyjna ścieżka
  `HttpxFetcher`/`HttpxPluginTransport` nie była pokryta wcale).

### Poprawione (Etap 15 — luki domknięte przy okazji)
- **`web`: loopback przez NAZWĘ** (`http://localhost:8000/admin`) był blokowany wyłącznie jako
  literał IP — nazwa przechodziła przez `is_local_endpoint` jako „endpoint lokalny" i, przy
  `localhost` na allowliście narzędzia, otwierała dostęp do usług na maszynie operatora.
  Teraz odrzucana (`allow_loopback=False` dla tej ścieżki).
- **`HttpxFetcher`: pobieranie całej odpowiedzi przed przycięciem** (`response.text[:max_bytes]`)
  — ryzyko OOM przy złośliwym/przejętym serwerze. Teraz odczyt strumieniowy z twardym sufitem
  bajtowym ORAZ bezwzględnym deadline'em wall-clock (anty-„slow-drip", parytet z transportem MCP).
- Dokumentacja: usunięte nieaktualne adnotacje „brak pinowania IP / TOCTOU odłożone" z
  `BEZPIECZENSTWO.md`, `WTYCZKI.md`, `NARZEDZIA.md` oraz z sekcji „Konsekwencje" ADR-0015/
  0016/0019 (przekreślone + odsyłacz do ADR-0020 — ADR-y pozostają zapisem historycznym).
  `ARCHITEKTURA.md` przestała opisywać zaimplementowane pakiety `husarz.memory`/`husarz.plugins`
  jako zaślepki. `README.md`: przykładowy wynik `validate` doprowadzony do stanu faktycznego
  (brakowało `husarz-local`, `husarz-vision`, `plugin_example`) — rozjazd docs↔kod.

### Poprawione (adwersaryjny przegląd Etapu 15 — 3 soczewki, 18 potwierdzonych findingów)
- **Bypass klasyfikacji adresów (major)**: `is_blocked_address` opierał się wyłącznie na
  właściwościach `ipaddress`, a stdlib NIE uznaje za prywatne m.in. **CGNAT 100.64.0.0/10**
  (endpoint metadanych Alibaba Cloud, typowe pule węzłów k8s/EKS) ani **IPv6 site-local
  `fec0::/10``**. Domena z allowlisty rozwiązana na taki adres przechodziła przez bramkę:
  `web` zwracał metadane modelowi, a konektor MCP wysyłał tam `Authorization: Bearer`.
  Dodano jawną listę sieci deny (CGNAT, site-local, 6to4 `2002::/16`, Teredo `2001::/32`,
  NAT64 `64:ff9b::/96` — tunele osadzające IPv4, plus benchmark/TEST-NET/klasa E, których
  `ipaddress` nie zna na Pythonie 3.11.0–3.11.8 dopuszczonym przez `requires-python`).
- **`*.localhost` jako przepustka (major)**: sufiks był uznawany za loopback po samym
  łańcuchu znaków, więc `mcp.localhost` łączył się WPROST — z pominięciem pinu, wymogu
  `https` i allowlisty egress (RFC 6761 tylko ZALECA mapowanie na loopback; glibc bez
  systemd-resolved wysyła taką nazwę do zwykłego DNS). Teraz `*.localhost` jest
  rozwiązywane, a KAŻDY adres musi być loopbackiem — inaczej odmowa.
- **`trust_env=True` w klientach httpx (major)**: `HTTP(S)_PROXY`/`ALL_PROXY` ze środowiska
  przekierowałyby PRZYPIĘTE połączenie przez cudzy serwer (a `SSLKEYLOGFILE` zrzucił klucze
  sesji) — czyli obeszłyby całą warstwę pinowania i deny-all egress. Ustawione `trust_env=False`
  w `HttpxFetcher` i `HttpxPluginTransport`.
- **Fail-open na wyjątek w resolverze (major)**: `default_resolve` łapał tylko `OSError`,
  a `getaddrinfo` koduje nazwę kodekiem `idna` i dla etykiety >63 znaków rzuca
  `UnicodeEncodeError` (podklasa `ValueError`) — wyjątek uciekał poza bramkę i wywracał
  orkiestrację zamiast dać odmowę. Łapane `(OSError, UnicodeError)`.
- **Obejście allowlisty egress przez `.local`/`.internal` (major)**: `check_endpoint_allowed`
  przepuszcza „endpointy lokalne" po samej NAZWIE (poprawne dla routera modeli — lokalny
  vLLM/Ollama), więc nazwa `cokolwiek.internal` na allowliście narzędzia `web` omijała
  politykę egress, także w profilu `airgap`. `WebTool` egzekwuje teraz allowlistę bez tego skrótu.
- **Wyciek rozpoznania do modelu (minor)**: komunikat odmowy zawierał ROZWIĄZANY adres
  wewnętrzny (`…rozwiązuje się na (10.0.0.7)`) i wracał do modelu jako wynik narzędzia —
  czyli skaner sieci wewnętrznej przez komunikaty błędów. Adres usunięty z komunikatu.
- **Limit `max_bytes` przekraczalny o rzędy wielkości (minor)**: `iter_bytes()` bez
  `chunk_size` oddaje cały zdekompresowany blok naraz (domyślne `Accept-Encoding: gzip`),
  więc sprawdzenie limitu następowało PO doklejeniu. Odczyt chunkami po 64 KiB.
- **Ciche obcięcie przy deadlinie (minor)**: przekroczenie limitu czasu urywało treść
  i raportowało `ok=True`. Teraz `FetchError` (parytet z transportem MCP).
- **Schemat URL niewalidowany (minor)**: `web` przyjmował dowolny schemat (`ftp://`,
  `ws://`) — teraz wyłącznie `http(s)://`, odrzucane przed jakąkolwiek pracą.
- **3xx od serwera MCP jako „brak narzędzi" (minor)**: przy `follow_redirects=False`
  przekierowanie dawało puste ciało i cichą degradację; teraz czytelny `PluginError`.
- **`build_plugin_service` nie przewlekał `resolve` (minor)** — resolver dało się wstrzyknąć
  tylko przez konstruktor `PluginService`, niespójnie z `build_tools`/`build_tool_loop`.
- **Audyt**: `tool.call` zapisuje `pinned_ip` (z JAKIM adresem faktycznie się połączono) —
  przy pinowaniu sama nazwa hosta jest mniej informatywna. Docstring `is_loopback_endpoint`
  wskazywał na przemianowaną funkcję `_validate_mcp_endpoint`.
- Testy: +36 niezmienników dla powyższych (m.in. parametryczne adresy, których `ipaddress`
  nie uznaje za prywatne, oraz kontrola `trust_env`/`verify`/`follow_redirects`).

### Zmienione (Etap 15 — kontrakt wewnętrzny, BREAKING dla kodu first-party)
- Protokoły `Fetcher` (narzędzie `web`) i `PluginTransport` (konektor MCP) oraz `McpClient`
  przyjmują `PinnedTarget` zamiast gołego `str`/URL. Świadome: opcjonalny pin byłby fail-open
  (implementacja mogłaby go po cichu zignorować i rozwiązać nazwę po raz drugi).
- `build_tools` / `build_tool_loop` / `BuildContext` przyjmują `resolve` (resolver DNS) —
  ten sam szew wstrzykiwania, co `executor`/`fetcher`/`rag_backend`.
- `_validate_mcp_endpoint` → `_endpoint_target` (zwraca cel połączenia, nie `None`).
- `.gitignore`: `._*` — macOS na woluminach exFAT/NTFS tworzy sidecary AppleDouble,
  które zaśmiecały repo i wywracały `ruff` („stream did not contain valid UTF-8").

### Poprawione (follow-up 14b — zwalnianie zasobów przy rekonfiguracji)
- Domknięte udokumentowane ograniczenie z Etapu 14b: `SqliteVectorStore` trzymał połączenie do
  pliku przez cykl życia stacku i przy `POST /api/config/runtime` powstawało nowe bez zamknięcia
  starego (wyciek uchwytu). Dodano `close()` do protokołów `VectorStore`/`RagBackend` (oraz
  `RagTool`/`ToolDispatcher`/`ToolLoop`); `app._build_stack` zwraca pętlę, a `config_apply`
  zamyka STARĄ pętlę po atomowej podmianie (best-effort, tłumi błędy, idempotentne; RAM = no-op).
  Testy: +5 (łańcuch close, idempotencja, tłumienie błędów, regresja config_apply). Docs: ADR-0018.

### Dodane (Etap 13b — wywołanie narzędzi wtyczki MCP / tools/call)
- Nowy `kind: plugin` (narzędzie agenta) wiążący JEDEN konektor MCP przez `config.plugin`.
  Dwie akcje: `list` (odkrywanie `tools/list`, tylko `enabled`) i `call` (wywołanie `tools/call`,
  deny-by-default). `McpClient.call_tool` + `RemoteCallResult`; `PluginService.call` z bramami.
- Deny-by-default: `PluginConfig.allow_call` (master-switch, domyślnie false) + `call_allowlist`
  (jawna enumeracja; `allow_call=true` wymaga niepustej listy — fail-closed) + `max_call_bytes`
  (cap zserializowanych params PRZED egress). Odkrywanie ≠ wywołanie.
- Bezpieczeństwo: egress/SSRF re-walidowany PER wywołanie; airgap na starcie wymaga **loopbacku**
  (spójne z runtime); wynik NIEZAUFANY (bloki binarne/`resource` pomijane — bez SSRF-by-proxy,
  ogrodzony jako dane w pętli); token tylko w nagłówku; `arguments` VERBATIM (env: NIE rozwiązywane);
  audyt loguje `{bytes, sha256}` ładunku `arguments` (eksfiltracja wykrywalna). Docs: ADR-0019.
- Utwardzenia z adwersaryjnej krytyki projektu: **M1** audyt `arguments` (nie `<dict>`), **M2**
  airgap-loopback, **S4/S5** cap bajtowy wyniku (config-driven) i cap całych `params`.
- Przewleczenie `plugin_service` do pętli (`create_app → build_tool_loop → build_tools →
  BuildContext`) — ten sam serwis co `/api/plugins`. Config: `example-mcp.yaml` (+pola call),
  `example-plugin.yaml` (NOWY, `kind: plugin`). Testy: +30 (unit/security/integracja, offline).

### Poprawione (adwersaryjny przegląd Etapu 13b)
- **Fail-open kill-switch (major)**: `plugin_service` był budowany raz na starcie i NIE przebudowywany
  przy `POST /api/config/runtime`, więc zmiana polityki konektora (`enabled`/`allow_call`/
  `call_allowlist`/egress) nie obowiązywała aż do restartu. Dodano `plugin_service_factory`
  (jak `router_factory`) — serwis jest przebudowywany z nowego configu; `/api/plugins` i pętla
  czytają świeży serwis ze stanu.
- **Wyciek audytu (minor)**: `arguments` podane jako NIE-mapa wpadały do gałęzi generycznej
  `_arg_summary` (surowe 200 znaków). Teraz `arguments` ZAWSZE logowane jako `{bytes, sha256}`.
- **Cicha odmowa (minor)**: `call_allowlist` z białymi znakami nie dopasowywała się w runtime —
  wpisy są przycinane przy walidacji (`field_validator`).
- Docstringi „6 wbudowanych rodzajów" → „7" (dispatch, test). Testy: +3.

### Dodane (portal dokumentacji + PDF)
- Portal dokumentacji **MkDocs Material** (`mkdocs.yml`, extra `husarz[docs]`) generowany z
  `docs/` — jedno źródło prawdy dla strony HTML i **interaktywnego PDF** (plugin `print-site`,
  strona „Wersja do druku / PDF"). Nowa strona startowa `docs/index.md` (przegląd, szybki start,
  mapa dokumentacji) ze **zrzutem ekranu konsoli WWW** (`docs/assets/screenshots/console.png`).
- Nawigacja: architektura/rdzeń, bezpieczeństwo, operacje, ADR 0001–0018; tryb jasny/ciemny,
  wyszukiwarka, kopiowanie kodu.

### Zmienione (dokumentacja)
- Linki w `docs/*.md` wychodzące poza `docs/` (do plików repo) przepięte na absolutne URL-e
  GitHuba — działają zarówno w portalu HTML, jak i na GitHubie (brak martwych odnośników).
- `README.md`: sekcja budowy portalu i PDF. `.gitignore`: `/site/` (wyjście builda MkDocs).
- `ollama/README.md`: sekcja „Rozwiązywanie problemów" — GPU 50xx/Blackwell (`cudaMalloc failed`
  mimo wolnego VRAM: limit pojedynczej alokacji ~4 GB; obejścia: sterownik / baza ≤3B / CPU)
  oraz pułapka `ollama create -f` (FROM mylone ze ścieżką na Windows).

## [0.14.0] - 2026-08-14

### Zmienione (standard prowadzenia projektu)
- `CLAUDE.md`: doprecyzowany standard prowadzenia projektu (wymóg użytkownika) — aktualizacja
  na bieżąco README/CHANGELOG/ROADMAP/`docs/`/wiki/PDF w tym samym kroku co kod; wiki/PDF ze
  zrzutami ekranu (preferowany interaktywny PDF), zasoby w `docs/assets/`; higiena gita
  (commit/branch/merge/push-readiness, tagi SemVer spójne z CHANGELOG); skan plików publicznych
  pod kątem kluczy/danych prywatnych przed publikacją (w tym zrzutów ekranu); obowiązkowy opis
  kodu „niebezpiecznego" (po co, ryzyko, czy usuwalny/jak zabezpieczony).

### Dodane (Etap 14b — trwałość SQLite + szyfrowanie at-rest + przewleczenie sekretów)
- `SqliteVectorStore` (stdlib `sqlite3`, jeden plik `data_dir/memory/<collection>.db`) za
  NIEZMIENIONYM `Protocol VectorStore` — realna pamięć długoterminowa (przeżywa restart).
  Izolacja `namespace` (WHERE), dedup `(namespace,id)`, ewikcja FIFO po `max_items`,
  zapis atomowy pod `threading.Lock`. Wybór magazynu: `RagBackendConfig.store ∈ {in_memory, sqlite}`.
- Szyfrowanie at-rest CAŁEGO rekordu (tekst + metadane + **wektor**) — `AesGcmCipher`
  (AES-256-GCM, lazy import `cryptography`, opcjonalny extra `husarz[memory]`). Nonce per rekord,
  **`AAD = namespace`** (anti-swap: rekordu nie da się przenieść/odszyfrować jako innej kolekcji).
  `IdentityCipher` tylko dla dev (`encrypt_at_rest=false`). DEK = SHA-256 sekretu z referencji.
- **Przewleczenie `SecretsProvider` do produkcji** (domknięcie blockera z Etapu 14):
  `cli._cmd_up → create_app(secrets) → build_tool_loop(secrets, data_dir) → build_tools →
  BuildContext → _build_rag → build_rag_backend` — `encryption_key_ref` realnie się rozwiązuje.
- Config: `RagBackendConfig` rozszerzony o `store`, `path`, `encrypt_at_rest` (None → dziedziczy
  z `security.encryption.at_rest`), `encryption_key_ref` (walidowany: musi być referencją sekretu).
- Bramki fail-closed przy budowie: sqlite+at-rest bez klucza → błąd PL (nigdy cichy plaintext);
  globalny `at_rest=true` nie może być wyłączony lokalnie dla trwałego magazynu; brak praw
  zapisu do `data_dir` → czytelny `RagBackendError`.
- Testy: +13 (unit crypto/sqlite/bramki, security „brak jawnego tekstu na dysku", integracja
  szyfrowana pamięć przez pętlę), wszystko OFFLINE. Docs: ADR-0018.

### Poprawione (adwersaryjny przegląd Etapu 14b)
- Przegląd (3 wymiary, 13 potwierdzonych z 14) i utwardzenia:
- **Odcisk treści at-rest (major)**: jawna kolumna `id` = `sha256(text)` była membership-oracle
  / brute-force do PII. Teraz autorytatywny `item_id` żyje w zaszyfrowanym blobie, a kolumna to
  `Cipher.blind_id` = `HMAC-SHA256(DEK, namespace‖id)` — nieodwracalna bez klucza, namespace'owana
  (brak korelacji między kolekcjami), zachowuje dedup. Test at-rest idzie ścieżką produkcyjną.
- **Niekontrolowany crash (minor)**: `sqlite3.Error` w `upsert/search/count` opakowany w
  `RagBackendError` → dispatch degraduje do `ToolResult(ok=False)` zamiast HTTP 500.
- **Fail-closed przy budowie (minor)**: `build_cipher` przy at-rest sprawdza dostępność
  `cryptography` (czytelny błąd PL: zainstaluj `husarz[memory]`), nie odroczony `ImportError`.
- **Walidacja krzyżowa (minor)**: pola at-rest (`path`/`encrypt_at_rest`/`encryption_key_ref`)
  wymagają `store: sqlite`, a `store: sqlite` wymaga `backend: embedding` — koniec cichego
  ignorowania intencji szyfrowania.
- **Anty-korupcja wymiaru (nit)**: niezgodny wymiar wektora w trwałym magazynie (zmiana modelu
  embeddera) → `RagBackendError`, nie cicha `0.0`.
- Docs↔kod: zaktualizowane nieaktualne wzmianki „wchodzą w 14b" (`memory/__init__.py`,
  `memory/store.py`, `docs/NARZEDZIA.md`, `config/tools/rag.yaml`). Udokumentowane ograniczenia
  (ADR-0018): `vault:`/`sops:` przyjmowane przez schemat, rozwiązywane tylko przez wspierający
  `SecretsProvider`; cykl życia połączenia sqlite przy runtime-rekonfiguracji (follow-up).
- Testy: +4 (odcisk treści na ścieżce produkcyjnej, szyfrowany dedup + zaślepiony klucz,
  fail-closed wymiaru, walidacja krzyżowa configu).

### Dodane (Etap 14 — pamięć długoterminowa / RAG)
- Pakiet `husarz.memory`: produkcyjny `EmbeddingRagBackend` (wektorowa pamięć semantyczna)
  za NIEZMIENIONYM `Protocol RagBackend` — drop-in za `InMemoryRagBackend`, `RagTool` bez zmian.
  Kompozycja wstrzykiwalnych szwów: `Embedder` (tekst→wektor) + `VectorStore` (cosine).
- Embedder suwerennie: `FakeEmbedder` (deterministyczny, TYLKO dev/test) + `OllamaEmbedder`
  (lokalny `/api/embeddings`, transport wstrzykiwalny, bramka `check_endpoint_allowed` PRZED
  każdym wywołaniem, walidacja wymiaru fail-closed, klucz jako secret-ref).
- `InMemoryVectorStore` (cosine czysty Python, izolacja namespace, cap `max_items`+FIFO,
  dedup po `sha256(text)`). Zero nowych zależności rdzenia.
- Config: `RagBackendConfig`/`EmbedderConfig` (typowane, `extra=forbid`) parsowane z
  `config/tools/rag.yaml`; domyślny backend `memory` (słowny, zero regresji), wektorowy
  `embedding` opt-in. `_build_rag` buduje backend z configu (wstrzyknięty backend ma pierwszeństwo).
- Bezpieczeństwo: izolacja cross-agent przez rozłączne kolekcje (walidacja `_cross_validate`
  odrzuca kolizję namespace); airgap odrzuca nielokalny endpoint embeddera (embeddingi ~ PII);
  wynik `search` re-injektowany zawsze jako ogrodzone DANE (pętla, ADR-0016).
- Testy: +25 (unit + security izolacja/egress + integracja przez pętlę), wszystko OFFLINE.
  Docs: ADR-0017.
- ODŁOŻONE do Etapu 14b (świadomie): trwałość (`SqliteVectorStore`) + szyfrowanie at-rest
  (`AesGcmCipher`) RAZEM z przewleczeniem `SecretsProvider` do produkcji — bez tego
  szyfrowanie byłoby teatrem (klucz nierozwiązywalny). pgvector/mem0/graphiti jako przyszłe
  adaptery za `RagBackend`.

### Poprawione (adwersaryjny przegląd Etapu 14)
- Przegląd (3 wymiary, 3 potwierdzone findingi z 7) i utwardzenia:
- **Łagodna degradacja**: `ToolDispatcher.dispatch` łapie też `MemoryError_`/`EgressError`
  (awaria embeddera RAG, egress) → `ToolResult(ok=False)` zamiast crashu całej orkiestracji.
- **Spójne domyślne**: `embedder.dim` domyślnie 768 (pasuje do `nomic-embed-text`) — koniec
  fail-closed out-of-the-box na udokumentowanej ścieżce ollama (768 ≠ 1024).
- **Docs↔kod**: docstringi `rag.py` (pgvector→EmbeddingRagBackend jako produkcyjny wektorowy).
- Testy: +2 (degradacja dispatchu, spójność domyślnego dim).

### Dodane (Etap 13 — pętla narzędziowa / function-calling)
- **Pętla ReAct** (`husarz.agents.tool_loop`): PIERWSZY egzekutor narzędzi. Model emituje
  ogrodzony blok akcji `[[HUSARZ_ACTION]]{tool,action,args}[[/HUSARZ_ACTION]]`; pętla parsuje,
  autoryzuje, dispatchuje, oddaje wynik NIEZAUFANY z powrotem — aż do odpowiedzi końcowej
  lub limitu. Prompt-based (przenośne na każdy lokalny model), ZERO zmian w routerze.
- **Dispatch** (`husarz.tools.dispatch`): jawna tabela akcji per kind (bez `getattr`),
  walidacja args (zły kształt → `ToolResult(ok=False)`, nigdy wyjątek), `manual()` dla modelu.
- **Autoryzacja per-wywołanie (deny-by-default)**: L0 `roe_required` wykluczony + opt-in
  per agent (`AgentConfig.tool_loop_enabled`, domyślnie false); L1 allowlista agenta;
  L2 walidacja dispatchu; L3 bramki w narzędziach (bez zmian). Audyt każdego wywołania
  (arg_summary sanityzowany — bez surowej treści/sekretów).
- **Limity**: `AgentConfig.max_iterations` (per krok) + `security.tool_loop`
  (`max_result_bytes`, `max_total_calls` — globalny budżet per orkiestracja, `max_plan_steps`).
- **Ogrodzenie**: `husarz.fencing` (wydzielone z załączników) — `fence_untrusted` ogradza
  wyniki narzędzi (i kontekst) jako DANE; marker z wnętrza wyniku neutralizowany (prefiks linii).
- **Wpięcie**: `Orchestrator`/`build_orchestrator`/`create_app` z opcjonalną pętlą;
  `BaseAgent.run` niezmieniony (Pocztowy, plan/synteza, `/api/chat` bez zmian). Walidacja:
  `workspace_dir` rozłączny z `data_dir`/`artifacts_dir`.
- Wspólny helper `husarz.textjson.extract_json_object` (reużyty przez plan i ReAct).
- Testy: +35 (dispatch, protokół, pętla, security offline). Docs: ADR-0016.

### Poprawione (adwersaryjny przegląd Etapu 13)
- Przegląd (3 wymiary, 0 findingów bezpieczeństwa/poprawności, 2 spójności) i utwardzenia:
- **Zero-hardcode**: cap `rag.add` przeniesiony z modułowej stałej do
  `security.tool_loop.max_rag_add_bytes` (konfigurowalny jak pozostałe limity pętli).
- **Kontrakt „nigdy nie rzuca"**: `ToolDispatcher.dispatch` łapie `AttributeError` z
  niespójnego `kind_of` (instancja innego rodzaju niż deklarowany kind) → `ToolResult(ok=False)`.
- **Docs↔kod**: `ORKIESTRATOR.md`/`ROADMAP.md` — pętla oznaczona jako zrealizowana (Etap 13),
  koniec sprzeczności z sekcją ✅. Testy: +2.

### Dodane (Etap 12b — wtyczki / konektory MCP)
- Pakiet `husarz.plugins` (lustro `husarz.git`): konektor do zewnętrznego serwera
  narzędzi **MCP** przez HTTP JSON-RPC nad WSTRZYKIWALNYM transportem. MVP:
  **odkrywanie** narzędzi (`tools/list`); wywołanie wchodzi z pętlą function-calling.
- Nowa sekcja `config/plugins/*.yaml` (`PluginConfig`): `endpoint`, `token_ref`
  (referencja do sekretu, nie wartość), `timeout_seconds`, `max_output_bytes`.
  Nowy konektor = nowy plik, bez zmian w rdzeniu.
- Bezpieczeństwo: anty-SSRF `_validate_mcp_endpoint` (loopback dozwolony; adresy
  wewnętrzne/metadanych — także IPv4-mapped IPv6 — twardo blokowane; host publiczny
  wymaga https + `security.egress.allowlist`), token rozwiązywany leniwie i nigdy
  nielogowany, wynik NIEZAUFANY z limitem `max_output_bytes` (podczas odczytu),
  błędy transportu → generyczne 502, audyt `plugin.discover` przed wyjściem.
- API: `GET /api/plugins`, `GET /api/plugins/{name}/tools` (RBAC `plugin:read`);
  deny-by-default (brak włączonych wtyczek → 404). Konsola: zakładka **Wtyczki**.
- Launcher: `_build_plugins` (HttpxPluginTransport + `_SchemeSecrets`). Przykład:
  `config/plugins/example-mcp.yaml` (`enabled: false`).
- Testy: +37 (unit + security SSRF + API). Docs: `docs/WTYCZKI.md`, ADR-0015.

### Poprawione (adwersaryjny przegląd Etapu 12b)
- Przegląd (3 wymiary, 6 potwierdzonych findingów) i utwardzenia:
- **Anty-DNS-rebinding**: `_validate_mcp_endpoint` rozwiązuje nazwę domenową i sprawdza
  KAŻDY zwrócony adres wobec bloku wewnętrznego (nazwa wskazująca metadane/adres
  wewnętrzny blokowana mimo allowlisty); nierozwiązywalna nazwa → fail-closed. Resolver
  wstrzykiwalny (testy bez DNS). Pełne pinowanie IP nadal odłożone.
- **Anty-„slow-drip" DoS**: `HttpxPluginTransport` egzekwuje bezwzględny deadline
  wall-clock na pętli odczytu (serwer sączący bajty nie blokuje już wątku puli).
- **TLS `verify=True` jawnie** w wywołaniu `httpx.stream` (spójne z docstring/ADR).
- **Walidacja `security.egress.allowlist`**: odrzuca wpisy puste/whitespace i o
  kształcie URL (koniec częściowego wildcardu `host.endswith('.')`).
- **Diagnostyka**: nierozwiązywalny `token_ref` → `PluginSecretError` → HTTP **500**
  (lokalna konfiguracja), odróżnione od zdalnej odmowy serwera (`502`).
- Usunięto martwe pole `PluginConfig.protocol_version` (zwalidowane, nieużywane).
- Testy: +5 (rebinding, fail-closed, walidacja allowlisty, 500 vs 502).

### Zmienione (Etap 12a — rejestr providerów narzędzi)
- `tools/loader.build_tools` porzuca twardy `if/elif kind` na rzecz
  `ToolProviderRegistry` (`tools/registry.py`): rodzaj narzędzia = zarejestrowany
  builder `BuildContext -> Tool`. Nowy rodzaj = builder + jedna linia `register`
  w `default_registry()`, BEZ zmian w rdzeniu dispatchu („zero hardcode").
- `build_tools(..., registry=None)` — wstrzykiwalny rejestr (seam do testów i
  przyszłego konektora MCP); nieznany `kind` daje `ToolError` z zachowanym
  komunikatem (kontrakt niezmieniony, cały pakiet testów narzędzi zielony).
- Rejestr jest WYŁĄCZNIE first-party — świadomie bez `entry_points`/`importlib`
  (obcy kod = RCE/łańcuch dostaw). Testy: +6. Docs: ADR-0014.

### Dodane (Etap 11 — zdjęcia w czacie / modele wizyjne)
- Obrazy w `POST /api/chat` (`images: [{name, data}]`, `data` = base64) dla modeli
  **wizyjnych**. Typ rozpoznawany z **magic-bytes** (png/jpeg/gif/webp) — serwer NIE ufa
  deklarowanemu MIME; obraz przekazywany jako część multimodalna OpenAI-compat
  (`image_url` z data-URI) do backendu (Ollama llava/qwen2-vl).
- Router: `ChatMessage.images: list[ImagePart]` + `_message_payload` buduje treść
  multimodalną (`[{type:text}, {type:image_url}]`) tylko gdy są obrazy (inaczej `str`).
- Konfiguracja: `ModelSpec.vision: bool` (bramka), sekcja `chat.images`
  (`enabled`, `max_images`, `max_bytes_per_image`), model `husarz-vision` w rejestrze,
  `chat.max_request_bytes` podniesione do 12 MB (base64 ~+33%).
- Bezpieczeństwo: `sanitize_images` — limit liczby/rozmiaru, dekodowanie base64 z
  walidacją, sniff magic-bytes, re-enkodowanie znormalizowanej treści; model bez
  `vision` lub dane nie-obraz → `400`. Bez egressu (data-URI, brak pobierania z URL).
- Konsola: przycisk 📎 przyjmuje też obrazy (chip 🖼), wysyłane jako base64; czyszczone
  po wysłaniu / zmianie trybu / resecie.
- Testy: +13 (`tests/unit/test_images.py` — sniff, sanityzacja, payload multimodalny,
  bramka vision w API). Docs: `docs/API.md`, ADR-0013.

### Poprawione (adwersaryjny przegląd Etapu 11)
- Przegląd (3 wymiary, 5 potwierdzonych findingów, 3 odrębne przyczyny) i utwardzenia:
- **Bramka vision na łańcuchu fallbacków**: `ModelRouter.complete` pomija kandydatów
  z `vision:false`, gdy żądanie niesie obrazy — po awarii modelu wizyjnego obraz NIE
  trafia już do modelu tekstowego przez fallback (cichy błąd/halucynacja). Niezmiennik
  z ADR-0013 egzekwowany end-to-end, nie tylko na modelu wybranym w handlerze.
- **Limit ciała odporny na `Transfer-Encoding: chunked`**: `BodySizeLimitMiddleware`
  (czyste ASGI) buforuje ciało z twardym sufitem i zwraca czyste `413` — żądanie bez
  `Content-Length` nie omija już kontroli ani nie grozi OOM (pre-auth DoS) przed walidacją.
- **Obrazy wiązane z ostatnią wiadomością `user`** (nie ślepo z `messages[-1]`) — brak
  obrazu na wiadomości `assistant`/`system`; konwersacja bez `user` + obraz → `400`.
- Testy: +10 (`tests/unit/test_etap11_fixes.py`). Docs: ADR-0013, `docs/BEZPIECZENSTWO.md`.

### Dodane (Etap 10 — pobierany launcher)
- Launcher desktopowy `husarz-app` (`husarz.launcher.app`): bez argumentów startuje
  serwer na loopbacku i **otwiera konsolę w przeglądarce**; deleguje do `husarz up
  --open` (reużywa logiki i bramek bezpieczeństwa). Frozen (PyInstaller) → domyślne
  `config`/`prompts` z `sys._MEIPASS`.
- CLI: flaga `husarz up --open` + `_open_browser_async` (wątek daemon, opener
  wstrzykiwalny, błąd otwarcia nie wywraca serwera; tylko loopback).
- Pakowanie: `packaging/husarz.spec` (PyInstaller onefile: rdzeń + konsola + domyślne
  config/prompts), `packaging/husarz_app.py`, `packaging/README.md`; extra `[package]`.
- CI: `.github/workflows/release.yml` — buduje binarki Windows/Linux/macOS i publikuje
  jako artefakty (dla tagu `v*` dołącza do GitHub Release).
- Testy: +6 (otwieranie przeglądarki, flaga --open, delegacja husarz-app, parser).
  Docs: `docs/LAUNCHER.md`, ADR-0012.

### Poprawione (adwersaryjny przegląd Etapu 10)
- CI Release: unikalne nazwy binarek per-OS (`husarz-app-{windows.exe,linux,macos}`)
  + osobny sekwencyjny job `release` (jedno `gh-release`) — koniec kolizji nazw i
  wyścigu przy dołączaniu do jednego Release.
- Odporność: niezapisywalny audyt (np. read-only CWD binarki) → czytelne **503**
  (handler `AuditError`), nie surowe 500.
- Launcher: poprawny URL dla hosta IPv6 (`[::1]`), `--open` egzekwuje „tylko loopback".
- `.dockerignore`: wykluczone `packaging/` (spójnie z tests/docs/deploy).
- Testy: +6 (non-loopback --open, tłumienie błędu openera, IPv6, delegacja
  profile/prompts + brak config, audyt→503).

### Dodane (Etap 9 — integracje Git: GitHub/GitLab + tworzenie PR)
- Pakiet `husarz.git`: klienci `GitHubProvider`/`GitLabProvider` nad WSTRZYKIWALNYM
  transportem (testy bez sieci): lista repozytoriów + utworzenie PR/MR. Magazyn
  połączeń (InMemory/File JSON, zapis atomowy); `GitService` (rozwiązuje token z
  referencji przy operacji). **Token jako referencja do sekretu**, nigdy plaintext.
- **Bramka egress (deny-all)**: host dostawcy musi być na `security.egress.allowlist`
  — inaczej 403. Ta sama warstwa co router modeli (suwerenność).
- API: `GET/POST/DELETE /api/git/connections`, `GET …/{name}/repos`,
  `POST …/{name}/pull-request`. RBAC: `git:read`/`git:write`/`git:pr` (operator/admin).
- Sekcja konfiguracji `git` (`config/git.yaml`, opcjonalna): `enabled`,
  `connections_path`. ENV: `HUSARZ_GIT__…`. Launcher buduje usługę, gdy włączona.
- Konsola: zakładka **Połączenia** — lista/dodawanie/usuwanie połączeń (token jako
  referencja), podgląd repozytoriów, formularz utworzenia PR/MR.
- Testy: +21 (klienci GitHub/GitLab na mock transport, egress, magazyn, GitService,
  API — 404/409/403/RBAC). Docs: `docs/GIT.md`, ADR-0011.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 9)
- **Blocker SSRF**: dla Git NIE stosujemy „lokalne = zawsze dozwolone" — nowa walidacja
  `api_base` twardo **blokuje hosty wewnętrzne** (loopback/link-local/metadata
  `169.254.169.254`/localhost) i **wymaga jawnej allowlisty** egress.
- **api_base https-only, bez userinfo** (token nie leci plaintextem/na obcy host) —
  walidator schematu (422) + walidacja runtime (403/502).
- **token_ref jako referencja** — walidator odrzuca surowy token (422); sekret nie
  trafia na dysk (spójne z ADR-0011).
- **repo bez wstrzyknięć** — walidator postaci `owner/name` + URL-encode ścieżki w
  kliencie GitHub (koniec `?`,`#` w URL).
- Magazyn połączeń: zapis atomowy pod zamkiem (mutacja+persist), unikatowy temp,
  odporny `_load` (uszkodzony plik → czytelny `GitConnectionError`).
- Klienci: pomijanie elementów nie-`dict` w liście repo (koniec 500). Audyt próby
  PR **przed** budową dostawcy (blok egress też audytowany).
- Testy: +13 (SSRF/https/userinfo, encode repo, non-dict, corrupt-load, 422 dla
  token/api_base/repo, 502 auth, DELETE, RBAC write/PR, GitLab MR, audyt egress).

### Dodane (Etap 8 — załączniki do czatu)
- Moduł `husarz.attachments`: pliki/foldery jako kontekst czatu. Treść NIEZAUFANA —
  twarde limity (liczba, rozmiar per plik/łączny → DoS), czyszczenie nazw (basename),
  odrzucanie danych binarnych, **ogrodzony** blok oznaczony jako dane (anty-prompt-injection,
  neutralizacja prób domknięcia ogrodzenia z wnętrza treści).
- `POST /api/chat` przyjmuje `attachments: [{name, content}]`; kontekst doklejany do
  bieżącej wiadomości; przekroczenie limitu/binaria → `400`. Zużycie tokenów obejmuje kontekst.
- Nowa sekcja konfiguracji `chat` (`config/chat.yaml`, opcjonalna): `chat.attachments`
  (`enabled`, `max_files`, `max_bytes_per_file`, `max_total_bytes`). ENV: `HUSARZ_CHAT__…`.
- Konsola: przyciski 📎 (pliki) i 📁 (folder), odczyt po stronie klienta (FileReader),
  chipy załączników z usuwaniem; foldery przez `webkitdirectory`. Bez CDN.
- Testy: sanityzacja (limity, binaria, konfinacja nazw, ogrodzenie+defang) + integracja
  `/api/chat` (kontekst doklejony, odrzucenia 400). Docs: `docs/API.md`, ADR-0010.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 8)
- **Ogrodzenie odporniejsze**: prefiksowanie KAŻDEJ linii treści niezaufanej (żadna
  linia nie udaje znacznika) zamiast podmiany literałów; nazwy pozbawiane run `=`.
- **Czyszczenie treści**: usuwanie znaków sterujących/formatujących (Cc/Cf poza `\n\t`)
  — ANSI/bidi/zero-width (anty-obfuskacja), analogicznie do czyszczenia nazw.
- **Limit rozmiaru ciała** (`chat.max_request_bytes`, middleware Content-Length → 413)
  chroni pamięć przed OOM przed ingestią; sufity schematu na `content`, liczbę
  załączników (≤1000), `messages.content`, `orchestrate.task`.
- Konsola: załączniki wyłączone/czyszczone w trybie Orkiestracja; czyszczenie chipów
  dopiero po sukcesie (zachowane do ponowienia przy błędzie).
- Docs: sprostowany ADR-0010 (limity egzekwuje serwer) i wiersz `attachments?` w API.md.
- Testy: +7 (przycinanie wielobajtowe, czyszczenie znaków sterujących, neutralizacja
  znacznika w nazwie, limit rozmiaru ciała 413, sufit liczby załączników 422).

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

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 7)
- **Najmniejsze uprawnienia**: nowe konta dostają rolę `user` (czat/orkiestracja),
  nie `operator` (bez `tool:*`, `roe:authorize`, `audit:read`). Dodano rolę `user`.
- **Anty-brute-force**: blokada konta po `login_max_attempts` nieudanych logowaniach
  na `login_lockout_minutes` (HTTP 429); nieudane logowania/blokady audytowane.
- **Walidacja ról**: `api_role`/`default_user_role` muszą należeć do `auth.roles`;
  pola seed-admina wymagane RAZEM (walidator schematu, czytelny błąd po polsku).
- **Hasła**: scrypt `n=2**16` (bliżej OWASP; jawny `maxmem`).
- **Trwały magazyn kont**: zapis atomowy (temp + `os.replace`) pod zamkiem — koniec
  ryzyka uszkodzenia pliku poświadczeń przy współbieżności.
- **Sesje**: sprzątanie wygasłych przy logowaniu + limit sesji na użytkownika.
- **Pusty token maszynowy** normalizowany do braku (koniec dopasowania „Bearer ").
- `check_quota` pod tym samym zamkiem co `record_usage` (limit udokumentowany jako miękki).
- **`husarz useradd`** — admin tworzy konta „dla wybranych" (hasło z ENV), gdy
  rejestracja wyłączona. Wymaga trwałego magazynu (`accounts_path`).
- Testy: +14 regresji (rola user, lockout+429, walidacja ról/seed, atomowy zapis,
  pusty Bearer, sweep sesji, most config→konta, fail-closed z kontami, useradd).

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

[Unreleased]: https://github.com/Gh0s777tt/Husarz/compare/v0.14.0...main
[0.14.0]: https://github.com/Gh0s777tt/Husarz/releases/tag/v0.14.0
