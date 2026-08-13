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


_TRUE_STRINGS = frozenset({"true", "1", "yes", "tak"})
_JSON_DECODER = json.JSONDecoder()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Wyłuskuje PIERWSZY poprawny obiekt JSON z tekstu (czysty lub osadzony w prozie).

    Skanuje kolejne pozycje ``{`` i próbuje ``raw_decode`` — dzięki temu obce
    nawiasy w prozie czy wiele obiektów nie psują wyniku (inaczej niż find/rfind).
    Nigdy nie rzuca: ``RecursionError``/``JSONDecodeError`` na złośliwym wejściu
    kończy się ``None`` (kontrakt: szum modelu -> brak obiektu, nie wyjątek).
    """
    stripped = text.strip()
    try:
        whole = json.loads(stripped)
        if isinstance(whole, dict):
            return whole
    except (json.JSONDecodeError, RecursionError):
        pass

    index = stripped.find("{")
    while index != -1:
        try:
            candidate, _ = _JSON_DECODER.raw_decode(stripped, index)
        except (json.JSONDecodeError, RecursionError):
            candidate = None
        if isinstance(candidate, dict):
            return candidate
        index = stripped.find("{", index + 1)
    return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Interpretuje ``done`` odpornie: bool wprost, string 'true/1/yes/tak', brak -> default."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return bool(value)


def _parse_steps(raw: Any) -> list[PlanStep]:
    """Buduje kroki tylko z pozycji o niepustych, tekstowych polach agent/task."""
    steps: list[PlanStep] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            agent = item.get("agent")
            task = item.get("task")
            if isinstance(agent, str) and agent and isinstance(task, str) and task:
                steps.append(PlanStep(agent=agent, task=task))
    return steps


def parse_plan(text: str) -> Plan:
    """Parsuje odpowiedź modelu na plan. Nieparsowalne wejście = pusty plan."""
    obj = _extract_json_object(text)
    if obj is None:
        return Plan()
    return Plan(steps=_parse_steps(obj.get("steps")))


def parse_reflection(text: str) -> Reflection:
    """Parsuje refleksję. Nieparsowalne wejście = zakończ (``done=True``).

    Gdy klucz ``done`` jest nieobecny, domyślamy go na podstawie obecności kroków
    (kroki obecne -> ``done=False``), by nie porzucić jawnie podanych ``additional_steps``.
    """
    obj = _extract_json_object(text)
    if obj is None:
        return Reflection(done=True)
    additional = _parse_steps(obj.get("additional_steps"))
    done = _coerce_bool(obj.get("done"), default=not additional)
    return Reflection(done=done, additional_steps=additional)
