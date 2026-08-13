# Pobierany launcher (Etap 10)

Husarz jako **jeden plik do pobrania**, który startuje serwer i otwiera konsolę w
przeglądarce — UX „aplikacji jak Claude", bez instalacji Pythona u użytkownika.

## Uruchomienie z pakietu (dev)

```bash
# Launcher desktopowy: serwer + automatyczne otwarcie konsoli
husarz-app                 # http://127.0.0.1:8000/ otwiera się w przeglądarce
husarz-app --no-open       # bez otwierania (np. zdalnie)
husarz-app --port 8080 --profile dev
```

Albo przez CLI: `husarz up --open`.

## Binarka do pobrania (PyInstaller)

Budowę binarki wykonuje **operator/CI** (nie środowisko dev):

```bash
pip install -e ".[package]"
pyinstaller packaging/husarz.spec --noconfirm
# → dist/husarz-app(.exe) — dwuklik startuje serwer i otwiera konsolę
```

Binarka zawiera rdzeń, konsolę oraz **domyślne** `config/`+`prompts/` (działa
od razu; nadpisz `--config`/`--prompts`). BEZ sekretów i wag. Szczegóły:
[../packaging/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/packaging/README.md).

## CI — pobieralny artefakt

[`.github/workflows/release.yml`](https://github.com/Gh0s777tt/Husarz/blob/main/.github/workflows/release.yml) buduje binarki dla
**Windows/Linux/macOS** i publikuje jako artefakty przebiegu (zakładka Actions);
dla tagu `v*` dołącza je do GitHub Release. Uruchom ręcznie (`workflow_dispatch`)
lub przez push tagu wersji.

## Zachowanie

- Domyślny nasłuch **loopback** (`127.0.0.1`) — bezpieczny, lokalny.
- Przeglądarka otwierana tylko dla loopbacku, po krótkiej zwłoce (serwer zdąży wstać);
  błąd otwarcia (headless/brak DISPLAY) nie wywraca serwera.
- Uwierzytelnianie/konta/Git działają jak w `husarz up` (ta sama logika i bramki).

## Ograniczenia

- **Podpis kodu** (Windows Authenticode / macOS notarization) — po stronie operatora
  (certyfikaty/tożsamość); bez tego systemy mogą ostrzegać przy pobraniu.
- Realne odpowiedzi AI wymagają lokalnego modelu (Ollama) — [../ollama/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/ollama/README.md).
- Bogatszy desktop (Tauri, auto-update, tray, ikona) — przyszły krok.

Decyzje: [ADR-0012](adr/0012-pobierany-launcher.md).
