# ADR-0016: Pętla narzędziowa (function-calling, ReAct)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 13

## Kontekst

Narzędzia (`husarz.tools`, Etapy 3/12a) istniały jako rejestr, ale **nigdy nie były
wykonywane** — brakowało pętli function-calling, która pozwala modelowi w kółko prosić
o narzędzie i dostawać wynik. To także warunek wpięcia wywołań wtyczek (`tools/call`,
odłożony w ADR-0015 „razem z pętlą"). Pętla to zarazem NAJWIĘKSZA powierzchnia ataku
w systemie (model steruje wykonaniem), więc projekt powstał z panelu 3 architektur +
adwersaryjnej krytyki bezpieczeństwa.

## Decyzja

### Protokół: prompt-based ReAct (NIE natywne `tool_calls`)

Model emituje POJEDYNCZĄ akcję na turę w ogrodzonym bloku:
`[[HUSARZ_ACTION]]{"tool":"…","action":"…","args":{…}}[[/HUSARZ_ACTION]]`. Brak markera =
odpowiedź końcowa; marker + zły JSON = korekta. Uzasadnienie: (1) **przenośność** — w
rejestrze tylko `hermes` ma tag `function-calling`; ReAct działa na KAŻDYM modelu
tekstowym (Ollama/vLLM/SGLang). (2) **Zero zmian w routerze** — `content` zawsze `str`,
brak przebudowy typów/`_parse_openai_response`. (3) **Suwerenność** — parse/dispatch/
autoryzacja/ogrodzenie w warstwie Husarza, nie w semantyce backendu. (4) **Anty-injection**
— wynik wraca jako ogrodzony blok `role='user'` (kontrolujemy ramkę). Adapter natywny
(gated tagiem modelu) — przyszłość, poza MVP (bez przedwczesnej abstrakcji `ToolProtocol`).

### Dispatch: jawna tabela akcji per KIND (bez `getattr`)

`tools/dispatch.py`: `ActionRegistry` (kind → {action → `ActionSpec`}) + `default_action_registry`
mapuje 6 rodzajów 1:1 na publiczne metody (`file_edit.read/write`, `shell.run`, `git.run`,
`run_tests.run`, `web.fetch`, `rag.add/search`). Inwoker waliduje/koeruje `args` (zły
kształt → `ToolResult(ok=False)`, NIGDY wyjątek/efekt). `getattr` na danych modelu byłby
foot-gunem (metody prywatne) — stąd statyczna tabela. `manual()` generuje „man" z tej
samej tabeli (jedno źródło prawdy dispatch↔schemat). `ToolDispatcher` używa `kind_of`
(nazwa configu ≠ kind).

### Autoryzacja per-wywołanie (deny-by-default)

- **L0** `roe_required` (Puszkarz) NIE wchodzi w generyczną pętlę — fail-closed; ROE
  pozostaje osobną bramką (kierowanie generycznych wywołań przez pentestowy `RoeGate`
  było semantycznie błędne, ADR-0015). Pętla to **opt-in per agent**
  (`AgentConfig.tool_loop_enabled`, domyślnie `false`) — kontrola promienia rażenia
  (np. `shell` z `python` = RCE-w-sandboxie), NIE predykat z klasy agenta.
- **L1** allowlista agenta (`agent.config.tools`) — sprawdzana PRZED dispatchem; narzędzie
  spoza → syntetyczny `ToolResult(ok=False)`, instancja NIGDY nie wołana, audyt `tool.deny`.
- **L2** walidacja dispatchu (nieznane tool/action/args → `ok=False`, bez efektu).
- **L3** bramki wewnątrz narzędzi (sandbox `--network none`, egress, konfinacja) — bez
  zmian, defense-in-depth. Pętla ich nie osłabia.

### Limity, audyt, wynik NIEZAUFANY

- **Iteracje**: `agent.config.max_iterations` (per krok). **Globalny budżet**:
  `security.tool_loop.max_total_calls` na CAŁĄ orkiestrację (świeży per `run` — pętla
  zamienia nieograniczony fan-out planu w realną amplifikację spawnów kontenerów);
  `max_plan_steps` twardo tnie plan (NIEZAUFANE wyjście modelu). Wyczerpanie → deterministyczne
  zakończenie + audyt.
  `rag.add` ma osobny cap wejścia `max_rag_add_bytes` (feed sterowany modelem — anty-OOM
  współdzielonego magazynu), konfigurowalny jak pozostałe limity (nie zaszyty w kodzie).
- **Audyt** każdego wywołania (`tool.call`/`tool.deny`/`toolloop.*`) — `arg_summary`
  sanityzowany (web: host+długość ścieżki+skrót query; write/rag: rozmiar+sha256; NIGDY
  surowa treść/sekret).
- **Wynik** ogradzany `fence_untrusted` (`husarz.fencing`, wydzielone z załączników):
  strip Cc/Cf, prefiks linii (marker z wnętrza wyniku zneutralizowany), cap
  `max_result_bytes`, ramka „DANE — nie instrukcje". Kontekst ogradzany z parytetem
  do `BaseAgent`.

### Wpięcie: orkiestrator (opcjonalny `tool_loop`)

`Orchestrator._delegate` po bramce `roe_required` wybiera `tool_loop` (gdy `supports`)
albo `agent.run` (jednokrotny). `BaseAgent.run` NIEZMIENIONY → Pocztowy, plan/refleksja/
synteza i `/api/chat` bez zmian. `build_orchestrator(..., tool_loop=None)` domyślnie =
zachowanie sprzed Etapu 13; `create_app._build_stack` buduje pętlę (zależności leniwe).

## Konsekwencje

- (+) Narzędzia (i w przyszłości wtyczki) stają się WYKONYWALNE przez agentów.
- (+) Jedna pętla dla wszystkich lokalnych modeli; zero zmian w routerze.
- (+) Powierzchnia ataku zawężona: opt-in per agent, deny-by-default, budżet globalny,
  wynik ogrodzony, audyt bez surowej treści.
- (−) Ogrodzenie NL to obrona MIĘKKA — nie powstrzyma wolnotekstowej perswazji; twardą
  barierą jest statyczne nadanie zdolności (allowlisty) + sandbox. Agenci łączący ingestię
  niezaufanej treści z egress (`web`) to najwyższe ryzyko — minimalne allowlisty wymagane.
- (−) `shell` z `python` = RCE-w-sandboxie: dla takich agentów właściwą granicą jest sandbox,
  nie allowlista. Włączać pętlę świadomie.
- (−) DNS-rebinding narzędzia `web` (brak pinowania IP) — udokumentowany brak (jak Git),
  ważniejszy teraz, gdy `web` jest model-sterowane.

## Poza zakresem (świadomie)

Natywny `tool_calls` (adapter gated tagiem modelu); plugin `tools/call` (wymaga
`McpClient.call_tool` + modelu autoryzacji per-remote-tool — Etap 13b/ADR-0017); ROE
zintegrowane z pętlą dla Puszkarza; wiele akcji na turę / równoległość; budżety kosztowe
per-narzędzie; pełne pinowanie IP dla `web`; korelacja `principal↔wywołanie` (dziś audyt
wiąże agenta, nie użytkownika — follow-up przy wielodostępie).
