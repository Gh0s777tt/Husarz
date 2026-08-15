# Narzędzia i sandbox (Etap 3)

Warstwa narzędzi agentów: `file_edit`, `shell`, `git`, `run_tests`, `web`, `rag`.
Każde egzekwuje allowlisty i konfinację; polecenia biegną w sandboxie bez sieci
(o ile nie dozwolona). Sterowane konfiguracją (`config/tools/*.yaml` + `security.yaml`).
Kod: `husarz.tools`.

> Środowisko: gVisor (`runsc`) jest tylko dla Linuksa, a wykonanie sandboxa wymaga
> Dockera. Executor sandboxa, fetcher HTTP i backend RAG są **wstrzykiwalne**, więc
> cała logika bezpieczeństwa jest testowalna bez Dockera/DB i bez sieci. Realne
> backendy (Docker+gVisor, httpx, pgvector) są produkcyjne i wymagają środowiska.

## Narzędzia

| Narzędzie   | Klasa            | Egzekwuje |
|-------------|------------------|-----------|
| `file_edit` | `FileEditTool`   | konfinacja do workspace + deny-globi (sekrety, `models/`) |
| `shell`     | `ShellTool`      | allowlista binarek + sandbox (bez sieci, limity) |
| `git`       | `GitTool`        | allowlista podkomend; `push` tylko gdy `allow_push` |
| `run_tests` | `RunTestsTool`   | skonfigurowane polecenie testów w sandboxie |
| `web`       | `WebTool`        | allowlista domen narzędzia **oraz** globalny egress **oraz** anty-SSRF z pinowaniem IP |
| `rag`       | `RagTool`        | pamięć/wyszukiwanie; backend `memory` (słowny, domyślny) lub `embedding` (wektorowy, `husarz.memory`) — patrz niżej i ADR-0017 |

## Ustawienia narzędzi — typowane i walidowane przy starcie

Sekcja `config:` w `config/tools/*.yaml` NIE jest dowolną mapą: każdy `kind` ma własny model
(Pydantic, `extra="forbid"`), sprawdzany przy **starcie**. Nieznany klucz to błąd z nazwą
narzędzia i rodzaju — nie ciche zignorowanie.

| `kind` | Klucze `config` | Skąd reszta |
|---|---|---|
| `file_edit` | `deny_globs`, `max_file_bytes` | katalog: `platform.workspace_dir` |
| `shell` | **żadnych** | sieć/limity/timeout: `security.sandbox` |
| `git` | `allow_push` | katalog: `platform.workspace_dir`; sandbox: `security.sandbox` |
| `run_tests` | `command` | timeout i limity: `security.sandbox` |
| `web` | `max_bytes`, `timeout_seconds` | allowlista domen: pole `allowlist` |
| `rag` | `backend`, `store`, `collection`, `top_k`, `max_items`, `embedder`, … | patrz ADR-0017/0018 |
| `plugin` | `plugin` (wymagany), `max_output_bytes` | polityka wywołań: `config/plugins/` |

> **Dlaczego to jest kwestia bezpieczeństwa, nie kosmetyki.** Do Etapu 3b dostarczana
> konfiguracja zawierała klucze, których nikt nie czytał — m.in. `shell.config.network: false`,
> `cpu_limit` i `memory_limit`. Wyglądały jak wyłączenie sieci i limity zasobów, a nie robiły
> **nic**: izolacją steruje wyłącznie `security.sandbox`. Fałszywe poczucie kontroli jest
> gorsze niż jej brak, bo nie skłania do sprawdzenia. Teraz taki wpis nie pozwoli wystartować.

`ShellSettings` jest celowo puste — jedno źródło prawdy dla izolacji. Nowy rodzaj narzędzia =
builder w rejestrze + jedna pozycja w mapie `kind → model ustawień`.

## Sandbox

`SandboxSpec` opisuje uruchomienie; `build_docker_argv` buduje `docker run`:

- **`--network none`** gdy `network=False` (domyślnie) — twarda izolacja sieci,
- limity **`--cpus` / `--memory` / `--pids-limit`**, timeout (kontener nazwany,
  po timeout `docker rm -f` — bez osieroconych kontenerów),
- **`--cap-drop ALL`**, **`no-new-privileges`**, **`--read-only`** (rootfs) + **`--tmpfs /tmp`**,
  **`--user`** (non-root) — z `security.sandbox.run_as_user/pids_limit/read_only_rootfs`,
- montaż **wyłącznie workspace** do `/workspace` (opcjonalnie `:ro`),
- **`--runtime runsc`** dla gVisor (z `security.sandbox.runtime_class`),
- wymaga `security.sandbox.image` (bez hardcode obrazu; nazwa nie może zaczynać się od `-`).

> Uwaga bezpieczeństwa: `shell` sprawdza allowlistę binarki (`argv[0]`), ale argumenty
> są dowolne — realną granicą jest sam sandbox. Dlatego **sekrety i wagi modeli nie
> powinny znajdować się wewnątrz montowanego workspace** (deny-globi `file_edit` to
> tylko dodatkowa, aplikacyjna warstwa, nieobowiązująca dla `shell`).

`SandboxExecutor` to protokół; produkcyjny `DockerSandboxExecutor` woła Dockera,
w testach wstrzykujemy własny executor (zapisuje `SandboxSpec`, nie uruchamia nic).

## Konfinacja plików

`resolve_within_workspace(workspace, path, deny_globs=...)`:

- odrzuca wyjście poza workspace (`..`, ścieżki absolutne) — `PathNotAllowedError`,
- odrzuca dopasowania do deny-globów (matcher obsługuje `**`, zgodny z Py 3.11),
- domyślne deny-globi z `config/tools/file_edit.yaml`: `**/.env`, `**/*.key`,
  `**/*.pem`, `models/**`.

## Trójwarstwowy egress (web)

`WebTool.fetch` dopuszcza żądanie tylko gdy **wszystkie trzy** warstwy zezwalają — w tej
kolejności (odmowa na dowolnym poziomie nie dotyka sieci):

0. **schemat URL** — wyłącznie `http(s)://` (odrzucane przed jakąkolwiek pracą),
1. **allowlista domen narzędzia** (`config/tools/web.yaml`) — grant per-narzędzie,
2. **globalna polityka `security.egress`** — ta sama `check_endpoint_allowed`, co w routerze,
   ale dla `web` **bez skrótu „endpoint lokalny jest zawsze wolny"**: nazwy `.local`/`.internal`
   muszą przejść allowlistę jak każde inne (inaczej byłyby furtką omijającą egress, także
   w profilu `airgap`),
3. **anty-SSRF z pinowaniem IP** (`husarz.ssrf`) — patrz niżej.

### Pinowanie IP (anty-DNS-rebinding)

Nazwa domenowa jest rozwiązywana **dokładnie raz**, KAŻDY zwrócony adres jest sprawdzany
wobec blokady (prywatne, link-local/metadane chmury, zarezerwowane, multicast, `0.0.0.0`),
po czym pierwszy adres zostaje **przypięty**: fetcher łączy się z literałem IP, a nagłówek
`Host` i weryfikacja certyfikatu TLS (SNI) idą po oryginalnej nazwie. Dzięki temu nie
istnieje drugie rozwiązanie DNS, które atakujący mógłby podmienić między walidacją
a połączeniem (okno TOCTOU — patrz [ADR-0020](adr/0020-pinowanie-ip-anty-ssrf.md)).

Zasady dla narzędzia `web`:

- **loopback jest zabroniony** — także przez nazwę (`http://localhost:8000/…` jest
  odrzucane, nawet gdy `localhost` trafi na allowlistę domen),
- pusta odpowiedź DNS albo **jakikolwiek** adres wewnętrzny (także w mieszanych A/AAAA)
  → odmowa (fail-closed; nie wybieramy „czystego" adresu z zatrutej odpowiedzi),
- przekierowania są wyłączone (`follow_redirects=False`) — omijałyby walidację i pin,
- przypięty adres trafia do `ToolResult.metadata["pinned_ip"]` i do wpisu audytu
  `tool.call` — audyt pokazuje, z JAKIM adresem faktycznie się połączono,
- komunikat odmowy **nie** zawiera rozwiązanego adresu: wynik narzędzia wraca do modelu,
  więc byłby kanałem rozpoznania sieci wewnętrznej,
- blokada obejmuje też sieci, których `ipaddress` nie uznaje za prywatne (CGNAT
  `100.64.0.0/10`, IPv6 site-local `fec0::/10`, tunele 6to4/Teredo/NAT64 osadzające IPv4),
- klient HTTP działa z `trust_env=False` — zmienne `HTTP(S)_PROXY` ze środowiska nie
  przekierują przypiętego połączenia (egress pochodzi z configu, nie ze środowiska).

Resolver DNS jest **wstrzykiwalny** (`build_tools(..., resolve=...)` → `WebTool(resolve=...)`),
więc testy klasyfikują hosty bez odpytywania sieci.

## Ładowarka i użycie

```python
from husarz.config import load_config
from husarz.tools import build_tools

config = load_config("./config")
tools = build_tools(config, workspace="./workspace")  # prod: Docker/httpx/in-memory
# w testach: build_tools(config, workspace=..., executor=Fake(), fetcher=Fake(), rag_backend=...)

result = tools["file_edit"].write("notes/todo.md", "treść")
```

### Rejestr providerów (rozszerzalność, ADR-0014)

Dispatch po `kind` idzie przez `ToolProviderRegistry` (`tools/registry.py`): każdy
rodzaj to zarejestrowany builder `BuildContext -> Tool`. `default_registry()` daje
świeżą instancję z sześcioma wbudowanymi rodzajami. Nowy rodzaj = nowa funkcja-builder
+ jedna linia `register(...)` — bez zmian w `build_tools`:

```python
from husarz.tools import build_tools, default_registry

registry = default_registry()
registry.register("moj_kind", lambda ctx: MojeNarzedzie(ctx.name))
tools = build_tools(config, workspace="./workspace", registry=registry)
```

Rejestr obsługuje wyłącznie providerów **first-party** — świadomie NIE ładuje obcych
modułów (`entry_points`/`importlib`), bo import = wykonanie kodu (RCE/łańcuch dostaw).
Rozszerzalność zewnętrzną realizują **wtyczki/konektory MCP** (data-driven, `husarz.plugins`).

### Pamięć długoterminowa / RAG (Etap 14/14b, ADR-0017/0018)

Narzędzie `rag` ma dwa backendy (`config/tools/rag.yaml`, pole `config.backend`):
`memory` — słowny (domyślny, zero zależności) i `embedding` — wektorowy (`husarz.memory.
EmbeddingRagBackend`: lokalny embedder Ollama, egress-gated + magazyn wektorów,
izolacja `collection`/namespace, cap `max_items`). Nowy backend = gałąź w `build_rag_backend`
+ plik. Izolacja między agentami: rozłączne `collection` (walidacja odrzuca kolizję).

**Trwałość + szyfrowanie at-rest (Etap 14b, ADR-0018).** Dla `backend: embedding` magazyn
wektorów wybiera pole `config.store`: `in_memory` (ulotny, domyślny) lub `sqlite` (trwały,
plik `data_dir/memory/<collection>.db`). Przy `store: sqlite` cały rekord (tekst+metadane+
wektor) jest szyfrowany at-rest (AES-256-GCM) kluczem z `config.encryption_key_ref`
(referencja `env:`/`file:` rozwiązywana przez `SecretsProvider`; wymaga extry `husarz[memory]`).
`config.encrypt_at_rest` (domyślnie dziedziczy `security.encryption.at_rest`) i `config.path`
(nadpisanie ścieżki pliku) dopełniają konfigurację. Fail-closed: sqlite bez rozwiązywalnego
klucza → błąd startu (nigdy cichy plaintext). Patrz [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).

### Narzędzie wtyczki MCP (`kind: plugin`, Etap 13b, ADR-0019)

Rodzaj `plugin` udostępnia agentowi JEDEN konektor MCP z `config/plugins/` (przez
`config.plugin`). Dwie akcje: `list` (odkrywanie `tools/list`, tylko `enabled`) i `call`
(wywołanie `tools/call`, deny-by-default: `allow_call` + `call_allowlist` na konektorze).
Nazwa zdalnego narzędzia to argument `name` (nie akcja — brak `getattr`). Wynik jest
NIEZAUFANY (ogrodzony jako dane). Konfiguracja i warstwy odmowy: [WTYCZKI.md](WTYCZKI.md).

### Wykonanie w pętli narzędziowej (Etap 13, ADR-0016)

Narzędzia wykonuje **pętla function-calling** (`husarz.agents.tool_loop`) dla agentów z
opt-in `tool_loop_enabled`. Model prosi o `(tool, action, args)` (protokół ReAct);
`husarz.tools.dispatch` tłumaczy to na publiczną metodę narzędzia przez jawną tabelę akcji
(bez `getattr`), z walidacją argumentów. Autoryzacja per-wywołanie (allowlista agenta,
audyt, budżet) jest w pętli; sandbox/egress/konfinacja pozostają W narzędziach. Wynik jest
ogradzany jako DANE przed oddaniem modelowi. Patrz [AGENCI.md](AGENCI.md), [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).

## Testy

Konfinacja i allowlisty, budowa argv sandboxa (izolacja sieci, cap-drop, montaż),
shell/git/run_tests na wstrzykniętym executorze, web (allowlista + egress) na
wstrzykniętym fetcherze, rag in-memory, ładowarka z realnej konfiguracji oraz
osobne testy bezpieczeństwa (`tests/security/test_tools_security.py`).

Decyzje projektowe: [ADR-0005](adr/0005-narzedzia-sandbox.md).
