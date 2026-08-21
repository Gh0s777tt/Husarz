"""Rekord przebiegu NIE MOŻE nieść treści — niezmiennik prywatności (Etap 16).

Przebieg agenta to najwrażliwszy materiał w całym systemie: zadanie użytkownika, wyniki
narzędzi, fragmenty plików, zapytania do pamięci. Dlatego `RunRecord` jest zaprojektowany
tak, by **fizycznie nie mieć pola na tekst** — bezpieczeństwo z konstrukcji, nie z ustawienia.

Te testy pilnują, że projekt nie zostanie po cichu rozszczelniony:

1. struktura nie ma pól tekstowych na treść (dodanie takiego pola wywali test),
2. treść zadania, odpowiedzi modelu i argumentów narzędzi NIE trafia do rekordu ani do pliku,
3. zbieranie jest opt-in — nowa instalacja nic nie zapisuje.
"""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import pytest

from husarz.agents.react import ACTION_CLOSE, ACTION_OPEN
from husarz.agents.tool_loop import ToolLoop, build_tool_loop
from husarz.agents.towarzysz import Towarzysz
from husarz.config import load_config
from husarz.config.schema import AgentConfig, ToolLoopConfig
from husarz.router.types import ChatRequest, ChatResponse
from husarz.runs import JsonlRunStore, NullRunStore, RunRecord, RunStep
from husarz.security import AuditLog
from husarz.tools import ExecResult, InMemoryRagBackend, SandboxSpec, build_tools
from husarz.tools.dispatch import ToolDispatcher

pytestmark = pytest.mark.security

# Łańcuchy, które MUSZĄ zostać poza rekordem: treść zadania, odpowiedź modelu, argument narzędzia.
_TAJNE_ZADANIE = "TAJNE-ZADANIE-hasło-operatora"
_TAJNA_ODPOWIEDZ = "TAJNA-ODPOWIEDZ-modelu"
_TAJNA_SCIEZKA = "TAJNA-SCIEZKA.md"


class _Router:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self._i = 0

    def complete(self, request: ChatRequest, **kwargs: Any) -> ChatResponse:
        content = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return ChatResponse(model="m", content=content)


class _Executor:
    def run(self, spec: SandboxSpec) -> ExecResult:
        return ExecResult(exit_code=0, stdout="ok")


def _agent(tools: list[str], **cfg: Any) -> Towarzysz:
    base: dict[str, Any] = {
        "name": "kopijnik",
        "display_name": "Kopijnik",
        "agent_class": "towarzysz",
        "prompt_file": "kopijnik.md",
        "tools": tools,
        "tool_loop_enabled": True,
        "max_iterations": 4,
    }
    base.update(cfg)
    return Towarzysz(AgentConfig(**base), "Jesteś Kopijnikiem.")


# --- 1. Struktura nie ma miejsca na treść -----------------------------------


def test_record_has_no_free_text_fields() -> None:
    """Dodanie pola na treść (task, prompt, output, content...) MUSI wywalić ten test."""
    zakazane = {"task", "prompt", "output", "content", "text", "messages", "args", "transcript"}
    assert {f.name for f in fields(RunRecord)} & zakazane == set()
    assert {f.name for f in fields(RunStep)} & zakazane == set()


def test_only_lengths_are_carried() -> None:
    """Objętość mierzymy licznikiem znaków — to metryka, nie treść."""
    nazwy = {f.name for f in fields(RunRecord)} | {f.name for f in fields(RunStep)}
    assert "task_chars" in nazwy and "output_chars" in nazwy


# --- 2. Treść nie wycieka do rekordu ani do pliku ---------------------------


def _run_with_secrets(tmp_path: Path, repo_config_dir: Path, store: Any) -> None:
    config = load_config(repo_config_dir)
    tools = build_tools(
        config, workspace=tmp_path, executor=_Executor(), rag_backend=InMemoryRagBackend()
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}
    loop = ToolLoop(ToolDispatcher(tools, kind_of), AuditLog(), ToolLoopConfig(), runs=store)
    zadanie = {
        "tool": "file_edit",
        "action": "write",
        "args": {"path": _TAJNA_SCIEZKA, "content": "x"},
    }
    akcja = f"{ACTION_OPEN}{json.dumps(zadanie)}{ACTION_CLOSE}"
    loop.run(
        _agent(["file_edit"]),
        _TAJNE_ZADANIE,
        router=_Router([akcja, _TAJNA_ODPOWIEDZ]),
        budget=loop.new_budget(),
    )


def test_no_secret_reaches_the_record(tmp_path: Path, repo_config_dir: Path) -> None:
    class _Collect:
        def __init__(self) -> None:
            self.saved: list[RunRecord] = []

        def save(self, record: RunRecord) -> None:
            self.saved.append(record)

    store = _Collect()
    _run_with_secrets(tmp_path, repo_config_dir, store)
    # `RunRecord` ma slots — brak `__dict__`; `asdict` daje pełny, rekurencyjny zrzut.
    zrzut = json.dumps([asdict(r) for r in store.saved], default=str, ensure_ascii=False)
    for tajne in (_TAJNE_ZADANIE, _TAJNA_ODPOWIEDZ, _TAJNA_SCIEZKA):
        assert tajne not in zrzut


def test_no_secret_reaches_the_file(tmp_path: Path, repo_config_dir: Path) -> None:
    """Pełny obieg na dysk — plik JSONL też nie może nieść treści."""
    plik = tmp_path / "runs.jsonl"
    _run_with_secrets(tmp_path, repo_config_dir, JsonlRunStore(plik))
    zapis = plik.read_text(encoding="utf-8")
    for tajne in (_TAJNE_ZADANIE, _TAJNA_ODPOWIEDZ, _TAJNA_SCIEZKA):
        assert tajne not in zapis
    # ...ale metryki MUSZĄ tam być, inaczej test byłby pusty.
    assert json.loads(zapis.splitlines()[0])["task_chars"] == len(_TAJNE_ZADANIE)


# --- 3. Opt-in --------------------------------------------------------------


def test_disabled_by_default_in_shipped_config(repo_config_dir: Path) -> None:
    """Dostarczona konfiguracja NIE może zbierać pomiarów bez decyzji operatora."""
    assert load_config(repo_config_dir).platform.runs.enabled is False


def test_factory_wires_null_store_when_disabled(tmp_path: Path, repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    loop = build_tool_loop(
        config,
        audit=AuditLog(),
        workspace=tmp_path,
        executor=_Executor(),
        rag_backend=InMemoryRagBackend(),
        data_dir=tmp_path,
    )
    assert isinstance(loop._runs, NullRunStore)  # noqa: SLF001 - test celowo dotyka wnętrza
    assert not (tmp_path / "runs").exists()


def test_factory_wires_file_store_when_enabled(tmp_path: Path, repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    patched = config.model_copy(
        update={
            "platform": config.platform.model_copy(
                update={"runs": config.platform.runs.model_copy(update={"enabled": True})}
            )
        }
    )
    loop = build_tool_loop(
        patched,
        audit=AuditLog(),
        workspace=tmp_path,
        executor=_Executor(),
        rag_backend=InMemoryRagBackend(),
        data_dir=tmp_path,
    )
    assert isinstance(loop._runs, JsonlRunStore)  # noqa: SLF001
    assert loop._runs.path == tmp_path / "runs" / "runs.jsonl"  # noqa: SLF001


# --- 4. Nazwy sterowane przez model nie są kanałem na treść -----------------


def test_model_controlled_tool_name_never_reaches_the_record(
    tmp_path: Path, repo_config_dir: Path
) -> None:
    """ZNALEZISKO Z PRZEGLĄDU: `tool` i `action` pochodzą z bloku akcji, czyli OD MODELU.

    Zapisywanie ich wprost dawało 64-znakowy kanał na dowolną treść w pliku, który
    z założenia treści nie niesie. Do rekordu wpuszczamy je tylko, gdy należą do zbiorów
    zamkniętych: `tool` musi być w allowliście agenta, `action` musi być wywoływalna.
    """
    config = load_config(repo_config_dir)
    tools = build_tools(
        config, workspace=tmp_path, executor=_Executor(), rag_backend=InMemoryRagBackend()
    )
    kind_of = {name: tc.kind for name, tc in config.tools.items()}

    class _Collect:
        def __init__(self) -> None:
            self.saved: list[RunRecord] = []

        def save(self, record: Any) -> None:
            if isinstance(record, RunRecord):
                self.saved.append(record)

    store = _Collect()
    loop = ToolLoop(ToolDispatcher(tools, kind_of), AuditLog(), ToolLoopConfig(), runs=store)
    # Model wstawia sekret w NAZWĘ narzędzia i akcji.
    zadanie = {
        "tool": f"SEKRET-{_TAJNE_ZADANIE}",
        "action": f"SEKRET-{_TAJNA_ODPOWIEDZ}",
        "args": {},
    }
    akcja = f"{ACTION_OPEN}{json.dumps(zadanie)}{ACTION_CLOSE}"
    loop.run(_agent(["rag"]), "x", router=_Router([akcja, "koniec"]), budget=loop.new_budget())

    (record,) = store.saved
    zrzut = json.dumps(asdict(record), ensure_ascii=False)
    assert "SEKRET" not in zrzut, "nazwa narzędzia/akcji od modelu nie może trafić do rekordu"
    assert record.steps[0].tool == "<nieznane>"
    assert record.steps[0].action == "<nieznane>"
