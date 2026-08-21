# Współtworzenie Husarza

Dziękujemy za wkład. Ten dokument opisuje zasady pracy nad projektem.

## Zasady naczelne

- **Zero hardcode.** Adresy, klucze, nazwy modeli, ścieżki i polityki — tylko
  w konfiguracji, nigdy w kodzie.
- **Komentarze i dokumentacja po polsku; identyfikatory w kodzie po angielsku.**
- **Małe, weryfikowalne kroki.** Po każdym kroku uruchamiaj testy.
- **Bezpieczeństwo domyślnie.** Nie osłabiaj niezmienników (deny-all egress,
  sandbox bez sieci, brak telemetrii, sekrety poza repo).

## Środowisko deweloperskie

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pre_commit install   # hooki: gitleaks, ruff, black
```

## Bramki jakości (muszą przejść przed PR)

Z aktywnego venv — `.venv\Scripts\python.exe` na Windows, `.venv/bin/python` na Linuksie
i macOS:

```bash
python -m ruff check .
python -m black --check src tests scripts
python -m mypy
python -m pytest
python -m husarz.launcher.cli eval --config ./config --prompts ./prompts
python -m mkdocs build --strict
gitleaks protect --staged
```

Dwie pozycje bywają pomijane, a są równoprawnymi bramkami: `husarz eval` sprawdza niezmienniki
routingu i bramki narzędziowej (bez modelu i sieci), a `mkdocs --strict` wychwytuje martwe
odnośniki, czyli rozjazd dokumentacji.

## Definicja „ukończone"

- Kod otypowany (mypy `strict` czysty), sformatowany (black), bez uwag ruff.
- Testy: unit + integration + (dla powierzchni bezpieczeństwa) security — zielone.
- Brak sekretów (gitleaks czysty).
- Dokumentacja zaktualizowana i **zweryfikowana** (README/CHANGELOG/ROADMAP/docs);
  przykłady i polecenia z dokumentów faktycznie działają.
- Istotne decyzje udokumentowane jako ADR (`docs/adr/NNNN-*.md`).
- **Nośność testów sprawdzona**: cofnij poprawkę i upewnij się, że nowy test się czerwieni.
  Test przechodzący bez poprawki nie chroni niczego, a daje fałszywe poczucie bezpieczeństwa.
- **Weryfikacja skutku, nie deklaracji**: gdy środowisko pozwala, sprawdź obserwowalny efekt,
  a nie samą konfigurację. Test na `argv` jest uzupełnieniem, nie dowodem dla warstwy
  bezpieczeństwa — szczegóły w [SECURITY.md](SECURITY.md).
- Gdy zmiana dotyka **konsoli WWW** — zrzuty w dokumentacji odświeżone (patrz niżej).

## Odświeżanie zrzutów ekranu

Dokumentacja i wiki mają pokazywać realnie działającą aplikację, więc zrzuty w
`docs/assets/screenshots/` odświeżamy razem ze zmianą UI — nie „kiedyś potem".
Robi to skrypt operatora `scripts/screenshots.py` (wymaga `pip install playwright`
i zainstalowanego Google Chrome; nie jest zależnością projektu ani częścią CI):

```bash
python -m husarz.launcher.cli up --host 127.0.0.1 --port 8000   # terminal 1
python scripts/screenshots.py --base-url http://127.0.0.1:8000  # terminal 2
```

Zakładka **Czat** wymaga działającego modelu — skrypt zadaje pytanie i czeka na pełną
odpowiedź, więc uruchamiaj go przy podniesionej Ollamie (patrz README, „Lokalny czat").

> **Zanim zacommitujesz — obejrzyj każdy plik.** Repozytorium jest publiczne, a UI potrafi
> pokazać ścieżki, adresy, nazwy kont czy fragmenty tokenów. Zrzuty rób wyłącznie na
> instancji z danymi demonstracyjnymi; skrypt tego za Ciebie nie oceni.

## Konwencje commitów

Format [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(config): dodaj walidację krzyżową referencji narzędzi
fix(loader): popraw scalanie list z ENV
docs(readme): zaktualizuj sekcję szybkiego startu
test(security): dodaj niezmiennik deny-all egress
```

Zakresy (scope) zwykle: `config`, `router`, `agents`, `tools`, `security`,
`api`, `launcher`, `web`, `docs`, `ci`.

## Dodawanie agenta / narzędzia

- **Agent:** nowy plik `config/agents/<nazwa>.yaml` + prompt `prompts/<nazwa>.md`.
  Bez zmian w rdzeniu. Zaktualizuj `docs/AGENCI.md`.
- **Narzędzie:** nowy plik `config/tools/<nazwa>.yaml` (allowlisty, sandbox,
  egress). Implementacja adaptera w `src/husarz/tools/` (Etap 3).

## Zgłaszanie podatności

Patrz [SECURITY.md](SECURITY.md) — zgłaszaj prywatnie, nie przez publiczne issue.
