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
| `GET /api/doctor`                        | `diagnostics:read`   | ✅ | ✅ | ❌ |

Rola `user` (zakładana samodzielną rejestracją, patrz [KONTA.md](KONTA.md)) ma
`config:read` i `agent:run` — czyli **nie** ma `diagnostics:read`. To celowe: diagnoza
ujawnia adresy silników i ścieżki katalogów operatora, których `config:read` nie wystawia
(`GET /api/models` podaje backend i tagi, ale nie endpoint), a samo jej wywołanie otwiera
połączenia wychodzące. `viewer` też jej nie ma, ale **z innego powodu, niż pierwotnie zapisano**: argument „podgląd nie wysyła pakietów" przestał obowiązywać wraz z limitem tempa (sufit ruchu jest ten sam bez względu na liczbę uprawnionych ról). Granica stoi dziś na UJAWNIENIU: diagnoza pokazuje adresy silników, ścieżki operatora i katalog silnika — także na ścieżce szczęśliwej.

Bez skonfigurowanego tokenu (tryb dev) uwierzytelnianie jest wyłączone — dopuszczalne
**wyłącznie** dla nasłuchu loopback. Token nigdy nie trafia do configu ani logów —
w konfiguracji jest tylko *referencja* do sekretu (zero hardcode).

## Endpointy

| Metoda i ścieżka          | Opis | Uprawnienie |
|---------------------------|------|-------------|
| `GET /api/health`         | status, wersja, profil | — (otwarte) |
| `GET /api/config/summary` | podsumowanie konfiguracji | `config:read` |
| `GET /api/agents`         | Chorągiew (klasa, model **efektywny** — z `routing.agent_models`, a gdy tam `auto`, z pliku agenta — narzędzia, ROE) | `config:read` |
| `GET /api/models`         | rejestr modeli | `config:read` |
| `GET /api/tools`          | narzędzia (kind, egress) | `config:read` |
| `GET /api/audit?limit=N`  | wpisy audytu + `verified`; `limit` w zakresie `0..10000` (`0` → pusto). Pole `detail` niesie **wąski podzbiór z allowlisty** (`tool.call` → `tool`/`action`/`ok`); pełny szczegół zostaje w dzienniku na dysku | `audit:read` |
| `GET /api/usage`          | monitor: `orchestrations`, `chats`, `failures`, limity kosztów | `config:read` |
| `POST /api/chat`          | `{messages, model?, temperature?, attachments?, images?}` → **bezpośredni czat** z jednym modelem (szybki, konwersacyjny + kodowanie). Model z `models.chat` lub `default` | `agent:run` |
| `POST /api/orchestrate`   | `{task}` → hetman wieloagentowy. Brak routera → 503; błąd routera → 429/502/503 (nie 500) | `agent:run` |
| `POST /api/config/validate` | `{overrides}` → walidacja nadpisań runtime (tylko odczyt) | `config:read` |
| `POST /api/config/runtime`  | `{overrides}` → walidacja + zastosowanie w pamięci; **przebudowuje orkiestrator** (audytowane) | `config:write` |
| `POST /api/auth/register` · `login` · `logout` · `GET /api/auth/me` | konta i sesje — patrz [KONTA.md](KONTA.md) | mieszane |
| `GET/POST/DELETE /api/git/connections` · `…/wizard` · `…/{name}/repos` · `…/{name}/pull-request` | integracje Git — patrz [GIT.md](GIT.md) | `git:*` |
| `GET /api/doctor`         | diagnoza instalacji: modele czatu/orkiestracji/agentów, katalogi zapisu, kolizja portu. `findings[]` (`id`, `state`, `severity`, `description`, `remedy`) + liczniki `blocking`/`warnings`/`unknown`. **Ta sama funkcja, co `husarz doctor`**. Tempo ograniczone (`security.diagnostics.max_requests_per_minute`, domyślnie 6/min) — nadmiarowe żądanie dostaje 429 **przed** odpytaniem silników | `diagnostics:read` |
| `GET /api/secrets/store` | stan zapisywalnego magazynu sekretów: `enabled` + nazwy wpisów i daty (NIGDY wartości ani szyfrogramów) | `git:read` |
| `GET /`                   | konsola WWW (HTML) | — (otwarte) |

Błędy backendu routera są mapowane na kody HTTP: przekroczony limit → `429`,
brak modelu → `503`, awaria wszystkich modeli → `502` (surowa treść błędu nie wycieka).

## Dwa tryby rozmowy

- **Czat bezpośredni** (`POST /api/chat`) — rozmowa z JEDNYM modelem (`models.chat`,
  domyślnie lokalny `husarz-local` z Ollamy). Szybki, konwersacyjny, do kodowania.
  Ciało: `{"messages": [...], "model"?: "...", "temperature"?: 0.3, "attachments"?: [...], "images"?: [...]}`.
  Persona (hetman, PL, kod w blokach) jest zaszyta w modelu — patrz [ollama/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/ollama/README.md).
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
**Monitor** (koszty/tokeny), **Diagnoza** (patrz [LAUNCHER.md](LAUNCHER.md#diagnoza-husarz-doctor)). Bez kroku budowania i **bez zależności z CDN** (airgap-safe:
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

## `POST /api/chat/stream` — odpowiedź strumieniowana (SSE)

Ta sama treść co `POST /api/chat`, ale oddawana fragmentami, w miarę jak model je tworzy.
Uprawnienie, limit konta, sanitacja załączników i obrazów oraz wpis audytu są **identyczne** —
endpoint dzieli z nim kod przygotowania żądania, a nie ma własnej kopii.

```bash
curl -sN -X POST http://127.0.0.1:8000/api/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Cześć"}]}'
```

Odpowiedź to `text/event-stream`:

```
data: {"delta": "Cze"}

data: {"delta": "ść!"}

data: {"done": true, "model": "husarz-local"}
```

Rodzaje zdarzeń: `delta` (fragment treści), `done` (koniec, z użytym modelem), `error`
(strumień przerwany).

### Dlaczego SSE, a nie WebSocket

Plan rozwoju zapowiadał WebSocket. Przy realizacji okazał się złym narzędziem z trzech
niezależnych powodów, więc plan został poprawiony:

1. **Strumień jest jednokierunkowy** (serwer → klient) — dokładnie to, do czego SSE służy.
   WebSocket dokłada kanał zwrotny, którego nikt tu nie potrzebuje.
2. **Kosztowałby szóstą zależność runtime** (`websockets`/`wsproto`) w rdzeniu, który ma ich
   świadomie pięć.
3. **WebSocket nie podlega CORS.** Trzeba by osobno zaprojektować kontrolę `Origin`
   i uwierzytelnianie, bo przeglądarka nie wyśle nagłówka `Authorization` przy otwarciu
   gniazda. SSE idzie zwykłym POST-em i dziedziczy jedno i drugie bez wymyślania od nowa.

### Granica: co jest kodem HTTP, a co zdarzeniem

Wszystko, co da się sprawdzić **przed** rozpoczęciem strumienia, kończy się normalnym kodem
HTTP: brak uprawnienia (401/403), przekroczony limit konta (429), model bez obsługi obrazów
albo odrzucony załącznik (400), brak routera (503).

Po wysłaniu pierwszego bajtu status jest już ustalony i nie da się go zmienić — awaria modelu
może więc zostać zgłoszona **wyłącznie** jako zdarzenie `error`. To ograniczenie protokołu,
nie przeoczenie; dlatego bramki celowo działają wcześniej.

### Fallback kończy się na pierwszym fragmencie

Router ma łańcuch modeli zapasowych, ale przy strumieniu przechodzi na kolejny **tylko
dopóki nic nie wysłał**. Awaria w połowie kończy strumień zdarzeniem `error` — przełączenie
modelu skleiłoby dwie różne odpowiedzi w jedną. Szczegóły: [ROUTER](ROUTER.md).
