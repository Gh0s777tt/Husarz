# packaging/ — pobierany launcher (PyInstaller)

Budowa jednego pliku wykonywalnego `husarz-app`, który startuje serwer i otwiera
konsolę w przeglądarce (UX „aplikacji do pobrania"). Binarkę buduje **operator/CI**
(z zależnościami projektu), nie środowisko dev.

## Budowa lokalna

```bash
# 1) Zależności + narzędzie pakujące
pip install -e ".[package]"

# 2) Zbuduj binarkę (Windows/Linux/macOS — natywnie na danym systemie)
pyinstaller packaging/husarz.spec --noconfirm

# 3) Uruchom
dist/husarz-app          # Linux/macOS
dist\husarz-app.exe      # Windows
```

Uruchomienie startuje serwer na `127.0.0.1:8000` i otwiera konsolę w przeglądarce.
Flagi: `--host`, `--port`, `--profile`, `--config`, `--prompts`, `--no-open`.

## Co zawiera binarka

- Rdzeń `husarz` + konsola (`api/static/console.html`),
- **domyślne** `config/` i `prompts/` (działa out-of-the-box; nadpisz `--config`/`--prompts`),
- BEZ sekretów i wag modeli.

## CI (pobieralny artefakt)

`.github/workflows/release.yml` buduje binarki dla Windows/Linux/macOS i publikuje je
jako artefakty przebiegu (do pobrania z zakładki Actions) oraz — dla tagu `v*` —
dołącza do GitHub Release.

## Ograniczenia

- **Podpis kodu** (Windows Authenticode / macOS notarization) i dystrybucja bez
  ostrzeżeń systemu — po stronie operatora (wymaga certyfikatów/tożsamości).
- Realne odpowiedzi AI wymagają lokalnego modelu (Ollama) — patrz `../ollama/README.md`.
- Bogatszy desktop (Tauri, auto-update, ikona w tray) — przyszły krok.
