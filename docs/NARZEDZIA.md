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
| `web`       | `WebTool`        | allowlista domen narzędzia **oraz** globalny egress |
| `rag`       | `RagTool`        | pamięć/wyszukiwanie (in-memory w testach, pgvector w prod) |

## Sandbox

`SandboxSpec` opisuje uruchomienie; `build_docker_argv` buduje `docker run`:

- **`--network none`** gdy `network=False` (domyślnie) — twarda izolacja sieci,
- limity **`--cpus` / `--memory`**, timeout,
- **`--cap-drop ALL`** i **`--security-opt no-new-privileges`** (hardening),
- montaż **wyłącznie workspace** do `/workspace`,
- **`--runtime runsc`** dla gVisor (z `security.sandbox.runtime_class`),
- wymaga `security.sandbox.image` (bez hardcode obrazu w kodzie).

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

## Testy

Konfinacja i allowlisty, budowa argv sandboxa (izolacja sieci, cap-drop, montaż),
shell/git/run_tests na wstrzykniętym executorze, web (allowlista + egress) na
wstrzykniętym fetcherze, rag in-memory, ładowarka z realnej konfiguracji oraz
osobne testy bezpieczeństwa (`tests/security/test_tools_security.py`).

Decyzje projektowe: [ADR-0005](adr/0005-narzedzia-sandbox.md).
