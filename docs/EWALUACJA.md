# Ewaluacja — deterministyczny pomiar poprawności

Husarz długo nie miał **żadnej liczby** opisującej własne działanie. Można było zmienić prompt,
tabelę routingu albo model i nie dowiedzieć się, czy cokolwiek się poprawiło. Warstwa
ewaluacji (Etap 16) zamyka tę lukę od strony, która nie wymaga ani modelu, ani GPU, ani sieci.

## Zasada: mierzymy NASZ kod, nie humor modelu

Weryfikator, który woła model, mierzy jakość **modelu** — i daje inny wynik przy każdym
uruchomieniu. Weryfikator deterministyczny mierzy poprawność **naszego kodu** i daje ten sam
werdykt zawsze. Tylko ten drugi może być bramką w CI, więc od niego zaczynamy.

```bash
husarz eval                      # wszystkie zestawy z config/evals/
husarz eval --set podstawowy     # jeden zestaw
```

Kod wyjścia: `0` = wszystko zdane, `1` = choć jeden przypadek niezdany albo błąd konfiguracji.

## Zestawy

Zestaw to jeden plik `config/evals/<nazwa>.yaml`, wczytywany przez loader razem z resztą
konfiguracji (sekcja `evals`). Literówka w nazwie pola albo w rodzaju przypadku jest **błędem
walidacji przy starcie**, nie cichym pominięciem.

```yaml
name: podstawowy
description: Routing agentów i bramka narzędziowa.
cases:
  - name: kopijnik-na-hermesa
    kind: routing
    agent: kopijnik
    expect_model: hermes
```

## Rodzaje przypadków

### `routing` — czy agent trafi na oczekiwany model

Liczone czystą funkcją (`husarz.router.selection.select_candidates`): bez sieci, bez backendu,
w mikrosekundach. Wychwytuje dokładnie tę klasę wady, którą naprawiał commit o panelu Agenci —
rozjazd między tym, co config **deklaruje**, a tym, co router **faktycznie wybierze**.

| Pole | Znaczenie |
|---|---|
| `agent` | nazwa agenta z `config/agents/` |
| `expect_model` | identyfikator modelu, który MUSI być pierwszym kandydatem |
| `tags` | opcjonalne tagi żądania (wpływają na reguły routingu) |

### `tool_policy` — czy bramka narzędziowa działa

Uruchamia **prawdziwą** pętlę narzędziową ze skryptowanym routerem, który udaje prośbę modelu
o wskazane narzędzie. Sprawdza więc realną ścieżkę autoryzacji (allowlista agenta, ROE, budżet),
a nie samą deklarację w YAML-u. Skryptowany router jest tu konieczny: bramka bezpieczeństwa musi
dawać ten sam werdykt zawsze, a nie zależeć od tego, czy model dziś zechce poprosić o narzędzie.

| Pole | Znaczenie |
|---|---|
| `agent`, `tool`, `action` | kogo i o co „poprosi" skryptowany model |
| `expect` | `allowed` albo `denied` |

!!! warning "Wymaga włączonej pętli narzędziowej"
    Przypadki `tool_policy` działają tylko dla agenta z `tool_loop_enabled: true`. W dostarczonym
    configu pętla jest **wyłączona** (deny-by-default, [ADR-0016](adr/0016-petla-narzedziowa.md)),
    więc taki przypadek zgłosi się jako niezdany z komunikatem „agent ma wyłączoną pętlę
    narzędziową". To celowe: fałszywe „zablokowano" byłoby gorsze niż brak pomiaru.

## Czego ta warstwa jeszcze NIE mierzy

Uczciwie, żeby nie budować złudzeń:

- **Jakości odpowiedzi modelu.** Do tego potrzebny jest przebieg na realnym modelu i miara
  w rodzaju `malformed_ratio` z [`husarz.runs`](https://github.com/Gh0s777tt/Husarz/blob/main/src/husarz/runs/records.py).
  Metryki już zbieramy, brakuje zestawów zadaniowych.
- **Kodu wyjścia z `run_tests`.** Wymaga sandboxa z Dockerem — środowisko docelowe.
- **Sędziego LLM.** Świadomie odłożony: oceny relatywne są nieporównywalne między grupami,
  a skuteczność metody jest udokumentowana dla sędziów znacznie większych niż model, który
  startuje na sprzęcie operatora. Patrz [ADR-0022](adr/0022-zewnetrzne-narzedzia-agentowe.md).

Trend jakości budujemy wyłącznie na weryfikatorach deterministycznych. Sędzia — gdy w ogóle —
będzie służył porównaniom A/B w obrębie jednej grupy, nigdy krzywej w czasie.
