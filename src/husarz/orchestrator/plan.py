"""Struktury i parsowanie planu oraz refleksji orkiestratora.

Parsowanie jest odporne (lenient): akceptuje czysty JSON albo JSON osadzony w
tekście (pierwszy blok ``{...}``). Niepoprawne wejście daje pusty plan / refleksję
„zakończ", a nie wyjątek — orkiestrator ma działać mimo szumu z modelu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlanStep:
    """Pojedynczy krok planu: który agent i jakie zadanie."""

    agent: str
    task: str


@dataclass(slots=True)
class Plan:
    """Plan wykonania — uporządkowana lista kroków."""

    steps: list[PlanStep] = field(default_factory=list)


@dataclass(slots=True)
class Reflection:
    """Wynik refleksji: czy zakończyć oraz ewentualne dodatkowe kroki."""

    done: bool
    additional_steps: list[PlanStep] = field(default_factory=list)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Wyłuskuje obiekt JSON z tekstu (czysty albo osadzony w prozie)."""
    stripped = text.strip()
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(stripped[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _parse_steps(raw: Any) -> list[PlanStep]:
    steps: list[PlanStep] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and "agent" in item and "task" in item:
                steps.append(PlanStep(agent=str(item["agent"]), task=str(item["task"])))
    return steps


def parse_plan(text: str) -> Plan:
    """Parsuje odpowiedź modelu na plan. Nieparsowalne wejście = pusty plan."""
    obj = _extract_json_object(text)
    if obj is None:
        return Plan()
    return Plan(steps=_parse_steps(obj.get("steps")))


def parse_reflection(text: str) -> Reflection:
    """Parsuje refleksję. Nieparsowalne wejście = zakończ (``done=True``)."""
    obj = _extract_json_object(text)
    if obj is None:
        return Reflection(done=True)
    return Reflection(
        done=bool(obj.get("done", True)),
        additional_steps=_parse_steps(obj.get("additional_steps")),
    )
