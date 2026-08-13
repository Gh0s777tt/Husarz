"""Wybór modeli-kandydatów dla żądania (strategia po tagach + fallbacki).

Zwraca uporządkowaną, odfiltrowaną (tylko włączone) i pozbawioną duplikatów
listę identyfikatorów modeli do wypróbowania — od preferowanego do ostatniego
fallbacku. Logika jest czysta (bez sieci), więc w pełni testowalna.
"""

from __future__ import annotations

from husarz.config.schema import HusarzConfig


def select_candidates(
    config: HusarzConfig,
    *,
    agent: str | None = None,
    model: str | None = None,
    tags: list[str] | None = None,
) -> list[str]:
    """Buduje listę modeli do wypróbowania dla danego żądania.

    Kolejność preferencji:
      1. jawnie wskazany ``model`` (jeśli podany),
      2. model domyślny agenta z ``routing.agent_models`` (o ile nie ``auto``),
      3. modele preferowane przez reguły ``routing.rules`` pasujące do ``tags``,
      4. dowolny model posiadający wszystkie wymagane ``tags``,
      5. ``models.default`` jako ostatnia deska ratunku (gdy nic nie wybrano).

    Do każdego wybranego modelu dołączany jest jego łańcuch fallback
    (o ile ``routing.fallbacks_enabled``). Zwracane są wyłącznie modele włączone.
    """
    registry = config.models.registry
    routing = config.routing
    required_tags = set(tags or [])

    ordered: list[str] = []

    if model is not None:
        ordered.append(model)
    else:
        # (2) model przypisany agentowi: najpierw routing.agent_models (tabela centralna),
        #     a gdy brak wpisu (lub 'auto') — pole 'model' z pliku agenta (config/agents/*.yaml).
        if agent is not None:
            mapped = routing.agent_models.get(agent)
            if mapped is not None and mapped != "auto":
                ordered.append(mapped)
            else:
                agent_cfg = config.agents.get(agent)
                if agent_cfg is not None and agent_cfg.model != "auto":
                    ordered.append(agent_cfg.model)

        # (3) reguły routingu dopasowane po tagach.
        # Reguła z pustym match_tags jest pomijana (nie jest łapaczem wszystkiego —
        # inaczej literówka/pominięcie tagów cicho przejęłoby globalny routing).
        if required_tags:
            for rule in routing.rules:
                if rule.match_tags and set(rule.match_tags) <= required_tags:
                    ordered.extend(rule.prefer)
            # (4) modele posiadające wszystkie wymagane tagi
            for model_id, spec in registry.items():
                if required_tags <= set(spec.tags):
                    ordered.append(model_id)

        # (5) domyślny model, gdy nic nie wskazano
        if not ordered:
            ordered.append(config.models.default)

    return _expand(ordered, config)


def _expand(ordered: list[str], config: HusarzConfig) -> list[str]:
    """Rozwija łańcuchy fallback, filtruje do włączonych i usuwa duplikaty.

    Ochronę przed cyklami zapewnia zbiór ``processed`` (każdy model odwiedzamy raz),
    a rejestr jest skończony — rekurencja zawsze się kończy, bez limitu głębokości.
    """
    registry = config.models.registry
    fallbacks_enabled = config.routing.fallbacks_enabled
    result: list[str] = []
    processed: set[str] = set()

    def add(model_id: str) -> None:
        if model_id in processed:
            return
        processed.add(model_id)
        spec = registry.get(model_id)
        if spec is None:
            return
        if spec.enabled and model_id not in result:
            result.append(model_id)
        if fallbacks_enabled:
            for fallback_id in spec.fallback:
                add(fallback_id)

    for model_id in ordered:
        add(model_id)
    return result
