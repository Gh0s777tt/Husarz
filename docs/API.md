# API i konsola WWW (Etap 5)

REST API rdzenia (FastAPI) + serwowana konsola WWW. Kod: `husarz.api`.
`create_app(config, ...)` buduje aplikację; router modeli i audyt są wstrzykiwalne,
więc API testuje się przez `TestClient` bez serwera i sieci.

## Uruchomienie

```bash
husarz up --profile dev --host 127.0.0.1 --port 8000
```

Otwiera API i konsolę WWW pod `http://127.0.0.1:8000/`. Domyślny nasłuch to loopback.

## Endpointy

| Metoda i ścieżka          | Opis |
|---------------------------|------|
| `GET /api/health`         | status, wersja, profil |
| `GET /api/config/summary` | podsumowanie konfiguracji |
| `GET /api/agents`         | Chorągiew (klasa, model, narzędzia, ROE) |
| `GET /api/models`         | rejestr modeli |
| `GET /api/tools`          | narzędzia (kind, egress) |
| `GET /api/audit?limit=N`  | wpisy audytu + `verified` (łańcuch skrótów) |
| `GET /api/usage`          | monitor: liczba orkiestracji, limity kosztów |
| `POST /api/orchestrate`   | `{task}` → uruchamia hetmana (wymaga routera; inaczej 503) |
| `POST /api/config/validate` | `{overrides}` → walidacja nadpisań runtime |
| `POST /api/config/runtime`  | `{overrides}` → walidacja + zastosowanie w pamięci (audytowane) |
| `GET /`                   | konsola WWW (HTML) |

## Konsola WWW

Jednoplikowa konsola (`api/static/console.html`, vanilla JS, theme-aware) z zakładkami:
**Czat** (orkiestracja), **Konfiguracja** (podgląd + walidacja nadpisań), **Agenci**,
**Audyt** (status łańcucha), **Monitor** (koszty/tokeny). Bez kroku budowania.
Pełny frontend Next.js pozostaje ścieżką produkcyjną (`web/`).

## Programowo

```python
from husarz.config import load_config
from husarz.api import create_app
from husarz.router import ModelRouter

config = load_config("./config")
app = create_app(config, config_dir="./config", router=ModelRouter(config), prompts_dir="./prompts")
# uvicorn.run(app, ...) — albo TestClient(app) w testach
```

Decyzje projektowe: [ADR-0007](adr/0007-api-launcher-web.md).
