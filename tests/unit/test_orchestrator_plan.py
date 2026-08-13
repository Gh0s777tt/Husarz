"""Testy parsowania planu i refleksji orkiestratora (odporność na szum modelu)."""

from __future__ import annotations

import pytest

from husarz.orchestrator import parse_plan, parse_reflection

pytestmark = pytest.mark.unit


def test_parse_plan_pure_json() -> None:
    plan_json = (
        '{"steps": [{"agent": "bielik", "task": "tłumacz"}, '
        '{"agent": "kopijnik", "task": "koduj"}]}'
    )
    plan = parse_plan(plan_json)
    assert [(s.agent, s.task) for s in plan.steps] == [("bielik", "tłumacz"), ("kopijnik", "koduj")]


def test_parse_plan_json_embedded_in_prose() -> None:
    text = 'Oto plan:\n{"steps": [{"agent": "bielik", "task": "x"}]}\nGotowe.'
    plan = parse_plan(text)
    assert len(plan.steps) == 1
    assert plan.steps[0].agent == "bielik"


def test_parse_plan_malformed_returns_empty() -> None:
    assert parse_plan("brak jakiegokolwiek json").steps == []
    assert parse_plan('{"steps": "zły typ"}').steps == []


def test_parse_plan_skips_incomplete_steps() -> None:
    plan = parse_plan('{"steps": [{"agent": "bielik"}, {"agent": "kopijnik", "task": "ok"}]}')
    assert [s.agent for s in plan.steps] == ["kopijnik"]  # krok bez 'task' pominięty


def test_parse_reflection_done() -> None:
    reflection = parse_reflection('{"done": true, "additional_steps": []}')
    assert reflection.done is True
    assert reflection.additional_steps == []


def test_parse_reflection_not_done_with_steps() -> None:
    reflection = parse_reflection(
        '{"done": false, "additional_steps": [{"agent": "kopijnik", "task": "popraw"}]}'
    )
    assert reflection.done is False
    assert reflection.additional_steps[0].agent == "kopijnik"


def test_parse_reflection_malformed_defaults_to_done() -> None:
    assert parse_reflection("??? brak json").done is True
