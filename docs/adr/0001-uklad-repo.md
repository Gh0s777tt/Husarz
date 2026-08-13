# ADR-0001: Układ repozytorium (src-layout, pakiet `husarz`)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 0

## Kontekst

Specyfikacja wymienia moduły rdzenia jako `core/ orchestrator/ router/ agents/
tools/ memory/ security/ api/`. Trzeba zdecydować, czy będą to katalogi
najwyższego poziomu (płaski układ), czy podpakiety pod jednym pakietem
instalowalnym.

Nazwy takie jak `core`, `api`, `tools`, `security`, `memory` są bardzo popularne
i jako pakiety najwyższego poziomu powodowałyby kolizje importów oraz
zanieczyszczenie globalnej przestrzeni nazw.

## Decyzja

Przyjmujemy **src-layout** z jednym pakietem `husarz`:

```
src/husarz/{config,core,orchestrator,router,agents,tools,memory,security,api,launcher}/
```

- Importy: `from husarz.config import load_config`, `husarz.router`, itd.
- Pakiet oznaczony `py.typed` (dystrybucja typów).
- Testy w `tests/{unit,integration,security}` z `pythonpath=["src"]`.

Moduły wymienione w specyfikacji odpowiadają podpakietom `husarz.*` — mapowanie
1:1, bez utraty zgodności z intencją.

## Konsekwencje

- (+) Czyste, jednoznaczne importy; brak kolizji z popularnymi nazwami.
- (+) Instalowalny pakiet (`pip install -e .`), gotowy pod dystrybucję i CI.
- (+) `src-layout` wymusza testowanie zainstalowanego pakietu, nie przypadkowych
  ścieżek — mniej „działa u mnie".
- (−) Ścieżki o jeden poziom głębsze niż w płaskim układzie (akceptowalne).

## Alternatywy odrzucone

- **Płaski układ** (`core/`, `api/` w korzeniu repo): kolizje nazw, brak czystej
  instalacji jako pakiet, ryzyko importów względnych.
