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
from husarz.router.types import ChatMessage, ChatRequest, Usage, UsageMeter
from husarz.runs import (
    NullRunStore,
    RunRecord,
    RunStep,
    RunStore,
    StepKind,
    Termination,
    build_run_store_from_config,
)
from husarz.security.audit import AuditLog
from husarz.ssrf import HostResolver
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


# Zastępnik dla nazw spoza zbiorów zamkniętych — patrz komentarz przy budowie RunStep.
_UNKNOWN_NAME = "<nieznane>"


def _tokens(usage: Usage | None) -> int:
    """Zużycie tokenów jako liczba — ``0``, gdy backend go nie raportuje.

    Podwójny ``None`` jest tu realny: ``UsageMeter.snapshot()`` zwraca ``None``, gdy żaden
    backend nic nie zgłosił, a samo ``Usage.total_tokens`` też bywa ``None``. Pomiar musi
    dawać liczbę zawsze — inaczej rekord przebiegu przestaje się sumować.
    """
    return (usage.total_tokens or 0) if usage is not None else 0


def _result_text(result: ToolResult) -> str:
    parts = [f"ok={result.ok}"]
    if result.output:
        parts.append(result.output)
    if result.error:
        parts.append(f"BŁĄD: {result.error}")
    return "\n".join(parts)


class ToolLoop:
    """Wykonuje zadanie agenta w pętli ReAct z autoryzacją per-wywołanie i audytem."""

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        audit: AuditLog,
        config: ToolLoopConfig,
        runs: RunStore | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._audit = audit
        self._config = config
        # Pomiar jakości (Etap 16) — domyślnie NullRunStore, czyli nic nie zapisujemy.
        # Zbieranie jest opt-in, tak jak sama pętla narzędziowa (ADR-0016).
        self._runs: RunStore = runs if runs is not None else NullRunStore()

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

    def close(self) -> None:
        """Zwalnia zasoby narzędzi (deleguje do dispatchu). Woła się przy podmianie stacku."""
        self._dispatcher.close()

    def run(
        self,
        agent: BaseAgent,
        task: str,
        *,
        router: SupportsComplete,
        context: str | None = None,
        budget: ToolCallBudget,
        principal: str = "",
        run_id: str = "",
    ) -> AgentResult:
        """Wykonuje zadanie z narzędziami. Zawsze kończy deterministycznie (limit/budżet).

        ``principal`` (kto ZLECIŁ) trafia do każdego wpisu audytu obok ``actor`` (kto
        wykonał). Bez tego dziennik odpowiada „kopijnik wywołał shell", ale nie „na czyje
        żądanie" — a przy wielu kontach to właśnie ta druga informacja jest rozliczalna.
        """
        # L0 (fail-closed, redundantnie wobec supports): ROE-agent nie wchodzi w pętlę.
        record = RunRecord(
            run_id=run_id, agent=agent.name, principal=principal, task_chars=len(task)
        )
        if agent.config.roe_required:
            self._audit.record(
                agent.name, "toolloop.refuse_roe", {"task_len": len(task)}, principal=principal
            )
            record.termination = Termination.ROE_REFUSED
            self._runs.save(record)
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
        # Pętla to WIELE wywołań modelu na jedno zadanie — sumujemy zużycie, inaczej
        # rozliczenie limitu widziałoby tylko ostatnią iterację.
        meter = UsageMeter()
        for index in range(max(1, agent.config.max_iterations)):
            response = router.complete(ChatRequest(messages=list(messages)), agent=agent.name)
            meter.add(response.usage)
            last_model = response.model
            messages.append(ChatMessage("assistant", response.content))
            parsed = parse_action(response.content)
            turn_tokens = _tokens(response.usage)

            if parsed.kind is ParseKind.FINAL:
                record.steps.append(
                    RunStep(
                        index=index,
                        kind=StepKind.FINAL,
                        model=last_model,
                        output_chars=len(parsed.text),
                        total_tokens=turn_tokens,
                    )
                )
                record.termination = Termination.FINAL
                record.total_tokens = _tokens(meter.snapshot())
                self._runs.save(record)
                return AgentResult(agent.name, parsed.text, last_model, meter.snapshot())
            if parsed.kind is ParseKind.MALFORMED:
                record.steps.append(
                    RunStep(
                        index=index,
                        kind=StepKind.MALFORMED,
                        model=last_model,
                        output_chars=len(response.content),
                        total_tokens=turn_tokens,
                    )
                )
                messages.append(
                    ChatMessage(
                        "user",
                        f"Błąd protokołu: {parsed.text} Popraw format bloku akcji lub odpowiedz "
                        "ostatecznie bez bloku.",
                    )
                )
                continue

            action: ToolAction = parsed.tool_action  # type: ignore[assignment]
            result = self._authorize_and_dispatch(agent, action, allowed, budget, principal)
            if result is None:  # budżet wyczerpany — deterministyczne zakończenie
                self._audit.record(
                    agent.name, "toolloop.budget", {"tool": action.tool[:64]}, principal=principal
                )
                # Tura MIAŁA miejsce (model odpowiedział, tokeny poszły) — pominięcie jej
                # zaniżałoby sumę tur i zawyżało `malformed_ratio`, bo mianownik byłby mniejszy.
                record.steps.append(
                    RunStep(
                        index=index,
                        kind=StepKind.ACTION,
                        model=last_model,
                        output_chars=len(response.content),
                        total_tokens=turn_tokens,
                    )
                )
                record.termination = Termination.BUDGET
                record.total_tokens = _tokens(meter.snapshot())
                self._runs.save(record)
                return AgentResult(
                    agent.name,
                    "Przerwano: wyczerpano globalny budżet wywołań narzędzi.",
                    last_model,
                    meter.snapshot(),
                )
            # `denied` odróżnia „narzędzie zawiodło" od „bramka nie wpuściła" — bez tego
            # rozróżnienia nie da się zmierzyć skuteczności allowlisty agenta.
            # Nazwy pochodzą z bloku akcji, czyli OD MODELU. Do rekordu wpuszczamy je
            # wyłącznie, gdy należą do zbiorów zamkniętych (allowlista agenta / rejestr
            # akcji) — inaczej model mógłby przemycić dowolne 64 znaki treści do pliku,
            # który z założenia treści nie niesie.
            record.steps.append(
                RunStep(
                    index=index,
                    kind=StepKind.ACTION,
                    model=last_model,
                    tool=action.tool if action.tool in allowed else _UNKNOWN_NAME,
                    action=(
                        action.action
                        if self._dispatcher.supports(action.tool, action.action)
                        else _UNKNOWN_NAME
                    ),
                    ok=result.ok,
                    denied=bool(result.metadata.get("denied")),
                    output_chars=len(response.content),
                    total_tokens=turn_tokens,
                )
            )
            fenced, _ = fence_untrusted(
                f"{action.tool}.{action.action}",
                _result_text(result),
                max_bytes=self._config.max_result_bytes,
            )
            messages.append(ChatMessage("user", fenced))

        self._audit.record(
            agent.name,
            "toolloop.limit",
            {"max_iterations": agent.config.max_iterations},
            principal=principal,
        )
        record.termination = Termination.ITERATION_LIMIT
        record.total_tokens = _tokens(meter.snapshot())
        self._runs.save(record)
        return AgentResult(
            agent.name,
            "Osiągnięto limit iteracji pętli narzędziowej.",
            last_model,
            meter.snapshot(),
        )

    def _authorize_and_dispatch(
        self,
        agent: BaseAgent,
        action: ToolAction,
        allowed: set[str],
        budget: ToolCallBudget,
        principal: str = "",
    ) -> ToolResult | None:
        """L1 allowlista → budżet → L2 dispatch. ``None`` = budżet wyczerpany (przerwij)."""
        if action.tool not in allowed:
            # L1: narzędzie spoza allowlisty agenta — instancja NIGDY nie jest wołana.
            self._audit.record(
                agent.name,
                "tool.deny",
                {"tool": action.tool[:64], "action": action.action[:64], "reason": "allowlist"},
                principal=principal,
            )
            # `denied` w metadanych, a nie porównanie treści komunikatu: pomiar nie może
            # zależeć od brzmienia napisu widzianego przez model.
            return ToolResult(
                action.tool,
                ok=False,
                error="Narzędzie spoza allowlisty agenta.",
                metadata={"denied": True},
            )
        if not budget.try_spend():
            return None
        result = self._dispatcher.dispatch(action.tool, action.action, action.args)
        entry: dict[str, Any] = {
            "tool": action.tool[:64],
            "action": action.action[:64],
            "ok": result.ok,
            "args": _arg_summary(action.args),
            "bytes": len(result.output.encode("utf-8")),
        }
        pinned_ip = result.metadata.get("pinned_ip")
        if isinstance(pinned_ip, str):
            # Z JAKIM adresem faktycznie się połączyliśmy (narzędzie web, ADR-0020) — bez tego
            # audyt zna tylko nazwę hosta, a przy pinowaniu to nazwa jest mniej informatywna.
            entry["pinned_ip"] = pinned_ip[:64]
        self._audit.record(agent.name, "tool.call", entry, principal=principal)
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
    resolve: HostResolver | None = None,
) -> ToolLoop:
    """Buduje ``ToolLoop`` z konfiguracji (mirror ``build_tools``/``build_plugin_service``).

    ``secrets``/``data_dir`` przewleczone do budowy trwałej pamięci RAG (sqlite + at-rest);
    domyślnie ``data_dir`` z ``config.platform``. ``plugin_service`` (ten sam co ``/api/plugins``)
    zasila narzędzia ``kind=plugin`` (``list``/``call``); ``None`` → degradacja do ``ok=False``.
    ``resolve`` — resolver DNS dla pinowania IP narzędzia ``web`` (ADR-0020).
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
        resolve=resolve,
    )
    kind_of = {name: tool_config.kind for name, tool_config in config.tools.items()}
    descriptions = {name: tool_config.description for name, tool_config in config.tools.items()}
    dispatcher = ToolDispatcher(
        tools,
        kind_of,
        descriptions=descriptions,
        max_rag_add_bytes=config.security.tool_loop.max_rag_add_bytes,
    )
    # Pomiar jakości (Etap 16): domyślnie WYŁĄCZONY (NullRunStore). Włączenie wymaga
    # `platform.runs.enabled: true` — nowa instalacja nie zaczyna po cichu zapisywać
    # danych o pracy operatora.
    run_store = build_run_store_from_config(config, data_dir=data_dir)
    return ToolLoop(dispatcher, audit, config.security.tool_loop, runs=run_store)
