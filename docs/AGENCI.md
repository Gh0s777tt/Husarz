# Agenci — Chorągiew

Roster agentów Husarza (motyw husarski). Każdy agent to plik
`config/agents/<nazwa>.yaml` + prompt systemowy `prompts/<nazwa>.md`.
Dodanie/wymiana agenta nie wymaga zmian w rdzeniu.

## Klasy agentów

- **Towarzysz** — agent pełny (własny model, narzędzia, pętla rozumowania).
- **Pocztowy** — lekki podwykonawca (wąskie, tanie zadania).

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

Model per agent ustawia `config/routing.yaml -> agent_models` (można też podać
`model` bezpośrednio w pliku agenta, lub `auto` — wtedy decyduje router).

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
| `max_iterations`| int            | Limit iteracji pętli agenta. |
| `roe_required`  | bool           | `true` wymusza aktywne ROE (Puszkarz). |
| `enabled`       | bool           | Czy agent aktywny. |
| `params`        | dict           | Parametry dodatkowe. |

## Dodanie nowego agenta

1. Utwórz `config/agents/<nazwa>.yaml` (pola jak wyżej).
2. Utwórz prompt `prompts/<nazwa>.md`.
3. (Opcjonalnie) dodaj wpis w `routing.agent_models`.
4. Zwaliduj: `husarz validate`. Zaktualizuj tę tabelę i CHANGELOG.
