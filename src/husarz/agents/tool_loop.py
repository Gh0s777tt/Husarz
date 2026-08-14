"""Pętla narzędziowa (function-calling, ReAct) — PIERWSZY egzekutor narzędzi.

Cykl: model → parse akcji → autoryzacja per-wywołanie (deny-by-default) → jednolity
dispatch → ``ToolResult`` → wynik NIEZAUFANY ogrodzony i podany z powrotem (role='user')
→ … aż do odpowiedzi końcowej (brak bloku akcji) albo do ``agent.max_iterations`` /
globalnego budżetu wywołań. Autoryzacja warstwowo (patrz ADR-0016):

- **L0** ``roe_required`` wyklucza agenta z generycznej pętli (fail-closed) — Puszkarz
  pozostaje bramkowany w orkiestratorze, generyczne wywołania NIE idą przez ROE.
- **L1** allowlista agenta (``agent.config.tools``) — sprawdzana PRZED dispatchem.
- **L2** walidacja dispatchu (nieznane tool/action/args → ``ToolResult(ok=False)``).
- **L3** bramki wewnątrz narzędzi (sandbox/egress) — bez zmian, defense-in-depth.

Wynik injection w treści narzędzia może co najwyżej skłonić model do wywołania narzędzia
Z ALLOWLISTY — promień rażenia ogranicza statyczne nadanie zdolności agenta + bramki
narzędzia. Pętla NIE eskaluje uprawnień. Ogrodzenie NL to obrona miękka (jak załączniki).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from husarz.agents.base import AgentResult, BaseAgent, SupportsComplete
from husarz.agents.react import ParseKind, ToolAction, parse_action, protocol_instructions
from husarz.config.schema import HusarzConfig, ToolLoopConfig
from husarz.config.secrets import SecretsProvider
from husarz.fencing import fence_untrusted
from husarz.router.types import ChatMessage, ChatRequest
from husarz.security.audit import AuditLog
from husarz.tools.base import ToolResult
from husarz.tools.dispatch import ToolDispatcher
from husarz.tools.loader import build_tools
from husarz.tools.rag import RagBackend
from husarz.tools.registry import ToolProviderRegistry
from husarz.tools.sandbox import SandboxExecutor
from husarz.tools.web import Fetcher

if TYPE_CHECKING:
    from husarz.plugins.service import PluginService


@dataclass(slots=True)
class ToolCallBudget:
    """Globalny budżet wywołań narzędzi na CAŁĄ orkiestrację (mutowalny licznik).

    Tworzony ŚWIEŻO per ``Orchestrator.run`` (nie na ToolLoop — inaczej współdzielony
    stan między żądaniami/wątkami). Bounduje amplifikację: kroki × iteracje × rundy.
    """

    remaining: int

    def try_spend(self) -> bool:
        """Zużywa jeden token budżetu; ``False`` gdy wyczerpany (fail-closed)."""
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _summarize_url(url: str) -> dict[str, Any]:
    # Audyt egress bez surowego ładunku: host + długość ścieżki + skrót query (eksfiltracja
    # przez query jest wtedy wykrywalna, ale sekret/PII nie trafia do dziennika).
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "",
        "path_len": len(parsed.path),
        "query_sha256": _sha12(parsed.query) if parsed.query else "",
    }


def _arg_summary(args: dict[str, Any]) -> dict[str, Any]:
    """Sanityzuje argumenty do audytu — bez surowej treści/sekretów/PII (skróty/rozmiary)."""
    out: dict[str, Any] = {}
    for key, value in args.items():
        if key == "url" and isinstance(value, str):
            out["url"] = _summarize_url(value)
        elif key in ("content", "text") and isinstance(value, str):
            out[key] = {"bytes": len(value.encode("utf-8")), "sha256": _sha12(value)}
        elif key == "arguments":
            # plugin.call: 'arguments' to KANAŁ EGRESS (do max_call_bytes treści na serwer MCP).
            # ZAWSZE {rozmiar, skrót} — także gdy model poda zły typ (inaczej surowa treść wpadłaby
            # do gałęzi generycznej i wyciekła do audytu). Eksfiltracja wykrywalna, treść ukryta.
            blob = (
                json.dumps(value, sort_keys=True, ensure_ascii=False)
                if isinstance(value, dict)
                else str(value)
            )
            out[key] = {"bytes": len(blob.encode("utf-8")), "sha256": _sha12(blob)}
        elif key in ("command", "args", "extra_args") and isinstance(value, list):
            out[key] = " ".join(str(item) for item in value)[:200]
        elif isinstance(value, (str, int, float, bool)):
            out[key] = str(value)[:200]
        else:
            out[key] = f"<{type(value).__name__}>"
    return out


def _result_text(result: ToolResult) -> str:
    parts = [f"ok={result.ok}"]
    if result.output:
        parts.append(result.output)
    if result.error:
        parts.append(f"BŁĄD: {result.error}")
    return "\n".join(parts)


class ToolLoop:
    """Wykonuje zadanie agenta w pętli ReAct z autoryzacją per-wywołanie i audytem."""

    def __init__(self, dispatcher: ToolDispatcher, audit: AuditLog, config: ToolLoopConfig) -> None:
        self._dispatcher = dispatcher
        self._audit = audit
        self._config = config

    def supports(self, agent: BaseAgent) -> bool:
        """Czy agent wchodzi w pętlę: jawny opt-in + niepusta allowlista + brak ROE."""
        return (
            agent.config.tool_loop_enabled
            and bool(agent.config.tools)
            and not agent.config.roe_required
        )

    def new_budget(self) -> ToolCallBudget:
        """Tworzy świeży budżet wywołań dla jednej orkiestracji."""
        return ToolCallBudget(self._config.max_total_calls)

    def run(
        self,
        agent: BaseAgent,
        task: str,
        *,
        router: SupportsComplete,
        context: str | None = None,
        budget: ToolCallBudget,
    ) -> AgentResult:
        """Wykonuje zadanie z narzędziami. Zawsze kończy deterministycznie (limit/budżet)."""
        # L0 (fail-closed, redundantnie wobec supports): ROE-agent nie wchodzi w pętlę.
        if agent.config.roe_required:
            self._audit.record(agent.name, "toolloop.refuse_roe", {"task_len": len(task)})
            return AgentResult(agent.name, "Agent wymaga ROE — pętla narzędziowa niedostępna.", "")

        manual = self._dispatcher.manual(agent.tools)
        system = f"{agent.system_prompt}\n\n{protocol_instructions(manual)}"
        messages: list[ChatMessage] = [ChatMessage("system", system)]
        if context:
            # PARYTET z BaseAgent: kontekst (wcześniejsze obserwacje) jest NIEZAUFANY → ogrodzony.
            fenced_ctx, _ = fence_untrusted(
                "kontekst", context, max_bytes=self._config.max_result_bytes
            )
            messages.append(ChatMessage("user", fenced_ctx))
        messages.append(ChatMessage("user", task))

        allowed = set(agent.tools)
        last_model = ""
        for _ in range(max(1, agent.config.max_iterations)):
            response = router.complete(ChatRequest(messages=list(messages)), agent=agent.name)
            last_model = response.model
            messages.append(ChatMessage("assistant", response.content))
            parsed = parse_action(response.content)

            if parsed.kind is ParseKind.FINAL:
                return AgentResult(agent.name, parsed.text, last_model)
            if parsed.kind is ParseKind.MALFORMED:
                messages.append(
                    ChatMessage(
                        "user",
                        f"Błąd protokołu: {parsed.text} Popraw format bloku akcji lub odpowiedz "
                        "ostatecznie bez bloku.",
                    )
                )
                continue

            action: ToolAction = parsed.tool_action  # type: ignore[assignment]
            result = self._authorize_and_dispatch(agent, action, allowed, budget)
            if result is None:  # budżet wyczerpany — deterministyczne zakończenie
                self._audit.record(agent.name, "toolloop.budget", {"tool": action.tool[:64]})
                return AgentResult(
                    agent.name,
                    "Przerwano: wyczerpano globalny budżet wywołań narzędzi.",
                    last_model,
                )
            fenced, _ = fence_untrusted(
                f"{action.tool}.{action.action}",
                _result_text(result),
                max_bytes=self._config.max_result_bytes,
            )
            messages.append(ChatMessage("user", fenced))

        self._audit.record(
            agent.name, "toolloop.limit", {"max_iterations": agent.config.max_iterations}
        )
        return AgentResult(agent.name, "Osiągnięto limit iteracji pętli narzędziowej.", last_model)

    def _authorize_and_dispatch(
        self,
        agent: BaseAgent,
        action: ToolAction,
        allowed: set[str],
        budget: ToolCallBudget,
    ) -> ToolResult | None:
        """L1 allowlista → budżet → L2 dispatch. ``None`` = budżet wyczerpany (przerwij)."""
        if action.tool not in allowed:
            # L1: narzędzie spoza allowlisty agenta — instancja NIGDY nie jest wołana.
            self._audit.record(
                agent.name,
                "tool.deny",
                {"tool": action.tool[:64], "action": action.action[:64], "reason": "allowlist"},
            )
            return ToolResult(action.tool, ok=False, error="Narzędzie spoza allowlisty agenta.")
        if not budget.try_spend():
            return None
        result = self._dispatcher.dispatch(action.tool, action.action, action.args)
        self._audit.record(
            agent.name,
            "tool.call",
            {
                "tool": action.tool[:64],
                "action": action.action[:64],
                "ok": result.ok,
                "args": _arg_summary(action.args),
                "bytes": len(result.output.encode("utf-8")),
            },
        )
        return result


def build_tool_loop(
    config: HusarzConfig,
    *,
    workspace: str | Path,
    audit: AuditLog,
    executor: SandboxExecutor | None = None,
    fetcher: Fetcher | None = None,
    rag_backend: RagBackend | None = None,
    secrets: SecretsProvider | None = None,
    data_dir: str | Path | None = None,
    plugin_service: PluginService | None = None,
    registry: ToolProviderRegistry | None = None,
) -> ToolLoop:
    """Buduje ``ToolLoop`` z konfiguracji (mirror ``build_tools``/``build_plugin_service``).

    ``secrets``/``data_dir`` przewleczone do budowy trwałej pamięci RAG (sqlite + at-rest);
    domyślnie ``data_dir`` z ``config.platform``. ``plugin_service`` (ten sam co ``/api/plugins``)
    zasila narzędzia ``kind=plugin`` (``list``/``call``); ``None`` → degradacja do ``ok=False``.
    """
    tools = build_tools(
        config,
        workspace=workspace,
        executor=executor,
        fetcher=fetcher,
        rag_backend=rag_backend,
        secrets=secrets,
        data_dir=data_dir if data_dir is not None else config.platform.data_dir,
        plugin_service=plugin_service,
        registry=registry,
    )
    kind_of = {name: tool_config.kind for name, tool_config in config.tools.items()}
    descriptions = {name: tool_config.description for name, tool_config in config.tools.items()}
    dispatcher = ToolDispatcher(
        tools,
        kind_of,
        descriptions=descriptions,
        max_rag_add_bytes=config.security.tool_loop.max_rag_add_bytes,
    )
    return ToolLoop(dispatcher, audit, config.security.tool_loop)
