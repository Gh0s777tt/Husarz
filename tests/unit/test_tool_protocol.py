"""Testy protokołu ReAct pętli narzędziowej (parse_action + instrukcje)."""

from __future__ import annotations

import pytest

from husarz.agents.react import (
    ACTION_CLOSE,
    ACTION_OPEN,
    ParseKind,
    parse_action,
    protocol_instructions,
)

pytestmark = pytest.mark.unit


def _wrap(json_text: str) -> str:
    return f"{ACTION_OPEN}{json_text}{ACTION_CLOSE}"


def test_no_marker_is_final() -> None:
    result = parse_action("Oto ostateczna odpowiedź dla użytkownika.")
    assert result.kind is ParseKind.FINAL
    assert result.text == "Oto ostateczna odpowiedź dla użytkownika."


def test_valid_action_parsed() -> None:
    result = parse_action(_wrap('{"tool":"web","action":"fetch","args":{"url":"https://x"}}'))
    assert result.kind is ParseKind.ACTION
    assert result.tool_action is not None
    assert result.tool_action.tool == "web"
    assert result.tool_action.action == "fetch"
    assert result.tool_action.args == {"url": "https://x"}


def test_action_without_args_defaults_empty() -> None:
    result = parse_action(_wrap('{"tool":"run_tests","action":"run"}'))
    assert result.kind is ParseKind.ACTION
    assert result.tool_action is not None
    assert result.tool_action.args == {}


def test_marker_with_bad_json_is_malformed() -> None:
    result = parse_action(_wrap("{to nie jest json"))
    assert result.kind is ParseKind.MALFORMED


def test_marker_missing_fields_is_malformed() -> None:
    result = parse_action(_wrap('{"tool":"web"}'))  # brak action
    assert result.kind is ParseKind.MALFORMED


def test_action_with_prose_before_marker() -> None:
    block = _wrap('{"tool":"web","action":"fetch","args":{}}')
    result = parse_action("Muszę pobrać stronę.\n" + block)
    assert result.kind is ParseKind.ACTION


def test_first_action_wins_on_multiple() -> None:
    text = _wrap('{"tool":"a","action":"x","args":{}}') + _wrap(
        '{"tool":"b","action":"y","args":{}}'
    )
    result = parse_action(text)
    assert result.tool_action is not None and result.tool_action.tool == "a"


def test_prose_mentioning_tools_without_marker_is_final() -> None:
    # Brak false-positive: sama wzmianka o narzędziach bez markera = odpowiedź końcowa.
    result = parse_action('Rozważyłem użycie narzędzia "web" ale odpowiadam wprost.')
    assert result.kind is ParseKind.FINAL


def test_missing_close_marker_tolerated() -> None:
    result = parse_action(f'{ACTION_OPEN}{{"tool":"web","action":"fetch","args":{{}}}}')
    assert result.kind is ParseKind.ACTION


def test_instructions_contain_manual_and_markers() -> None:
    instr = protocol_instructions('- narzędzie "web"')
    assert ACTION_OPEN in instr and ACTION_CLOSE in instr
    assert 'narzędzie "web"' in instr
