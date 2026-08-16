# Agenci — Chorągiew

Roster agentów Husarza (motyw husarski). Każdy agent to plik
`config/agents/<nazwa>.yaml` + prompt systemowy `prompts/<nazwa>.md`.
Dodanie/wymiana agenta nie wymaga zmian w rdzeniu.

## Klasy agentów

- **Towarzysz** — agent pełny (własny model, narzędzia, pętla rozumowania).
- **Pocztowy** — lekki podwykonawca (wąskie, tanie zadania).

Implementacja (Etap 2): `husarz.agents` — `BaseAgent`, `Towarzysz`, `Pocztowy`,
`build_agents(config, prompts_dir)`. Agenci są dobierani do modelu przez router
(`agent=<nazwa>`), a hetmanem dowodzi orkiestrator — patrz [ORKIESTRATOR.md](ORKIESTRATOR.md).

**Pętla narzędziowa (Etap 13, ADR-0016):** agent wykonuje narzędzia w pętli ReAct po
jawnym opt-in `tool_loop_enabled: true` (domyślnie `false` — deny-by-default). Wtedy
`Orchestrator` deleguje go przez `ToolLoop` (autoryzacja per-wywołanie, audyt, budżet),
a nie jako pojedyncze wywołanie. Agent `roe_required` (Puszkarz) NIE wchodzi w pętlę.
Uwaga: `shell` z `python` na allowliście = wykonanie dowolnego kodu w sandboxie — włączaj
pętlę świadomie, z minimalną allowlistą. Patrz [NARZEDZIA.md](NARZEDZIA.md), [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).

## Roster

| Agent      | Klasa     | Model (domyślny) | Narzędzia                         | ROE |
|------------|-----------|------------------|-----------------------------------|-----|
| Husarz     | towarzysz | `glm-main`       | —                                 | nie |
| Bielik     | towarzysz | `bielik`         | `rag`                             | nie |
| Kopijnik   | towarzysz | `hermes`         | `file_edit, shell, git, run_tests`| nie |
| Zwiadowca  | towarzysz | `hermes`         | `web, rag`                        | nie |
| Puszkarz   | towarzysz | `hermes`         | `shell, web`                      | **tak** |
| Kanclerz   | towarzysz | `glm-main`       | `file_edit, git`                  | nie |
| Chorąży    | pocztowy  | `hermes`         | —                                 | nie |

Model per agent (kolejność priorytetu): `config/routing.yaml -> agent_models`
(tabela centralna) → pole `model` w pliku agenta (gdy nie `auto`) → reguły po
tagach → `models.default`. Ustaw `model: auto`, by całość zostawić routerowi.

Dwa pierwsze kroki tej reguły liczy `husarz.router.selection.resolve_agent_model` —
korzysta z niej zarówno router, jak i `GET /api/agents`, więc kolumna *Model*
w zakładce **Agenci** pokazuje model **efektywny** (ten, którego agent faktycznie
użyje), a nie samą deklarację z pliku agenta. Tabela wyżej odzwierciedla
dostarczony szablon, w którym oba źródła są zgodne.

## Role

- **Husarz (hetman, orkiestrator).** Dekomponuje zadanie, dobiera agentów,
  deleguje, obserwuje, koryguje plan (refleksja), syntetyzuje odpowiedź.
  Zadania po polsku kieruje do Bielika.
- **Bielik.** Język i zadania polskie: pisanie, redakcja, tłumaczenia, analiza.
- **Kopijnik.** Inżynieria oprogramowania: edycja plików, shell (sandbox), git,
  testy. Małe, weryfikowalne kroki.
- **Zwiadowca.** Research: web (tylko allowlist domen), dokumentacja, RAG.
  Treści niezaufane trzyma w izolacji.
- **Puszkarz.** Bezpieczeństwo — **wyłącznie autoryzowany pentest** w granicach
  podpisanego ROE. Domyślnie dry-run. Nie tworzy exploitów; proponuje działania
  defensywne. Szczegóły: [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).
- **Kanclerz.** Dokumentacja: README, CHANGELOG, ROADMAP, ADR, raporty —
  utrzymywane w spójności z kodem.
- **Chorąży.** Router/planner pomocniczy: klasyfikacja intencji, wstępny plan,
  kontrola kosztów. Odciąża orkiestratora tanimi decyzjami.

## Pola konfiguracji agenta

| Pole            | Typ            | Opis |
|-----------------|----------------|------|
| `name`          | str            | Unikalna nazwa (klucz agenta). |
| `display_name`  | str?           | Nazwa wyświetlana. |
| `agent_class`   | enum           | `towarzysz` \| `pocztowy`. |
| `role`          | str            | Krótki opis roli. |
| `model`         | str            | Id modelu z rejestru lub `auto`. |
| `prompt_file`   | str            | Plik promptu w `prompts/`. |
| `tools`         | list[str]      | Allowlista narzędzi (muszą istnieć w `config/tools/`). |
| `max_iterations`| int            | Limit iteracji pętli narzędziowej (per krok). |
| `tool_loop_enabled` | bool       | Opt-in na pętlę narzędziową (domyślnie `false`). |
| `roe_required`  | bool           | `true` wymusza aktywne ROE (Puszkarz). |
| `enabled`       | bool           | Czy agent aktywny. |
| `params`        | dict           | Parametry dodatkowe. |

## Dodanie nowego agenta

1. Utwórz `config/agents/<nazwa>.yaml` (pola jak wyżej).
2. Utwórz prompt `prompts/<nazwa>.md`.
3. (Opcjonalnie) dodaj wpis w `routing.agent_models`.
4. Zwaliduj: `husarz validate`. Zaktualizuj tę tabelę i CHANGELOG.
