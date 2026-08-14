# ADR-0019: Wywołanie narzędzi wtyczki MCP (`tools/call`) w pętli agenta

- Status: przyjęty
- Data: 2026-08-14
- Etap: 13b
- Rozszerza: [ADR-0015](0015-konektor-mcp.md) (konektor MCP, dotąd tylko odkrywanie)

## Kontekst

Etap 12b (ADR-0015) dostarczył konektor MCP z **odkrywaniem** (`tools/list`), świadomie
odkładając **wywołanie** (`tools/call`) „razem z pętlą function-calling agenta". Pętla powstała
w Etapie 13 (ADR-0016). Etap 13b domyka wywołanie: agent może realnie użyć zdalnego narzędzia
MCP, przy zachowaniu suwerenności, deny-all egress i deny-by-default.

## Decyzja

### Kind `plugin` = jeden konektor, akcje `list` + `call`

Nowy first-party `kind: plugin` wiąże się z JEDNYM konektorem z `config/plugins/` przez
`config.plugin` (parytet 1:1 z `web`: allowlista L1 per-narzędzie — grant „plugin_x" nadaje
dostęp do jednego serwera, nie wszystkich). Dwie STATYCZNE akcje kodowe:

| akcja | args | bramka | mapuje na |
|---|---|---|---|
| `list` | `{}` | `enabled` | `PluginService.discover` → `tools/list` |
| `call` | `{name, arguments?}` | `enabled` + `allow_call` + `call_allowlist` | `PluginService.call` → `tools/call` |

Nazwa zdalnego narzędzia to **argument** `name` (string koperty JSON-RPC), NIGDY `getattr`
ani klucz rejestru — złośliwe `"../.."` to niegroźny string. Zdalne narzędzia są DANYMI
(z configu + odkrywania), nie kodem: manual (kanał SYSTEM) pokazuje tylko 2 statyczne akcje,
a NIEZAUFANE nazwy poznaje model dopiero przez akcję `list` (wynik ogrodzony jako dane).

### Deny-by-default wielowarstwowo

`allow_call` (master-switch, domyślnie `false`) + `call_allowlist` (jawna enumeracja
dozwolonych narzędzi). `allow_call=true` wymaga niepustej `call_allowlist` (fail-closed:
nie da się wystartować „otwarte"). Odkrywanie ≠ wywołanie: `list` działa bez `allow_call`
(read-only), `call` wymaga obu bram — sprawdzanych PRZED egress (transport nietknięty przy odmowie).

### Airgap egzekwowany na starcie — LOOPBACK (nie tylko „lokalny")

W profilu airgap włączona wtyczka MUSI mieć endpoint **loopback** (`is_loopback_endpoint`),
nie „lokalny" w szerszym sensie. Runtime konektora (`_validate_mcp_endpoint`) i tak przepuszcza
dla hostów spoza allowlisty WYŁĄCZNIE loopback (prywatne IP/`.internal` twardo blokuje), więc
bramka startowa i runtime są SPÓJNE — konfiguracja przechodząca start nie ma „gwarancji porażki"
w runtime, a dane wtyczki w airgap gwarantowanie nie opuszczają hosta.

### Wynik NIEZAUFANY, bez SSRF-by-proxy

`tools/call` zwraca wynik od niezaufanego serwera. Sklejamy wyłącznie bloki `type=text`;
bloki binarne/`resource`/nieznane → placeholder, **ZERO dereferencji** (transport wołany
dokładnie raz — brak SSRF-by-proxy). Tekst przycinany do `max_output_bytes` (UTF-8, config-driven).
Wynik wraca do modelu jako OGRODZONE DANE (`role='user'`), a parser akcji działa tylko na treści
modelu — `[[HUSARZ_ACTION]]` w wyniku NIE jest wykonywane. Błąd protokołu JSON-RPC → `PluginError`;
aplikacyjne `isError` → `ToolResult(ok=False)`.

### Audyt z wykrywalną eksfiltracją

`arguments` to KANAŁ EGRESS (do `max_call_bytes` treści modelu na serwer MCP). Audyt loguje
`{bytes, sha256}` ładunku (jak `web`/`content`) — rozmiar i skrót, bez treści i bez sekretu.
Eksfiltracja jest więc wykrywalna w niemodyfikowalnym dzienniku, a treść nie wycieka.

## Konsekwencje

- (+) Realne użycie narzędzi MCP przez agentów, deny-by-default na każdej warstwie; egress/SSRF
  re-walidowany PER wywołanie (dziedziczone z ADR-0015); token tylko jako referencja, tylko w
  nagłówku `Authorization`; `arguments` przekazywane VERBATIM (modelowy `env:...` NIE rozwiązywany
  — sekret nie jest eksfiltrowany).
- (+) `PluginService` jest bezstanowy i **przebudowywany z bieżącego configu** przez
  `plugin_service_factory` (jak router) przy `POST /api/config/runtime` — dzięki temu zmiana
  polityki konektora (`enabled`/`allow_call`/`call_allowlist`/egress) realnie obowiązuje bez
  restartu (kill-switch nie jest fail-open). `/api/plugins` i pętla czytają ten sam, świeży serwis
  ze stanu (jedno źródło prawdy). Rdzeń dispatchu/pętli bez zmian (open/closed): kind = builder + `ActionSpec`.
- (−) **`call` to pełnoprawny kanał EGRESS i ZDOLNOŚCI, POZA bramką ROE.** `call_allowlist`
  bramkuje KTÓRE narzędzie, egress KĄD, ale nic nie ogranicza CO model wsadzi w `arguments`
  ani co robi zdalne narzędzie. Agent (nie-Puszkarz) z wtyczką na host publiczny + `allow_call`
  może (i) eksfiltrować do `max_call_bytes` kontekstu na wywołanie oraz (ii) uruchomić dowolną
  zdolność serwera — całkowicie poza ROE (która pilnuje tylko Puszkarza). To by-design (opt-in
  operatora: `enabled` + `endpoint` + `allow_call` + `call_allowlist` + egress-allowlist dla hostów
  publicznych), analogiczne do `web`/`shell`. Dla serwera loopback dane nie opuszczają hosta; dla
  hosta publicznego to egress ZA ZGODĄ operatora. Semantyka zdalna jest NIEZAUFANA.
- (−) **TOCTOU DNS-rebinding** (ryzyko rezydualne z ADR-0015): endpoint jest walidowany i
  rozwiązywany, ale IP NIE jest pinowane — transport rozwiązuje ponownie przy połączeniu. Dla
  `call` promień rażenia jest większy niż dla `list` (POST `arguments`). Domknięcie (pinowanie IP,
  łączenie po IP z oryginalnym `Host`) odłożone; do tego czasu ochroną jest blokada prywatnych IP
  po rozwiązaniu nazwy (anty-rebinding do sieci wewnętrznej) i deny-all egress.

## Alternatywy odrzucone

- **Nazwa konektora w `args`** (jeden `plugin` sięga dowolnego serwera): grant traci ziarnistość
  L1 — „plugin" dawałby dostęp do wszystkich serwerów. Wiązanie przez `config.plugin` zachowuje
  parytet z `web`.
- **Sam `allow_call` bez `call_allowlist`**: każde inne narzędzie egress/exec w Husarzu ma
  allowlistę (git subcommand, web domain, shell command) — konektor oferuje DOWOLNE, rosnące
  narzędzia, więc jawna enumeracja jest idiomatyczna.
- **Materializacja każdego zdalnego narzędzia jako osobnej akcji**: wymagałaby wstrzyknięcia
  NIEZAUFANYCH nazw do manuala (kanał SYSTEM) — wektor prompt-injection. Nazwa jako argument
  trzyma je w płaszczyźnie danych.
- **Airgap tylko w runtime**: dopuszczałby na starcie konfiguracje z gwarancją porażki w runtime;
  loopback-gate na starcie daje wczesny, spójny błąd.
