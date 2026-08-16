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
- **domyślne** `config/` i `prompts/` — binarka startuje bez żadnej konfiguracji
  (nadpisz `--config`/`--prompts`),
- BEZ sekretów i wag modeli.

> **Czym to NIE jest.** „Bez konfiguracji" znaczy: serwer i konsola wstają po dwukliku.
> **Nie** znaczy: czat od razu odpowiada. Binarka nie zawiera ani silnika Ollamy, ani wag —
> bez nich `POST /api/chat` kończy się `502 Backend modelu zawiódł`. Model trzeba raz
> przygotować wg [`../ollama/README.md`](../ollama/README.md); wagi (≈1 GB dla `1.5b`,
> ≈4,7 GB dla `7b`) zostają potem w `~/.ollama` i przeżywają aktualizacje launchera.
>
> **Uwaga na port.** Launcher i vLLM z dostarczonego `config/models.yaml` mają ten sam
> port domyślny 8000. Przy kolizji launcher wypisuje ostrzeżenie przy starcie — zmień
> wtedy `--port` albo endpoint modelu.

## CI (pobieralny artefakt)

`.github/workflows/release.yml` buduje binarki dla Windows/Linux/macOS i publikuje je
jako artefakty przebiegu (do pobrania z zakładki Actions) oraz — dla tagu `v*` —
dołącza do GitHub Release.

## Ograniczenia

- **Podpis kodu** (Windows Authenticode / macOS notarization) i dystrybucja bez
  ostrzeżeń systemu — po stronie operatora (wymaga certyfikatów/tożsamości).
- Realne odpowiedzi AI wymagają lokalnego modelu (Ollama) — patrz `../ollama/README.md`.
- Bogatszy desktop (Tauri, auto-update, ikona w tray) — przyszły krok.
