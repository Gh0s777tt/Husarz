# Wtyczki (konektory MCP)

Wtyczki to sposób na **rozszerzenie Husarza o zewnętrzne narzędzia BEZ ładowania
obcego kodu**. Zamiast instalować pakiety-wtyczki (co oznaczałoby wykonanie cudzego
kodu — RCE/łańcuch dostaw), Husarz łączy się z lokalnym **serwerem narzędzi MCP**
przez HTTP JSON-RPC. To podejście *data-driven*: konektor opisujesz w konfiguracji,
a rdzeń pozostaje nienaruszony.

> Dwie warstwy rozszerzalności:
> - **Rejestr providerów narzędzi** (wbudowane rodzaje: `web`, `shell`, …) — patrz
>   [NARZEDZIA.md](NARZEDZIA.md) i ADR-0014. Wyłącznie *first-party*.
> - **Wtyczki/konektory MCP** (ten dokument, ADR-0015) — rozszerzalność *zewnętrzna*.

## Model bezpieczeństwa (suwerenność ponad wygodę)

- **Egress deny-all + anty-SSRF.** Endpoint lokalny (loopback: `127.0.0.1`, `::1`,
  `localhost`) jest dozwolony — lokalny serwer MCP to główny przypadek. Host **publiczny**
  wymaga `https` **oraz** wpisu w `security.egress.allowlist`. Adresy wewnętrzne i
  metadanych (`169.254.169.254`, `10.x`, `192.168.x`, `172.16–31.x`, `0.0.0.0`,
  multicast, a także zapis IPv4-mapped `::ffff:…`) są **twardo blokowane**. Dla nazwy
  domenowej host jest **rozwiązywany**, a każdy zwrócony adres sprawdzony wobec bloku
  wewnętrznego (anty-DNS-rebinding); nierozwiązywalna nazwa → odmowa (fail-closed).
  Pełne pinowanie IP (okno TOCTOU) pozostaje do zrobienia, jak w narzędziu `web`.
- **Token wyłącznie jako referencja** (`env:` / `file:` / `vault:` / `sops:`), nigdy
  wartość. Rozwiązywany leniwie przy operacji; nigdy nie trafia do configu na dysku,
  logów, audytu ani odpowiedzi API. Brak/nierozwiązywalny token przy ustawionym
  `token_ref` → **fail-closed** (błąd, nie ciche nieuwierzytelnione połączenie).
- **Wynik NIEZAUFANY.** Odpowiedź serwera jest cięta twardym limitem `max_output_bytes`
  (podczas odczytu — ochrona OOM), a w konsoli renderowana wyłącznie z escapowaniem.
- **Audyt.** Każda próba odkrycia (`plugin.discover`) trafia do niemodyfikowalnego
  dziennika **przed** wyjściem na sieć — bez tokenu, nagłówków i treści.
- **RBAC.** Odczyt/odkrywanie wymaga uprawnienia `plugin:read` (rola `operator`+).
- **Deny-by-default.** Przykładowy konektor jest `enabled: false`; gdy żadna wtyczka
  nie jest włączona, API `/api/plugins*` odpowiada `404`.

## Konfiguracja

Nowy konektor = nowy plik `config/plugins/<nazwa>.yaml` (bez zmian w rdzeniu):

```yaml
name: local-mcp
transport: http                 # MVP: tylko HTTP JSON-RPC (stdio odłożone)
description: Lokalny serwer narzędzi MCP.
enabled: true
endpoint: http://127.0.0.1:8808/mcp    # loopback dozwolony; publiczny → https + allowlist
token_ref: env:HUSARZ_MCP_TOKEN        # REFERENCJA do sekretu (nie sama wartość); opcjonalny
timeout_seconds: 30
max_output_bytes: 1000000              # twardy limit odpowiedzi (DoS/OOM)
```

## API

| Endpoint | Uprawnienie | Opis |
|---|---|---|
| `GET /api/plugins` | `plugin:read` | Lista konektorów (bez sekretu — tylko `token_ref`). |
| `GET /api/plugins/{name}/tools` | `plugin:read` | Odkrywa narzędzia serwera (`tools/list`). |

Kody błędów: nieznana wtyczka → `404`; wyłączona → `409`; odmowa egress/SSRF → `403`;
nierozwiązywalny `token_ref` (lokalna konfiguracja) → `500`; odmowa/awaria serwera
(autoryzacja/transport) → `502` (komunikat generyczny, bez wnętrzności).

Konsola (`/`) ma zakładkę **Wtyczki**: lista konektorów + przycisk „sprawdź narzędzia".

## Zakres MVP i granice

**W zakresie:** rejestr providerów (open/closed), konektor MCP nad wstrzykiwalnym
transportem, **odkrywanie** narzędzi (`tools/list`), bramki egress/RBAC/audyt, token
jako referencja, limit odpowiedzi.

**Poza zakresem (świadomie):** **wywoływanie** narzędzi (`tools/call`) — wchodzi razem
z pętlą function-calling agenta (wtedy z autoryzacją per-wywołanie); transport `stdio`
(spawnowanie procesów, wymaga sandboxa); ładowanie wtyczek jako kodu Pythona
(`entry_points`); pełny handshake MCP (`initialize`, streaming/SSE, `resources`).
Pełne **pinowanie IP** (domknięcie okna TOCTOU rebindingu) — rozwiązanie nazwy już
blokuje trywialny rebinding, ale pinowanie połączenia do zwalidowanego adresu pozostaje
odłożone (ten sam brak co w narzędziu `web`).
