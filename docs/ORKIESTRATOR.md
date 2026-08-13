# Rdzeń agentów i orkiestrator „Husarz" (Etap 2)

Ten etap wprowadza agentów Chorągwi i hetmana, który nimi dowodzi. Wszystko
sterowane konfiguracją i promptami; modele wołane przez router (Etap 1). Kod:
`husarz.agents`, `husarz.orchestrator`.

## Agenci

- **`BaseAgent`** — wspólna baza: buduje wiadomości (system = prompt agenta,
  opcjonalny kontekst) i woła router (`agent=<nazwa>` → dobór modelu wg `routing.yaml`).
- **`Towarzysz`** — agent pełny. Po opt-in (`tool_loop_enabled`) orkiestrator deleguje go
  przez pętlę narzędziową (function-calling) — patrz ADR-0016 (Etap 13); inaczej jednokrotny.
- **`Pocztowy`** — lekki podwykonawca (pojedyncze wywołanie, bez narzędzi).
- **`build_agents(config, prompts_dir)`** — ładowarka: dla każdego wpisu
  `config/agents/*.yaml` czyta prompt z `prompts/*.md` i tworzy właściwą klasę
  (wg `agent_class`). Agent wyłączony jest pomijany; brak promptu = czytelny błąd.

Dodanie agenta = nowy plik `config/agents/<nazwa>.yaml` + prompt — bez zmian w rdzeniu.

## Orkiestrator (hetman „Husarz")

Pętla `Orchestrator.run(task)`:

```
plan ─▶ deleguj ─▶ obserwuj ─▶ refleksja ─▶ (ew. dodatkowe kroki) ─▶ synteza
```

1. **Plan** — hetman (model agenta `husarz`) dostaje zadanie + listę dozwolonych
   agentów i zwraca JSON `{"steps": [{"agent", "task"}]}`. Parsowanie jest odporne
   (czysty JSON albo osadzony w prozie; nieparsowalne = pusty plan).
2. **Deleguj + obserwuj** — każdy krok trafia do wskazanego agenta; wynik zapisywany
   jako `Observation`. Krok wskazujący nieznanego/niedozwolonego agenta jest pomijany
   z adnotacją (nie przerywa orkiestracji).
3. **Refleksja** — hetman ocenia obserwacje i zwraca `{"done", "additional_steps"}`.
   Dodatkowe kroki są wykonywane (do `max_extra_rounds`, domyślnie 1).
4. **Synteza** — hetman składa obserwacje w spójną odpowiedź końcową.

`build_orchestrator(config, router, *, prompts_dir)` składa Chorągiew i hetmana z
konfiguracji (`prompts_dir` jest argumentem nazwanym). Izolacja treści niezaufanej
(ogradzanie obserwacji) jest sterowana flagą `security.prompt_injection_filters`.

## Fazy i sterowanie

Każda wiadomość do hetmana zaczyna się znacznikiem fazy (`[FAZA:PLANOWANIE]` itd.),
co czyni fazę jednoznaczną dla modelu i umożliwia deterministyczne testy. Instrukcje
faz (`husarz.orchestrator.prompts`) wymuszają zwięzły, parsowalny format.

## Testowalność (bez sieci)

Agenci i orkiestrator zależą od protokołu `SupportsComplete` (spełnia go `ModelRouter`),
więc testy wstrzykują **skryptowany router** i nie wykonują połączeń sieciowych.
Test e2e pokazuje pełny przepływ wieloagentowy (plan → bielik + kopijnik → refleksja → synteza).

## Przykład

```python
from husarz.config import load_config
from husarz.router import ModelRouter
from husarz.orchestrator import build_orchestrator

config = load_config("./config")
router = ModelRouter(config)  # w produkcji; w testach — router skryptowany/mock
orchestrator = build_orchestrator(config, router, prompts_dir="./prompts")

result = orchestrator.run("Napisz i przetłumacz krótki moduł w Pythonie.")
print(result.answer)
for obs in result.observations:
    print(obs.agent, "→", obs.output)
```

> Uwaga: uruchomienie na realnym routerze wymaga działających endpointów modeli.
> Do pracy bez sieci użyj modeli `backend: mock` lub wstrzyknij skryptowany router.

Decyzje projektowe: [ADR-0004](adr/0004-orkiestrator-agenci.md).
