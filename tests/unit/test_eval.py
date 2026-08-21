"""Warstwa ewaluacji — zestawy, weryfikatory, CLI (Etap 16).

Sens tej warstwy jest jeden: dać LICZBĘ, której Husarz dotąd nie miał. Testy pilnują, że
liczba jest prawdziwa — czyli że weryfikator wykrywa rozjazd, a nie zawsze mówi „OK".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from husarz.config import load_config
from husarz.config.evals import EvalSet, RoutingCase, ToolPolicyCase
from husarz.eval import run_case, run_set
from husarz.launcher.cli import main as cli_main

pytestmark = pytest.mark.unit


def _agents(config, prompts: Path):  # type: ignore[no-untyped-def]
    from husarz.agents.loader import build_agents

    return build_agents(config, prompts_dir=prompts)


# --- Weryfikator routingu ---------------------------------------------------


def test_routing_case_passes_when_expectation_matches(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    agent = next(iter(config.agents))
    from husarz.router.selection import select_candidates

    expected = select_candidates(config, agent=agent)[0]
    case = RoutingCase(name="t", kind="routing", agent=agent, expect_model=expected)
    assert run_case(config, case, agents={}, workspace=Path(".")).passed is True


def test_routing_case_fails_and_names_both_values(repo_config_dir: Path) -> None:
    """Komunikat MUSI podać oczekiwane i faktyczne — inaczej raport nie pomaga w naprawie."""
    config = load_config(repo_config_dir)
    agent = next(iter(config.agents))
    case = RoutingCase(name="t", kind="routing", agent=agent, expect_model="model-widmo")
    result = run_case(config, case, agents={}, workspace=Path("."))
    assert result.passed is False
    assert "model-widmo" in result.detail


def test_unknown_agent_is_a_failure_not_a_crash(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    case = RoutingCase(name="t", kind="routing", agent="nie-ma", expect_model="x")
    result = run_case(config, case, agents={}, workspace=Path("."))
    assert result.passed is False and "nie istnieje" in result.detail


# --- Weryfikator bramki narzędziowej ---------------------------------------


def _with_tool_loop(config, agent_name: str, tools: list[str]):  # type: ignore[no-untyped-def]
    cfg = config.agents[agent_name].model_copy(
        update={"tool_loop_enabled": True, "tools": tools, "roe_required": False}
    )
    return config.model_copy(update={"agents": {**config.agents, agent_name: cfg}})


def test_tool_outside_allowlist_is_reported_as_denied(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    config = _with_tool_loop(load_config(repo_config_dir), "bielik", ["rag"])
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="shell", action="run", expect="denied"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")
    assert run_case(config, case, agents=agents, workspace=tmp_path).passed is True


def test_tool_inside_allowlist_is_reported_as_allowed(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    config = _with_tool_loop(load_config(repo_config_dir), "bielik", ["rag"])
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="rag", action="search", expect="allowed"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")
    assert run_case(config, case, agents=agents, workspace=tmp_path).passed is True


def test_wrong_expectation_fails(repo_config_dir: Path, tmp_path: Path) -> None:
    """Test nośności: gdy oczekujemy 'allowed' dla narzędzia spoza allowlisty — ma paść."""
    config = _with_tool_loop(load_config(repo_config_dir), "bielik", ["rag"])
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="shell", action="run", expect="allowed"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")
    result = run_case(config, case, agents=agents, workspace=tmp_path)
    assert result.passed is False and "zablokowane" in result.detail


def test_agent_without_opt_in_is_reported_honestly(repo_config_dir: Path, tmp_path: Path) -> None:
    """Bez `tool_loop_enabled` pętla nie wystartuje — raport ma to POWIEDZIEĆ, a nie udawać
    zablokowanie. Fałszywe „denied" byłoby gorsze niż brak pomiaru."""
    config = load_config(repo_config_dir)  # dostarczony config: pętla wyłączona
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="shell", action="run", expect="denied"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")
    result = run_case(config, case, agents=agents, workspace=tmp_path)
    assert result.passed is False and "wyłączoną pętlę" in result.detail


# --- Zestaw i raport --------------------------------------------------------


def test_set_result_counts_and_ok_flag(repo_config_dir: Path, tmp_path: Path) -> None:
    config = load_config(repo_config_dir)
    agent = next(iter(config.agents))
    from husarz.router.selection import select_candidates

    good = select_candidates(config, agent=agent)[0]
    eval_set = EvalSet(
        name="mieszany",
        cases=[
            RoutingCase(name="dobry", kind="routing", agent=agent, expect_model=good),
            RoutingCase(name="zly", kind="routing", agent=agent, expect_model="widmo"),
        ],
    )
    result = run_set(
        config, eval_set, prompts_dir=repo_config_dir.parent / "prompts", workspace=tmp_path
    )
    assert (result.passed, result.failed, result.ok) == (1, 1, False)


def test_empty_set_is_considered_passing(repo_config_dir: Path, tmp_path: Path) -> None:
    result = run_set(
        load_config(repo_config_dir),
        EvalSet(name="pusty"),
        prompts_dir=repo_config_dir.parent / "prompts",
        workspace=tmp_path,
    )
    assert result.ok is True and result.passed == 0


# --- Konfiguracja i CLI -----------------------------------------------------


def test_shipped_eval_set_loads_and_passes(repo_config_dir: Path) -> None:
    """Dostarczony zestaw MUSI przechodzić — inaczej publikujemy czerwoną bramkę."""
    assert "podstawowy" in load_config(repo_config_dir).evals
    assert cli_main(["eval", "--config", str(repo_config_dir), "--prompts", "./prompts"]) == 0


def test_cli_returns_one_on_unknown_set(repo_config_dir: Path) -> None:
    assert cli_main(["eval", "--config", str(repo_config_dir), "--set", "nie-ma"]) == 1


def test_unknown_case_kind_is_rejected_at_load() -> None:
    """Literówka w rodzaju przypadku ma być BŁĘDEM konfiguracji, nie cichym pominięciem."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        EvalSet(name="x", cases=[{"name": "a", "kind": "nieznany"}])  # type: ignore[list-item]


# --- Bramka faktycznie broni gałęzi -----------------------------------------


@pytest.mark.parametrize("pipeline", [".github/workflows/ci.yaml", ".gitlab-ci.yml"])
def test_eval_gate_is_wired_into_ci(pipeline: str) -> None:
    """Bramka, której CI nie uruchamia, jest rytuałem — nie bramką.

    Test pilnuje OBU pipeline'ów, bo projekt utrzymuje zdalne w synchronizacji i cicha
    utrata bramki na jednym z nich byłaby niewidoczna do czasu regresji.
    """
    from pathlib import Path as _Path

    tresc = _Path(pipeline).read_text(encoding="utf-8")
    assert "husarz.launcher.cli eval" in tresc, f"{pipeline}: brak kroku `husarz eval`"


# --- Znaleziska z adwersaryjnego przeglądu ----------------------------------


def test_eval_never_touches_docker_or_network(repo_config_dir: Path, tmp_path: Path) -> None:
    """ZNALEZISKO: `build_tools` bez jawnego egzekutora podstawia DockerSandboxExecutor.

    Przypadek `expect: allowed` dla `shell`/`run_tests` naprawdę wywołałby `docker run` —
    w CI, na maszynie operatora, przy każdym przebiegu bramki, wbrew obietnicy z docs.
    """
    config = _with_tool_loop(load_config(repo_config_dir), "bielik", ["shell"])
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="shell", action="run", expect="allowed"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")

    # Asercja MUSI być strukturalna: bez Dockera realny egzekutor i tak zwróciłby ok=False,
    # a `denied` pozostałoby False, więc przypadek przeszedłby w obie strony. Sprawdzamy
    # więc rzecz jedyną rozstrzygającą — czy ewaluacja W OGÓLE konstruuje sandbox Dockera.
    import husarz.tools.loader as loader

    class _Zakazany:
        def __init__(self, *a: Any, **k: Any) -> None:
            raise AssertionError("ewaluacja nie może konstruować DockerSandboxExecutor")

    oryginal = loader.DockerSandboxExecutor
    loader.DockerSandboxExecutor = _Zakazany  # type: ignore[misc,assignment]
    try:
        result = run_case(config, case, agents=agents, workspace=tmp_path)
    finally:
        loader.DockerSandboxExecutor = oryginal  # type: ignore[misc]
    # `run_case` łapie wyjątki szeroko, więc próba konstrukcji sandboxa nie wysadzi testu —
    # objawi się jako niezdany przypadek z komunikatem. Asercja musi to wyłapać.
    assert "DockerSandboxExecutor" not in result.detail, result.detail
    assert "błąd wykonania" not in result.detail, result.detail


def test_typo_in_action_is_rejected_not_silently_passed(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """ZNALEZISKO: literówka w akcji dawała `passed=True` dla `expect: allowed`.

    Bramka jej nie blokuje (narzędzie JEST na allowliście), więc `denied=False` zgadzało się
    z oczekiwaniem — a to, że akcja nie istnieje, pozostawało niewidoczne.
    """
    config = _with_tool_loop(load_config(repo_config_dir), "bielik", ["rag"])
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="bielik", tool="rag", action="searchh", expect="allowed"
    )
    agents = _agents(config, repo_config_dir.parent / "prompts")
    result = run_case(config, case, agents=agents, workspace=tmp_path)
    assert result.passed is False
    assert "nie jest wywoływalne" in result.detail


def test_roe_agent_counts_as_denied(repo_config_dir: Path, tmp_path: Path) -> None:
    """ZNALEZISKO: agent `roe_required` nie wchodzi w pętlę (L0), więc nie ma tury ACTION —
    a narzędzie jest przecież skutecznie zablokowane. Bez tej gałęzi niezmiennika „Puszkarz
    nie ma narzędzi" nie dało się wyrazić zdawalnym przypadkiem w ŻADNEJ konfiguracji."""
    config = load_config(repo_config_dir)
    cfg = config.agents["puszkarz"].model_copy(
        update={"tool_loop_enabled": True, "tools": ["rag"], "roe_required": True}
    )
    patched = config.model_copy(update={"agents": {**config.agents, "puszkarz": cfg}})
    case = ToolPolicyCase(
        name="t", kind="tool_policy", agent="puszkarz", tool="rag", action="search", expect="denied"
    )
    agents = _agents(patched, repo_config_dir.parent / "prompts")
    assert run_case(patched, case, agents=agents, workspace=tmp_path).passed is True


def test_empty_eval_returns_failure_not_green(repo_config_dir: Path, tmp_path: Path) -> None:
    """ZNALEZISKO: zielona bramka bez ani jednego pomiaru to najgorszy sygnał — CI melduje
    sukces, choć nic nie sprawdzono (brak podkatalogu evals/ w obrazie, zły wolumen)."""
    import shutil

    pusty = tmp_path / "config"
    shutil.copytree(repo_config_dir, pusty)
    shutil.rmtree(pusty / "evals")
    assert cli_main(["eval", "--config", str(pusty), "--prompts", "./prompts"]) == 1
    # ...ale jawna furtka pozostaje, gdy operator wie, co robi.
    assert (
        cli_main(["eval", "--config", str(pusty), "--prompts", "./prompts", "--allow-empty"]) == 0
    )
