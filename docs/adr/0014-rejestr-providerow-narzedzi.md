# ADR-0014: Rejestr providerów narzędzi (open/closed)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 12a

## Kontekst

`tools/loader.build_tools` dobierał implementację narzędzia twardym łańcuchem
`if kind == "web" … elif kind == "shell" …`, a nieznany `kind` kończył się
`ToolError`. Dodanie nowego rodzaju narzędzia wymagało edycji rdzenia dispatchu —
sprzeczne z zasadą „zero hardcode" (nowy komponent = nowy plik/rejestracja, bez
zmian w rdzeniu). Etap 12 (wtyczki) potrzebuje otwartego, ale kontrolowanego
punktu rozszerzeń.

## Decyzja

### `ToolProviderRegistry` jako jedyny punkt rozszerzeń

`tools/registry.py`: `BuildContext` (frozen — komplet wstrzykiwalnych zależności:
`workspace`, `security`, `executor`, `fetcher`, `rag_backend` + `ToolConfig`),
typ `ToolBuilder = Callable[[BuildContext], Tool]` oraz `ToolProviderRegistry`
z `register(kind, builder)` / `get(kind)` / `known_kinds()`. Rejestracja duplikatu
rzuca `ToolError` (blokuje ciche przejęcie wbudowanego rodzaju — determinizm).

### Buildery wbudowane + świeża fabryka `default_registry()`

Sześć wbudowanych rodzajów (`file_edit`, `shell`, `git`, `run_tests`, `web`, `rag`)
wydzielono 1:1 z dawnego `if/elif` do funkcji `BuildContext -> Tool`.
`default_registry()` buduje **świeżą** instancję rejestru z tą szóstką — świadomie
BEZ globalnego mutowalnego singletona, aby re-import/testy nie mogły po cichu
przesłonić wbudowanego rodzaju. `build_tools(..., registry=None)` używa
`default_registry()`, gdy nie wstrzyknięto własnego; nieznany `kind` daje
`ToolError` z IDENTYCZNYM komunikatem jak dotąd (kontrakt zachowany).

### Rozszerzalność WYŁĄCZNIE first-party (bez ładowania obcego kodu)

Rejestr obsługuje tylko providerów wbudowanych. ŚWIADOMIE NIE ładuje zewnętrznych
modułów Pythona przez `entry_points`/`importlib` — import obcego kodu = jego
wykonanie (RCE/łańcuch dostaw), sprzeczne z suwerennością i pakietem frozen
(PyInstaller). Rozszerzalność ZEWNĘTRZNĄ realizuje **data-driven** konektor MCP
(ADR-0015), a nie wtyczki jako kod.

## Konsekwencje

- (+) Nowy rodzaj narzędzia = nowa funkcja-builder + jedna linia `register` w
  `default_registry` (lub wstrzyknięty rejestr) — ZERO zmian w `build_tools`.
- (+) Wstrzykiwalny `registry` = łatwe testy i przyszły seam dla providera `mcp`.
- (+) Refaktor behawioralnie neutralny — cały istniejący pakiet testów narzędzi
  i bezpieczeństwa pozostaje zielony (regresja kontraktu).
- (−) Rejestr jest first-party — brak wtyczek-jako-kod (to celowe: bezpieczeństwo
  ponad wygodę). Zewnętrzne rozszerzenia idą przez konektor MCP (dane, nie kod).

## Alternatywy odrzucone

- **Pozostawić `if/elif`**: łamie „zero hardcode"; każdy nowy rodzaj dotyka rdzenia.
- **Globalny singleton rejestru z auto-rejestracją przy imporcie**: ryzyko cichego
  przesłonięcia rodzaju i zależność od kolejności importów — wybrano świeżą fabrykę.
- **Wtyczki jako pakiety Pythona (entry_points)**: wykonanie obcego kodu —
  odrzucone z powodów bezpieczeństwa/suwerenności.
