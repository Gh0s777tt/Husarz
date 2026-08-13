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
`POST /api/config/runtime` dodatkowo stosuje je w pamięci i audytuje zmianę.

## Konsekwencje

- (+) API i UI w 100% testowalne bez sieci/serwera/Node.
- (+) Router/audyt wstrzykiwalne — te same komponenty co w rdzeniu.
- (+) Panel egzekwuje walidację schematem przed zastosowaniem nadpisań.
- (−) Konsola to MVP (vanilla JS), nie pełny Next.js — bogatszy frontend to przyszłość.
- (−) `uvicorn.run` jest blokujący, więc sam serwing nie jest testem jednostkowym
  (testujemy fabrykę aplikacji i wiring parsera).

## Alternatywy odrzucone

- **Pełny Next.js już teraz**: build Node w CI jednostkowym, cięższe testy — odłożone.
- **API bez wstrzykiwania routera**: wymagałoby żywych modeli w testach (sieć).
