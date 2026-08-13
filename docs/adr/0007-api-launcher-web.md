# ADR-0007: API rdzenia, launcher i konsola WWW

- Status: przyjęty
- Data: 2026-08-13
- Etap: 5

## Kontekst

Etap 5 dodaje interfejs: REST API rdzenia, launcher `husarz up` oraz UI (czat,
panel konfiguracji, audyt, monitor). Wymogi: testowalność bez serwera/sieci,
brak ciężkiego builda frontendu w środowisku dev (brak pełnego łańcucha Node w CI
jednostkowym), spójność z zasadą „zero hardcode".

## Decyzja

### REST API na FastAPI, `create_app(config, ...)`

Aplikacja jest **funkcją fabryki** z wstrzykiwalnym routerem, audytem i katalogiem
promptów. Dzięki temu testuje się ją przez `TestClient` w procesie — bez uruchamiania
serwera i bez sieci. Endpointy: health, config/summary, agents, models, tools,
audit (z `verify`), usage, orchestrate, config/validate, config/runtime.

### Konsola WWW jako samodzielny plik statyczny (MVP)

Zamiast pełnej aplikacji Next.js (wymaga builda Node) dostarczamy **jednoplikową
konsolę** (`api/static/console.html`, vanilla JS) serwowaną przez API pod `/`.
Zapewnia czat, podgląd/edycję konfiguracji (walidacja nadpisań), listę agentów,
audyt i monitor — bez kroku budowania, w pełni testowalna (API zwraca HTML).
Next.js pozostaje ścieżką produkcyjną na przyszłość (katalog `web/`).

### Launcher `husarz up`

`up` ładuje konfigurację (wymuszając wybrany profil jako nadpisanie), buduje
`create_app` z `ModelRouter` i uruchamia `uvicorn`. Importy FastAPI/uvicorn są
**leniwe** — `validate`/`version` nie ciągną tych zależności. Domyślny nasłuch to
loopback (`127.0.0.1`).

### Edycja konfiguracji z panelu = walidacja nadpisań

`POST /api/config/validate` waliduje nadpisania runtime przez ten sam loader
(`load_config(..., runtime_overrides=...)`) i zwraca podsumowanie lub czytelny błąd.
`POST /api/config/runtime` dodatkowo stosuje je w pamięci, **przebudowuje orkiestrator**
(przez wstrzykiwalną fabrykę — inaczej `/api/orchestrate` działałby na starej
konfiguracji) i audytuje zmianę.

### Uwierzytelnianie Bearer + RBAC (poprawka z przeglądu Etapu 5)

Pierwotny szkielet API nie miał uwierzytelniania. Po adwersaryjnym przeglądzie:
token API (Bearer) rozwiązywany jest z **sekretu** (`security.auth.api_token_ref`,
np. `env:`/`file:`), a autoryzacja opiera się na istniejącym `husarz.security.rbac`
(rola z `security.auth.api_role`). Uwierzytelnianie jest wstrzykiwalne do
`create_app` (`api_token`, `api_role`, `rbac`), więc pełna macierz ról testuje się
przez `TestClient`. Fail-closed: launcher odmawia nasłuchu poza loopbackiem bez tokenu.
`TrustedHostMiddleware` (dla loopbacku) ogranicza wektor DNS-rebindingu.

### Odporność i spójność wykonania (poprawki z przeglądu)

- Błędy routera w `/api/orchestrate` mapowane na kody HTTP (`429`/`502`/`503`),
  nie gołe `500`; surowa treść błędu nie wycieka do klienta ani audytu.
- Licznik `usage.orchestrations` liczy **próby** (spójnie z audytem, który zapisuje
  wpis przed uruchomieniem) + osobny `failures`; inkrementy pod `Lock`.
- `AuditLog.record` serializowany `Lock`-iem — endpointy FastAPI biegną w puli
  wątków, więc read-modify-write łańcucha skrótów musi być atomowe (inaczej fałszywy
  alarm manipulacji w `verify`).
- `GET /api/audit?limit` walidowany (`0..10000`); `limit=0` → pusta lista, nie „wszystko".

## Konsekwencje

- (+) API i UI w 100% testowalne bez sieci/serwera/Node.
- (+) Router/audyt wstrzykiwalne — te same komponenty co w rdzeniu.
- (+) Panel egzekwuje walidację schematem przed zastosowaniem nadpisań.
- (+) Uwierzytelnianie + RBAC bez sekretów w configu; fail-closed dla ekspozycji sieciowej.
- (−) Konsola to MVP (vanilla JS), nie pełny Next.js — bogatszy frontend to przyszłość.
- (−) `uvicorn.run` jest blokujący, więc sam serwing nie jest testem jednostkowym
  (testujemy fabrykę aplikacji i wiring parsera).
- (−) OIDC (pełny przepływ tożsamości) wciąż odłożony — token Bearer + RBAC to
  pomost do czasu wpięcia OIDC (Etap 6).

## Alternatywy odrzucone

- **Pełny Next.js już teraz**: build Node w CI jednostkowym, cięższe testy — odłożone.
- **API bez wstrzykiwania routera**: wymagałoby żywych modeli w testach (sieć).
