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

## Równoległa delegacja kroków planu

`platform.orchestrator.max_parallel_delegations` decyduje, ile kroków planu wykonywać
naraz. Wartość domyślna to **1** — wykonanie sekwencyjne, bit w bit jak przed Etapem 18k.

### Dlaczego to jest bezpieczne

Kroki jednej rundy są **niezależne**: każdy dostaje ten sam `context` — w pierwszej rundzie
`None`, w rundach refleksji podsumowanie policzone RAZ przed pętlą. Żaden krok nie widzi
wyniku innego, więc wykonanie równoległe nie zmienia semantyki.

To nie jest założenie, lecz własność odczytana z kodu i **utrwalona testem**
(`test_kroki_planu_NIE_widza_sie_nawzajem`). Gdyby ktoś zaczął przekazywać krokom wyniki
poprzedników, zrównoleglanie przestałoby być poprawne — i test to zatrzyma.

### Kolejność wyniku pozostaje planowa

Obserwacje wracają w kolejności **kroków planu**, nie zakończenia. To nie jest kosmetyka:
wchodzą one do refleksji i syntezy, a rekord pomiarowy (`husarz.runs`) ma być porównywalny
między przebiegami. Kolejność zależna od wyścigu uczyniłaby oba nieporównywalnymi.

### Dlaczego domyślnie wyłączone

Zrównoleglenie nie zawsze przyspiesza. Kroki planu trafiają często do **tego samego** silnika
lokalnego, a jedna karta graficzna wykona je i tak po kolei — tyle że przy większym zużyciu
pamięci i ryzyku, że model zostanie wyładowany w połowie. Zysk pojawia się dopiero wtedy, gdy
agenci korzystają z **różnych** endpointów.

Włączenie tego bez zrozumienia własnego układu sprzętowego potrafi więc pogorszyć czas
odpowiedzi zamiast go poprawić. Stąd decyzja operatora, nie wartość domyślna.

### Co się zmienia po włączeniu

Przeplot **skutków ubocznych**: wpisy audytu i wywołania narzędzi z różnych kroków mieszają
się ze sobą, więc dziennik czyta się trudniej. Liczniki, na których opierają się limity, są
od Etapu 18k chronione zamkami:

| Licznik | Co ogranicza | Skutek zgubionego przyrostu |
|---|---|---|
| `ToolCallBudget` | amplifikację wywołań narzędzi (spawny kontenerów) | przekroczenie twardego budżetu |
| `UsageMeter` | limit tokenów konta | przepuszczenie żądania ponad przydział |
| `_Tally` | pomiar jakości planu | zafałszowany rekord `husarz.runs` |

Bramka ROE jest niemutowalna po konstrukcji, a dziennik audytu ma własny zamek — obie były
bezpieczne wcześniej.
