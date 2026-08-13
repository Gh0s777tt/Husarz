# ADR-0002: Hierarchia i walidacja konfiguracji

- Status: przyjęty
- Data: 2026-08-13
- Etap: 0

## Kontekst

Zasada „zero hardcode" wymaga, by wszystko istotne pochodziło z konfiguracji,
walidowanej przy starcie, z czytelnym komunikatem błędu zamiast crasha. Potrzebna
jest jednoznaczna hierarchia nadpisań i mechanizm walidacji krzyżowej.

## Decyzja

### Hierarchia (rosnący priorytet)

```
defaults (Pydantic) -> config/*.yaml -> ENV (HUSARZ_*) -> sekrety -> runtime (panel)
```

- **Scalanie**: głębokie dla map; skalary i listy nadpisywane w całości.
- **ENV**: prefiks `HUSARZ_`, zagnieżdżenie przez `__`, segmenty małymi literami;
  wartości w formacie JSON (`[...]`, `{...}`) są parsowane (listy/obiekty).
  `HUSARZ_CONFIG_DIR` steruje lokalizacją, nie treścią.
- **Runtime**: słownik nadpisań o najwyższym priorytecie (panel/API).
- **Sekrety**: rozwiązywane przez dostawcę (`none`/`env`/`vault`/`sops`) z
  *referencji*; wartości nie trafiają do plików ani logów.

### Walidacja

- Schematy **Pydantic v2** z `extra="forbid"` — literówka = błąd, nie ciche
  zignorowanie.
- Walidatory lokalne (np. telemetria zabroniona, ROE `end>start`, egress
  narzędzia wymaga allowlisty) oraz **walidacja krzyżowa** w `HusarzConfig`
  (referencje modeli/narzędzi, reguły profilu `airgap`).
- Błędy zbierane i formatowane jako czytelny komunikat po polsku
  (`ConfigValidationError`).

### Mapowanie plików

Sekcje jednoplikowe: `husarz.yaml`→`platform`, `models.yaml`→`models` (wymagany),
`routing.yaml`→`routing`, `security.yaml`→`security`. Sekcje wieloplikowe:
`agents/*`, `tools/*`, `roe/*` — kluczowane po polu (`name`/`engagement_id`).

## Konsekwencje

- (+) Jedno źródło prawdy o strukturze; testowalne, przewidywalne nadpisania.
- (+) Bezpieczne domyślne (deny-all, brak telemetrii) wprost z Pydantic.
- (+) Rozszerzanie (nowy agent/narzędzie) bez zmian w kodzie.
- (−) ENV najlepiej nadaje się do skalarów; głębokie edycje rejestru wygodniej
  robić w YAML lub przez runtime (świadome ograniczenie).

## Alternatywy odrzucone

- **`pydantic-settings` jako jedyny mechanizm**: słabo składa wiele plików i
  sekcje wieloplikowe (agents/tools/roe); własny loader daje pełną kontrolę nad
  hierarchią i komunikatami.
- **Brak `extra="forbid"`**: literówki przechodziłyby cicho — niezgodne z
  wymogiem „czytelny błąd, nie crash".
