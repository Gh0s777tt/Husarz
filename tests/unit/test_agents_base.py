"""Testy bazowej klasy agenta (Towarzysz/Pocztowy dzielą zachowanie run w Etapie 2)."""

from __future__ import annotations

import pytest

from husarz.agents import Pocztowy, Towarzysz
from husarz.config.schema import AgentConfig
from husarz.router import ChatResponse

pytestmark = pytest.mark.unit


class EchoRouter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        self.calls.append((agent, request.messages))
        return ChatResponse(model=f"mock-{agent}", content=f"echo:{request.messages[-1].content}")


def test_towarzysz_run_uses_router_and_system_prompt() -> None:
    agent = Towarzysz(AgentConfig(name="bielik", prompt_file="bielik.md"), "Jesteś Bielik.")
    router = EchoRouter()
    result = agent.run("Przetłumacz tekst.", router=router)

    assert result.agent == "bielik"
    assert result.model == "mock-bielik"
    assert result.output == "echo:Przetłumacz tekst."

    agent_arg, messages = router.calls[0]
    assert agent_arg == "bielik"  # router dobiera model po nazwie agenta
    assert messages[0].role == "system"
    assert "Bielik" in messages[0].content
    assert messages[1].role == "user"
    assert messages[1].content == "Przetłumacz tekst."


def test_run_puts_context_in_fenced_user_message_not_system() -> None:
    agent = Pocztowy(AgentConfig(name="chorazy", prompt_file="chorazy.md"), "Jesteś Chorąży.")
    router = EchoRouter()
    agent.run("Sklasyfikuj intencję.", router=router, context="Wcześniej: kod PL.")

    _, messages = router.calls[0]
    # System prompt pozostaje czysty (zaufany) — bez kontekstu.
    assert messages[0].role == "system"
    assert messages[0].content == "Jesteś Chorąży."
    # Kontekst trafia do wiadomości user, ogrodzony i oznaczony jako dane.
    context_msgs = [m for m in messages if m.role == "user" and "Wcześniej: kod PL." in m.content]
    assert context_msgs
    assert "nie instrukcje" in context_msgs[0].content
    # Ostatnia wiadomość to samo zadanie.
    assert messages[-1].content == "Sklasyfikuj intencję."


def test_agent_exposes_tools_allowlist() -> None:
    agent = Towarzysz(
        AgentConfig(name="kopijnik", prompt_file="kopijnik.md", tools=["file_edit", "git"]),
        "Jesteś Kopijnik.",
    )
    assert agent.tools == ["file_edit", "git"]
