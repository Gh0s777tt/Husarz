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

```bash
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m black --check src tests
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
```

## Definicja „ukończone"

- Kod otypowany (mypy `strict` czysty), sformatowany (black), bez uwag ruff.
- Testy: unit + integration + (dla powierzchni bezpieczeństwa) security — zielone.
- Brak sekretów (gitleaks czysty).
- Dokumentacja zaktualizowana i **zweryfikowana** (README/CHANGELOG/ROADMAP/docs);
  przykłady i polecenia z dokumentów faktycznie działają.
- Istotne decyzje udokumentowane jako ADR (`docs/adr/NNNN-*.md`).

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
