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
| `web`       | `WebTool`        | allowlista domen narzędzia **oraz** globalny egress + blok wewnętrznych IP |
| `rag`       | `RagTool`        | pamięć/wyszukiwanie (obecnie in-memory także w prod; pgvector planowany, Etap 6) |

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

## Dwuwarstwowy egress (web)

`WebTool.fetch` dopuszcza żądanie tylko gdy **obie** warstwy zezwalają:
allowlista domen narzędzia (`config/tools/web.yaml`) **i** globalna polityka
`security.egress` (ta sama `check_endpoint_allowed`, co w routerze).

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
