"""Testy regresyjne dla poprawek Etapu 2 po adwersaryjnym przeglądzie."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from husarz.agents import Towarzysz
from husarz.agents.errors import PromptNotFoundError
from husarz.agents.loader import _read_prompt
from husarz.config import HusarzConfig, load_config
from husarz.config.schema import AgentConfig
from husarz.orchestrator import (
    PHASE_PLAN,
    PHASE_REFLECT,
    PHASE_SYNTH,
    Orchestrator,
    build_orchestrator,
    parse_plan,
    parse_reflection,
)
from husarz.router import ChatResponse, select_candidates

pytestmark = pytest.mark.unit


class Rec:
    """Router testowy: dyspozycja po fazie (niezależnie od nazwy hetmana), echo dla specjalistów."""

    def __init__(
        self, plan: str, reflect: str = '{"done": true}', answer: str = "Synteza."
    ) -> None:
        self.calls: list[tuple] = []
        self._plan = plan
        self._reflect = reflect
        self._answer = answer

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        self.calls.append((agent, request.messages))
        content = request.messages[-1].content
        if PHASE_PLAN in content:
            return ChatResponse(model="glm", content=self._plan)
        if PHASE_REFLECT in content:
            return ChatResponse(model="glm", content=self._reflect)
        if PHASE_SYNTH in content:
            return ChatResponse(model="glm", content=self._answer)
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] {content}")


def _agents(with_roe: bool = False) -> dict:
    agents = {
        "husarz": Towarzysz(AgentConfig(name="husarz", prompt_file="husarz.md"), "Hetman."),
        "bielik": Towarzysz(AgentConfig(name="bielik", prompt_file="bielik.md"), "Bielik."),
        "kopijnik": Towarzysz(AgentConfig(name="kopijnik", prompt_file="kopijnik.md"), "Kopijnik."),
    }
    if with_roe:
        agents["puszkarz"] = Towarzysz(
            AgentConfig(name="puszkarz", prompt_file="puszkarz.md", roe_required=True), "Puszkarz."
        )
    return agents


def _husarz_bodies(router: Rec) -> list[str]:
    return [msgs[-1].content for a, msgs in router.calls if a == "husarz"]


# --------------------------------------------------------------------------
# Parsowanie (plan.py)
# --------------------------------------------------------------------------


def test_reflection_string_false_is_false() -> None:
    assert parse_reflection('{"done": "false", "additional_steps": []}').done is False
    assert parse_reflection('{"done": "no"}').done is False
    assert parse_reflection('{"done": "true"}').done is True


def test_reflection_done_absent_with_steps_is_not_done() -> None:
    reflection = parse_reflection('{"additional_steps": [{"agent": "kopijnik", "task": "x"}]}')
    assert reflection.done is False
    assert reflection.additional_steps[0].agent == "kopijnik"


def test_extract_first_valid_json_among_many() -> None:
    reflection = parse_reflection('{"done": false} potem {"steps": []}')
    assert reflection.done is False  # pierwszy poprawny obiekt wygrywa


def test_extract_skips_stray_braces_in_prose() -> None:
    plan = parse_plan('Rozważ zbiór {a, b} a potem {"steps": [{"agent": "x", "task": "y"}]}')
    assert [s.agent for s in plan.steps] == ["x"]


def test_parser_never_raises_on_recursion() -> None:
    assert parse_plan("[" * 4000).steps == []  # brak wyjątku
    assert parse_reflection("[" * 4000).done is True


def test_parse_steps_rejects_non_string_fields() -> None:
    assert parse_plan('{"steps": [{"agent": null, "task": {"x": 1}}]}').steps == []
    assert parse_plan('{"steps": [{"agent": "a", "task": ""}]}').steps == []


# --------------------------------------------------------------------------
# Router — pole 'model' z pliku agenta jako fallback
# --------------------------------------------------------------------------

_TWO_MODELS = {
    "default": "m1",
    "registry": {
        "m1": {"backend": "mock", "model": "a"},
        "m2": {"backend": "mock", "model": "b"},
    },
}


def test_agent_model_field_used_as_fallback() -> None:
    config = HusarzConfig(
        models=_TWO_MODELS,
        agents={"scout": {"name": "scout", "prompt_file": "scout.md", "model": "m2"}},
    )
    assert select_candidates(config, agent="scout") == ["m2"]


def test_routing_agent_models_takes_precedence_over_agent_field() -> None:
    config = HusarzConfig(
        models=_TWO_MODELS,
        routing={"agent_models": {"scout": "m1"}},
        agents={"scout": {"name": "scout", "prompt_file": "scout.md", "model": "m2"}},
    )
    assert select_candidates(config, agent="scout") == ["m1"]


# --------------------------------------------------------------------------
# Bezpieczeństwo — path traversal w prompt_file
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["../../etc/passwd", "C:/Windows/win.ini", "sub/dir.md", "no-extension", "..\\evil.md"],
)
def test_prompt_file_pattern_rejects_paths(bad: str) -> None:
    with pytest.raises(ValidationError):
        AgentConfig(name="x", prompt_file=bad)


def test_prompt_file_pattern_accepts_plain_name() -> None:
    assert AgentConfig(name="x", prompt_file="bielik.md").prompt_file == "bielik.md"


def test_read_prompt_confines_to_prompts_dir(tmp_path: Path) -> None:
    # Defense-in-depth w loaderze (nawet gdyby nazwa ominęła walidację schematu).
    with pytest.raises(PromptNotFoundError):
        _read_prompt(tmp_path, "../../../secret.md")


# --------------------------------------------------------------------------
# Orkiestrator — kontekst, ROE-gate, izolacja, rundy refleksji
# --------------------------------------------------------------------------


def test_reflection_step_receives_prior_observations_as_context() -> None:
    plan = '{"steps": [{"agent": "bielik", "task": "Znajdź X"}]}'
    reflect = '{"done": false, "additional_steps": [{"agent": "kopijnik", "task": "Użyj X"}]}'
    router = Rec(plan=plan, reflect=reflect)
    Orchestrator(_agents(), router).run("Zadanie")

    kop_msgs = next(msgs for a, msgs in router.calls if a == "kopijnik")
    joined = " ".join(m.content for m in kop_msgs)
    assert "[bielik] Znajdź X" in joined  # obserwacja bielika przekazana jako kontekst
    assert "nie instrukcje" in joined  # ogrodzona i oznaczona jako dane


def test_roe_required_agent_is_not_delegated() -> None:
    plan = '{"steps": [{"agent": "puszkarz", "task": "skan"}, {"agent": "bielik", "task": "opis"}]}'
    router = Rec(plan=plan)
    result = Orchestrator(_agents(with_roe=True), router).run("Zadanie")

    assert result.observations[0].output.startswith("[pominięto: agent wymaga aktywnego ROE")
    assert result.observations[1].agent == "bielik"
    assert "puszkarz" not in [a for a, _ in router.calls]  # nigdy nie wywołany


def test_observations_are_fenced_when_isolation_on() -> None:
    router = Rec(plan='{"steps": [{"agent": "bielik", "task": "x"}]}')
    Orchestrator(_agents(), router, isolate_untrusted=True).run("Zadanie")
    reflect_body = next(b for b in _husarz_bodies(router) if PHASE_REFLECT in b)
    assert "<<<OBSERWACJE" in reflect_body and ">>>OBSERWACJE" in reflect_body


def test_observations_not_fenced_when_isolation_off() -> None:
    router = Rec(plan='{"steps": [{"agent": "bielik", "task": "x"}]}')
    Orchestrator(_agents(), router, isolate_untrusted=False).run("Zadanie")
    reflect_body = next(b for b in _husarz_bodies(router) if PHASE_REFLECT in b)
    assert "<<<OBSERWACJE" not in reflect_body
    assert "Obserwacje:" in reflect_body


def test_max_extra_rounds_zero_skips_reflection() -> None:
    reflect = '{"done": false, "additional_steps": [{"agent": "kopijnik", "task": "y"}]}'
    router = Rec(plan='{"steps": [{"agent": "bielik", "task": "x"}]}', reflect=reflect)
    result = Orchestrator(_agents(), router, max_extra_rounds=0).run("Zadanie")
    assert result.rounds == 0
    assert not any(PHASE_REFLECT in b for b in _husarz_bodies(router))
    assert [o.agent for o in result.observations] == ["bielik"]


def test_multiple_reflection_rounds_bounded_by_limit() -> None:
    reflect = '{"done": false, "additional_steps": [{"agent": "kopijnik", "task": "y"}]}'
    router = Rec(plan='{"steps": [{"agent": "bielik", "task": "x"}]}', reflect=reflect)
    result = Orchestrator(_agents(), router, max_extra_rounds=2).run("Zadanie")
    assert result.rounds == 2
    assert [o.agent for o in result.observations] == ["bielik", "kopijnik", "kopijnik"]
    assert sum(1 for b in _husarz_bodies(router) if PHASE_REFLECT in b) == 2


def test_delegation_to_hetman_is_skipped() -> None:
    plan = '{"steps": [{"agent": "husarz", "task": "x"}, {"agent": "bielik", "task": "y"}]}'
    router = Rec(plan=plan)
    result = Orchestrator(_agents(), router).run("Zadanie")
    assert result.observations[0].agent == "husarz"
    assert result.observations[0].output.startswith("[pominięto")
    # Hetman nie został wywołany jako specjalista (żadne wywołanie z treścią liścia 'x').
    assert not any(a == "husarz" and msgs[-1].content == "x" for a, msgs in router.calls)


def test_empty_observations_render_brak() -> None:
    router = Rec(plan="brak jakiegokolwiek json")  # pusty plan
    Orchestrator(_agents(), router).run("Zadanie")
    synth_body = next(b for b in _husarz_bodies(router) if PHASE_SYNTH in b)
    assert "(brak)" in synth_body


# --------------------------------------------------------------------------
# build_orchestrator — custom hetman, wiring izolacji z konfiguracji
# --------------------------------------------------------------------------


def test_build_orchestrator_custom_name_and_rounds(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    router = Rec(plan='{"steps": [{"agent": "kopijnik", "task": "x"}]}')
    orchestrator = build_orchestrator(
        config,
        router,
        prompts_dir=repo_config_dir.parent / "prompts",
        orchestrator_name="bielik",
        max_extra_rounds=0,
    )
    result = orchestrator.run("Zadanie")

    assert result.rounds == 0
    assert result.answer == "Synteza."
    # Bielik był hetmanem (faza planowania) — potwierdza custom orchestrator_name.
    assert any(a == "bielik" and PHASE_PLAN in msgs[-1].content for a, msgs in router.calls)
    # Izolacja włączona z konfiguracji (prompt_injection_filters=true) — synteza ogrodzona.
    synth_body = next(
        msgs[-1].content
        for a, msgs in router.calls
        if a == "bielik" and PHASE_SYNTH in msgs[-1].content
    )
    assert "<<<OBSERWACJE" in synth_body
