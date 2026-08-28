"""Wybór modeli-kandydatów dla żądania (strategia po tagach + fallbacki).

Zwraca uporządkowaną, odfiltrowaną (tylko włączone) i pozbawioną duplikatów
listę identyfikatorów modeli do wypróbowania — od preferowanego do ostatniego
fallbacku. Logika jest czysta (bez sieci), więc w pełni testowalna.
"""

from __future__ import annotations

from collections.abc import Callable

from husarz.config.schema import HusarzConfig, ModelSpec, RoutingStrategy


def resolve_agent_model(config: HusarzConfig, agent: str) -> str | None:
    """Zwraca model, którym agent FAKTYCZNIE się posłuży (bez łańcucha fallback).

    Pierwszeństwo ma centralna tabela ``routing.agent_models``; dopiero gdy brak w niej
    wpisu (albo wpis to ``auto``), obowiązuje pole ``model`` z pliku agenta. Reguła żyje
    w jednym miejscu, bo używa jej zarówno router (``select_candidates``), jak i panel
    (``GET /api/agents``) — wcześniej panel czytał wyłącznie plik agenta i po zmianie
    tabeli routingu pokazywał operatorowi nieaktualny model.

    Args:
        config: pełna konfiguracja Husarza.
        agent: nazwa agenta (klucz w ``config.agents``).

    Returns:
        Identyfikator modelu, ``"auto"`` gdy wybór zostawiono routerowi (tagi/domyślny),
        albo ``None`` gdy agent o takiej nazwie nie istnieje.
    """
    mapped = config.routing.agent_models.get(agent)
    if mapped is not None and mapped != "auto":
        return mapped
    agent_cfg = config.agents.get(agent)
    if agent_cfg is None:
        return None
    return agent_cfg.model


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
            assigned = resolve_agent_model(config, agent)
            if assigned is not None and assigned != "auto":
                ordered.append(assigned)

        # (3) reguły routingu dopasowane po tagach.
        # Reguła z pustym match_tags jest pomijana (nie jest łapaczem wszystkiego —
        # inaczej literówka/pominięcie tagów cicho przejęłoby globalny routing).
        if required_tags:
            for rule in routing.rules:
                if rule.match_tags and set(rule.match_tags) <= required_tags:
                    ordered.extend(rule.prefer)
            # (4) modele posiadające wszystkie wymagane tagi, uporządkowane STRATEGIĄ
            pasujace = [
                (model_id, spec)
                for model_id, spec in registry.items()
                if required_tags <= set(spec.tags)
            ]
            ordered.extend(_uporzadkuj_strategia(pasujace, routing.strategy))

        # (5) domyślny model, gdy nic nie wskazano
        if not ordered:
            ordered.append(config.models.default)

    return _expand(ordered, config)


#: Wartość zastępcza dla modelu bez danych — trafia na KONIEC porządku, nigdy na początek.
#: Walidacja krzyżowa nie dopuszcza takiego modelu wśród WŁĄCZONYCH i otagowanych, więc
#: w praktyce dotyczy to wyłącznie modeli wyłączonych (te i tak odpadają w ``_expand``).
#: „Brak danych" nie może jednak wyglądać jak „najtańszy" — to ta sama zasada, co przy
#: diagnozie: nieznane nigdy nie zaokrągla się na korzyść.
_BRAK_DANYCH = float("inf")


def _uporzadkuj_strategia(
    pasujace: list[tuple[str, ModelSpec]], strategy: RoutingStrategy
) -> list[str]:
    """Porządkuje modele dopasowane po TAGACH zgodnie ze strategią routingu.

    **Zakres strategii jest węższy, niż sugeruje jej nazwa, i to jest świadome.** Strategia
    porządkuje WYŁĄCZNIE pulę z punktu (4) — modele pasujące tagami. NIE rusza modelu
    wskazanego wprost, przypisania z ``routing.agent_models`` ani kolejności w
    ``routing.rules[].prefer``, bo to są jawne decyzje operatora. Gdyby ``strategy: cost``
    je nadpisywało, przypisanie agenta do konkretnego modelu przestałoby cokolwiek znaczyć —
    a operator, który je wpisał, ma prawo oczekiwać, że obowiązuje.

    Sortowanie jest STABILNE, więc przy równych kosztach zachowana zostaje kolejność
    rejestru — czyli zachowanie strategii ``tags``.

    Args:
        pasujace: Pary (identyfikator, specyfikacja) w kolejności rejestru.
        strategy: Strategia z ``routing.strategy``.

    Returns:
        Identyfikatory w kolejności wynikającej ze strategii.
    """

    def po_koszcie(para: tuple[str, ModelSpec]) -> float:
        laczny = para[1].koszt_laczny
        return laczny if laczny is not None else _BRAK_DANYCH

    def po_opoznieniu(para: tuple[str, ModelSpec]) -> float:
        opoznienie = para[1].latency_p50_ms
        return float(opoznienie) if opoznienie is not None else _BRAK_DANYCH

    klucze: dict[RoutingStrategy, Callable[[tuple[str, ModelSpec]], float]] = {
        RoutingStrategy.COST: po_koszcie,
        RoutingStrategy.LATENCY: po_opoznieniu,
    }
    klucz = klucze.get(strategy)
    if klucz is None:
        return [model_id for model_id, _ in pasujace]
    return [model_id for model_id, _ in sorted(pasujace, key=klucz)]


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
