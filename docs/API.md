# API i konsola WWW (Etap 5)

REST API rdzenia (FastAPI) + serwowana konsola WWW. Kod: `husarz.api`.
`create_app(config, ...)` buduje aplikację; router modeli i audyt są wstrzykiwalne,
więc API testuje się przez `TestClient` bez serwera i sieci.

## Uruchomienie

```bash
husarz up --profile dev --host 127.0.0.1 --port 8000
```

Otwiera API i konsolę WWW pod `http://127.0.0.1:8000/`. Domyślny nasłuch to loopback.
Nasłuch na adresie **nie-loopback** (np. `0.0.0.0`) **wymaga tokenu API** — inaczej
launcher odmawia startu (kod 2). Token pochodzi z sekretu wskazanego przez
`security.auth.api_token_ref` (patrz niżej).

## Uwierzytelnianie i autoryzacja (RBAC)

Gdy `security.auth.api_token_ref` wskazuje sekret (np. `env:HUSARZ_API_TOKEN` lub
`file:api_token`), API wymaga nagłówka `Authorization: Bearer <token>` na wszystkich
endpointach **poza** `GET /api/health` (sonda liveness). Rola przypisana ważnemu
tokenowi pochodzi z `security.auth.api_role` (domyślnie `operator`), a autoryzacja
opiera się na RBAC (`husarz.security.rbac`):

| Endpoint(y)                              | Wymagane uprawnienie | admin | operator | viewer |
|------------------------------------------|----------------------|:-----:|:--------:|:------:|
| `GET` config/summary, agents, models, tools, usage; `POST` config/validate | `config:read` | ✅ | ✅ | ✅ |
| `GET /api/audit`                         | `audit:read`         | ✅ | ✅ | ✅ |
| `POST /api/orchestrate`                  | `agent:run`          | ✅ | ✅ | ❌ |
| `POST /api/config/runtime`               | `config:write`       | ✅ | ❌ | ❌ |

Bez skonfigurowanego tokenu (tryb dev) uwierzytelnianie jest wyłączone — dopuszczalne
**wyłącznie** dla nasłuchu loopback. Token nigdy nie trafia do configu ani logów —
w konfiguracji jest tylko *referencja* do sekretu (zero hardcode).

## Endpointy

| Metoda i ścieżka          | Opis | Uprawnienie |
|---------------------------|------|-------------|
| `GET /api/health`         | status, wersja, profil | — (otwarte) |
| `GET /api/config/summary` | podsumowanie konfiguracji | `config:read` |
| `GET /api/agents`         | Chorągiew (klasa, model, narzędzia, ROE) | `config:read` |
| `GET /api/models`         | rejestr modeli | `config:read` |
| `GET /api/tools`          | narzędzia (kind, egress) | `config:read` |
| `GET /api/audit?limit=N`  | wpisy audytu + `verified`; `limit` w zakresie `0..10000` (`0` → pusto) | `audit:read` |
| `GET /api/usage`          | monitor: liczba prób (`orchestrations`), `failures`, limity kosztów | `config:read` |
| `POST /api/orchestrate`   | `{task}` → hetman. Brak routera → 503; błąd routera → 429/502/503 (nie 500) | `agent:run` |
| `POST /api/config/validate` | `{overrides}` → walidacja nadpisań runtime (tylko odczyt) | `config:read` |
| `POST /api/config/runtime`  | `{overrides}` → walidacja + zastosowanie w pamięci; **przebudowuje orkiestrator** (audytowane) | `config:write` |
| `GET /`                   | konsola WWW (HTML) | — (otwarte) |

Błędy backendu routera są mapowane na kody HTTP: przekroczony limit → `429`,
brak modelu → `503`, awaria wszystkich modeli → `502` (surowa treść błędu nie wycieka).

## Konsola WWW

Jednoplikowa konsola (`api/static/console.html`, vanilla JS, theme-aware) z zakładkami:
**Czat** (orkiestracja), **Konfiguracja** (podgląd + walidacja nadpisań), **Agenci**,
**Audyt** (status łańcucha), **Monitor** (koszty/tokeny). Bez kroku budowania.
Wszystkie dane z API renderowane w tabelach są **escapowane HTML** (ochrona przed XSS),
a pole „token API" (nagłówek) pozwala korzystać z konsoli przy włączonym uwierzytelnianiu.
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
