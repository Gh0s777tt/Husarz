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
| `GET /api/usage`          | monitor: `orchestrations`, `chats`, `failures`, limity kosztów | `config:read` |
| `POST /api/chat`          | `{messages, model?, temperature?, attachments?, images?}` → **bezpośredni czat** z jednym modelem (szybki, konwersacyjny + kodowanie). Model z `models.chat` lub `default` | `agent:run` |
| `POST /api/orchestrate`   | `{task}` → hetman wieloagentowy. Brak routera → 503; błąd routera → 429/502/503 (nie 500) | `agent:run` |
| `POST /api/config/validate` | `{overrides}` → walidacja nadpisań runtime (tylko odczyt) | `config:read` |
| `POST /api/config/runtime`  | `{overrides}` → walidacja + zastosowanie w pamięci; **przebudowuje orkiestrator** (audytowane) | `config:write` |
| `POST /api/auth/register` · `login` · `logout` · `GET /api/auth/me` | konta i sesje — patrz [KONTA.md](KONTA.md) | mieszane |
| `GET/POST/DELETE /api/git/connections` · `…/{name}/repos` · `…/{name}/pull-request` | integracje Git — patrz [GIT.md](GIT.md) | `git:*` |
| `GET /`                   | konsola WWW (HTML) | — (otwarte) |

Błędy backendu routera są mapowane na kody HTTP: przekroczony limit → `429`,
brak modelu → `503`, awaria wszystkich modeli → `502` (surowa treść błędu nie wycieka).

## Dwa tryby rozmowy

- **Czat bezpośredni** (`POST /api/chat`) — rozmowa z JEDNYM modelem (`models.chat`,
  domyślnie lokalny `husarz-local` z Ollamy). Szybki, konwersacyjny, do kodowania.
  Ciało: `{"messages": [...], "model"?: "...", "temperature"?: 0.3, "attachments"?: [...], "images"?: [...]}`.
  Persona (hetman, PL, kod w blokach) jest zaszyta w modelu — patrz [ollama/README.md](../ollama/README.md).
  **Załączniki** (`attachments: [{name, content}]`) — pliki/foldery jako kontekst.
  Treść jest NIEZAUFANA: serwer egzekwuje limity (`chat.attachments` — liczba, rozmiar
  per plik/łączny), czyści nazwy (basename), odrzuca dane binarne i **ogradza** blok
  jako dane referencyjne (anty-prompt-injection). Przekroczenie/binaria → `400`.
  **Obrazy** (`images: [{name, data}]`, `data` = base64) — dla modeli **wizyjnych**
  (`models: vision: true`, np. `husarz-vision` z llava/qwen2-vl w Ollamie). Serwer
  rozpoznaje typ z **magic-bytes** (png/jpeg/gif/webp — nie ufa deklarowanemu MIME),
  egzekwuje limity (`chat.images` — liczba, rozmiar per obraz) i przekazuje obraz jako
  część multimodalną (`image_url` z data-URI). Model bez `vision` lub dane nie-obraz →
  `400`. Limit tokenów obejmuje też kontekst.
- **Orkiestracja** (`POST /api/orchestrate`) — pełna pętla wieloagentowa (plan → deleguj
  → synteza) hetmana „Husarz". Cięższa; do złożonych, wieloetapowych zadań.

Konsola (`/`) przełącza się między nimi w zakładce **Czat**.

## Konsola WWW

Jednoplikowa konsola (`api/static/console.html`, vanilla JS, theme-aware) z zakładkami:
**Czat** (dymki, Markdown + bloki kodu z „kopiuj", przełącznik Czat/Orkiestracja),
**Konfiguracja** (podgląd + walidacja nadpisań), **Agenci**, **Audyt** (status łańcucha),
**Monitor** (koszty/tokeny). Bez kroku budowania i **bez zależności z CDN** (airgap-safe:
własny mini-renderer Markdown). Wszystkie dane z API są **escapowane HTML** (ochrona przed
XSS — renderer najpierw escapuje, potem formatuje), a pole „token API" (nagłówek) pozwala
korzystać z konsoli przy włączonym uwierzytelnianiu. Pełny frontend Next.js pozostaje
ścieżką produkcyjną (`web/`).

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
