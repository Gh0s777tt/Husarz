# ADR-0015: Konektor wtyczek MCP (data-driven, discover-only w MVP)

- Status: przyjęty (rozszerzony przez [ADR-0019](0019-wywolanie-mcp-tools-call.md) — `tools/call`)
- Data: 2026-08-13
- Etap: 12b

## Kontekst

Wymóg użytkownika: „pluginy". Rozszerzalność zewnętrzna nie może jednak oznaczać
ładowania obcego kodu (import = wykonanie → RCE/łańcuch dostaw), co jest sprzeczne z
suwerennością i pakietem frozen. Potrzebny jest mechanizm *data-driven*: Husarz łączy
się z zewnętrznym serwerem narzędzi (protokół MCP) opisanym w konfiguracji, pod tymi
samymi bramkami bezpieczeństwa co reszta platformy.

Projekt powstał z panelu 3 architektur (synteza + adwersaryjna krytyka bezpieczeństwa).
Krytyka wycięła z MVP ścieżkę **wywołania** narzędzia (`tools/call`) — nie ma dla niej
konsumenta (brak pętli function-calling), a jej usunięcie kasuje naraz kolizję ROE,
egzekucję allowlisty narzędzi i DoS wynikiem.

## Decyzja

### Pakiet `husarz.plugins` jako lustro `husarz.git`

`errors`/`models`/`client`/`service`/`builder` — ten sam sprawdzony wzorzec co
integracja Git (Etap 9): transport HTTP **wstrzykiwalny** (testy bez sieci), token jako
referencja rozwiązywana leniwie, egress-gate na endpoint, fasada `PluginService`.
Konektory są **statyczne** (źródło prawdy: `config/plugins/*.yaml`) — bez mutowalnego
magazynu (inaczej niż GitService).

### Nowa sekcja `config/plugins/*.yaml` (nie rozszerzenie `config/tools`)

Konektor to zasób bezpieczeństwa z tokenem, transportem i powierzchnią egress — kształt
`GitConnection`, który repo świadomie modeluje jako osobną sekcję, nie jako `ToolConfig`.
`token_ref` jest **typowanym** polem z walidatorem referencji; wciśnięcie go w
`ToolConfig.config: dict[str, Any]` ominęłoby „każdy config walidowany schematem".
Dołożenie sekcji to jedna linia w `_MULTI_DIRS` (jak `agents`/`tools`/`roe`).

### Anty-SSRF z ODWRÓCONĄ polaryzacją względem Git

`_validate_mcp_endpoint`: brak userinfo → **twardy blok** adresów wewnętrznych/metadanych
(prywatne, link-local, zarezerwowane, multicast, unspecified; z rozwinięciem
**IPv4-mapped IPv6** `::ffff:…`) → **loopback dozwolony** (lokalny serwer MCP; `http` OK,
ruch nie opuszcza hosta) → host publiczny wymaga `https` **+** allowlisty egress. Dla
**nazwy domenowej** dodatkowo rozwiązujemy host (wstrzykiwalny resolver) i sprawdzamy
**KAŻDY** zwrócony adres wobec bloku wewnętrznego — nazwa wskazująca metadane/adres
wewnętrzny jest blokowana mimo wpisu w allowliście (anty-DNS-rebinding; nierozwiązywalna
nazwa → fail-closed). Walidacja powtarzana przy każdym połączeniu.

### Discover-only + twarde bramki

MVP: `tools/list` (odkrywanie, read-only), za `plugin:read`, audytowane przed wyjściem.
Token opcjonalny; gdy `token_ref` ustawiony a nierozwiązywalny → `PluginSecretError`
(lokalna konfiguracja → HTTP 500, odróżnione od zdalnej odmowy `PluginAuthError` → 502),
fail-closed przed wyjściem na sieć. Odpowiedź traktowana jako NIEZAUFANA: transport tnie
ciało twardym sufitem `max_output_bytes` **podczas** odczytu, egzekwuje **bezwzględny
deadline wall-clock** (anty-„slow-drip"), `httpx.stream` z `follow_redirects=False` i
**jawnym** `verify=True`; błędy transportu mapowane na **generyczne** `502` (bez URL/
wnętrzności). Wpisy `security.egress.allowlist` walidowane (bez pustych/kształtu URL).

## Konsekwencje

- (+) Rozszerzalność zewnętrzna bez wykonywania obcego kodu (dane, nie kod).
- (+) Spójność z integracją Git (jeden wzorzec: transport wstrzykiwalny, token-ref,
  egress-gate) → w pełni testowalne offline (`FakePluginTransport`).
- (+) Powierzchnia ataku zawężona do odkrywania; brak wywołań = brak kolizji ROE i DoS
  wykonania w MVP.
- (−) Bez wywoływania narzędzi konektor jeszcze nie „działa" dla agenta — to celowa
  granica: `tools/call` wchodzi z pętlą function-calling (i autoryzacją per-wywołanie).
- (−) ~~Rozwiązanie nazwy blokuje trywialny DNS-rebinding, ale bez **pinowania IP** okno
  TOCTOU (zmiana rekordu między walidacją a połączeniem) pozostaje.~~
  **DOMKNIĘTE w Etapie 15** — [ADR-0020](0020-pinowanie-ip-anty-ssrf.md): nazwa rozwiązywana
  raz, adres przypinany, `Host`/SNI po nazwie.

## Alternatywy odrzucone

- **`kind: mcp` w `config/tools`**: przeciąża `ToolConfig` nietypowanym tokenem w
  `config: dict`, nadaje `allowlist` trzecie znaczenie, łamie precedens (Git = osobna sekcja).
- **Wtyczki jako pakiety Pythona (`entry_points`)**: wykonanie obcego kodu — odrzucone.
- **`tools/call` w MVP**: brak konsumenta; kierowanie wywołań przez pentestowy `RoeGate`
  było semantycznie błędne (loopback poza zakresem ROE) — odłożone z pętlą agenta.
- **`config: dict` escape-hatch w `PluginConfig`**: kanał przemytu nagłówków/`verify=false`/
  surowego tokenu — usunięty; TLS `verify=True` zakodowane na sztywno.
- **Per-wtyczkowa `allowlist`**: zwalidowana-lecz-nieegzekwowana pułapka — polegamy na
  globalnej `security.egress.allowlist` (spójnie z Git).
