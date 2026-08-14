# ADR-0020: Pinowanie IP — domknięcie okna TOCTOU DNS-rebindingu

- Status: przyjęty
- Data: 2026-08-14
- Etap: 15 (rozszerzony w 15b o `husarz.git`, w 15c o embedder pamięci i router modeli)
- Domyka ograniczenia z: [ADR-0011](0011-integracje-git.md) (integracje Git),
  [ADR-0015](0015-konektor-mcp.md) (konektor MCP),
  [ADR-0016](0016-petla-narzedziowa.md) (narzędzie `web` w pętli),
  [ADR-0019](0019-wywolanie-mcp-tools-call.md) (`tools/call`)

## Kontekst

Trzy wcześniejsze ADR-y zapisały to samo ryzyko rezydualne: **DNS-rebinding**. Bramki
anty-SSRF sprawdzały host (a od Etapu 12b także adresy, na które rozwiązuje się nazwa),
ale samo połączenie wykonywał `httpx` po **nazwie** — czyli rozwiązywał ją PONOWNIE.

To klasyczne okno TOCTOU (*time-of-check → time-of-use*):

```
t0  walidacja:  resolve("mcp.vendor.com") -> 93.184.216.34   (publiczny → OK)
t1  ... (TTL rekordu wygasa; atakujący kontrolujący strefę podmienia rekord)
t2  połączenie: resolve("mcp.vendor.com") -> 169.254.169.254 (metadane chmury!)
```

Sprawdziliśmy jeden adres, a połączyliśmy się z innym. Atakujący nie musi łamać allowlisty —
wystarczy, że kontroluje DNS dla domeny, którą operator już dopuścił (albo zatruje resolver).
Skutki: odczyt metadanych chmury (poświadczenia IAM), skan i dostęp do usług w sieci
wewnętrznej, a dla wtyczki MCP — wysłanie **tokenu Bearer** do niepowołanego endpointu.

Dodatkowo logika klasyfikacji hostów była **zduplikowana** w trzech miejscach
(`tools/web.py`, `plugins/client.py`, `git/client.py`) z różną polaryzacją i różnym
zakresem — czyli z gwarancją, że kolejna poprawka trafi tylko do jednej kopii.

## Decyzja

### 1. Jeden moduł: `husarz.ssrf`

Klasyfikacja hostów i pinowanie żyją w jednym module bez zależności od `httpx`
(czysty stdlib → w pełni testowalny offline). Ścieżki wychodzące parametryzują go **dwiema
flagami polityki** — cała reszta logiki jest wspólna:

| Ścieżka | `allow_loopback` | `allow_lan` | Uzasadnienie |
|---|---|---|---|
| narzędzie `web` | `False` | `False` | model steruje URL-em — nie może sięgnąć ani usług operatora, ani jego LAN |
| konektor MCP | `True` | `False` | lokalny serwer wtyczki to GŁÓWNY przypadek; LAN nie jest potrzebny |
| integracje Git | `False` | `True` | Git nigdy nie jest usługą lokalną Husarza, ale samodzielnie hostowany GitLab pod RFC 1918 to podstawowy scenariusz suwerenności |
| embedder pamięci | `True` | `True` | serwer embeddingów to z założenia własny model operatora (domyślnie `127.0.0.1:11434`) |
| router modeli | `True` | `True` | endpointy modeli to własna infrastruktura (vLLM/Ollama/SGLang), lokalna lub w LAN |

Dla dwóch ostatnich ścieżek pin nie ogranicza legalnego użycia (loopback i LAN przechodzą),
ale nadal blokuje metadane chmury i zakresy infrastrukturalne — czyli miejsca, w których
wylądowałby **klucz API modelu** albo **wektor embeddingu** (odwracalny do PII), gdyby nazwa
endpointu została przejęta lub przez pomyłkę wskazała `169.254.169.254`.

`allow_lan` obejmuje WYŁĄCZNIE wąską listę `_LAN_NETWORKS` (RFC 1918 + ULA). Świadomie
**nie** realizujemy go przez `ipaddress.is_private`: ta właściwość obejmuje także loopback,
link-local (metadane chmury) i zakresy testowe, więc „przepuść prywatne" odblokowałoby
dokładnie to, co ma pozostać zamknięte.

### 2. Rozwiąż RAZ → sprawdź KAŻDY adres → PRZYPNIJ

```
resolve(host) -> [a1, a2, ...]        # dokładnie jedno zapytanie DNS
for a in adresy: blokada(a) -> odmowa # NIE „odfiltruj i weź czysty"
pin = adresy[0]                       # transport łączy się z literałem
```

Transport dostaje `PinnedTarget`, a nie URL — pin jest **częścią kontraktu**, więc
implementacja nie może go pominąć i rozwiązać nazwy po raz drugi. Drugiego rozwiązania
DNS po prostu nie ma, więc okna TOCTOU nie ma czego otwierać.

### 3. Połączenie po IP, tożsamość po nazwie

Łączenie się z literałem IP naiwnie **złamałoby TLS** (certyfikat wystawiony na nazwę,
weryfikacja wobec IP → błąd lub, gorzej, pokusa wyłączenia `verify`). Dlatego
`PinnedTarget` niesie trzy pola:

| Pole | Rola |
|---|---|
| `connect_url` | dokąd otwieramy gniazdo (literał IP, IPv6 w nawiasach) |
| `host_header` | nagłówek `Host` — oryginalna nazwa (routing na serwerze, vhosty) |
| `sni_hostname` | SNI **oraz** weryfikacja certyfikatu (`server_hostname` w `start_tls`) |

`sni_hostname` jedzie do `httpx` jako `extensions={"sni_hostname": ...}`; `httpcore`
używa go jako `server_hostname`, więc certyfikat jest weryfikowany wobec **nazwy**.
`verify=True` pozostaje włączone jawnie i na sztywno. Pinowanie **zawęża** powierzchnię
ataku i **nie** osłabia TLS.

### 4. Fail-closed w każdym rozgałęzieniu

| Sytuacja | Reakcja |
|---|---|
| pusta lista adresów (brak/awaria DNS) | `EgressError` |
| jakikolwiek adres wewnętrzny (także w mieszanych A/AAAA) | `EgressError` |
| resolver zwraca coś, co nie parsuje się jako IP | `EgressError` |
| URL bez hosta | `EgressError` |

Świadomie **nie** wybieramy „czystego" adresu z odpowiedzi zawierającej adres wewnętrzny:
odpowiedź, która zawiera `169.254.169.254`, jest odpowiedzią zatrutą w całości.

### 5. Kolejność bram: odmowa nie kosztuje nawet zapytania DNS

```
schemat/userinfo → literał wewnętrzny → loopback → https → allowlista egress → DNS + pin
```

Host odrzucony przez allowlistę egress nie trafia do resolvera (brak wycieku nazwy
do zewnętrznego DNS), a odmowa nigdy nie dotyka transportu.

### 6. Własna lista sieci deny — właściwości `ipaddress` nie wystarczają

Adwersaryjny przegląd wykazał, że oparcie klasyfikacji WYŁĄCZNIE na `is_private |
is_link_local | is_reserved | is_multicast | is_unspecified` zostawia realne przejścia:

| Sieć | Dlaczego groźna | Co mówi stdlib |
|---|---|---|
| `100.64.0.0/10` | CGNAT — endpoint metadanych Alibaba Cloud, typowe pule węzłów k8s/EKS | **nie** jest prywatna |
| `fec0::/10` | IPv6 site-local (deprecated, wciąż spotykane w LAN) | **nie** jest prywatna |
| `2002::/16`, `2001::/32`, `64:ff9b::/96` | 6to4 / Teredo / NAT64 — osadzają adres IPv4, więc `2002:a9fe:a9fe::1` prowadzi do `169.254.169.254` | zależnie od wersji |
| `198.18.0.0/15`, klasa E, TEST-NET | zakresy infrastrukturalne/testowe | prywatne dopiero od 3.11.9, a `requires-python = ">=3.11"` |

Dlatego `_EXTRA_BLOCKED_NETWORKS` jest **jawną, wersjonowaną** częścią bramki, a nie
delegacją do stdlib. Świadomie nie używamy `not ip.is_global`: ta właściwość zmieniała
znaczenie między wydaniami CPythona, a bramka bezpieczeństwa nie może zależeć od patcha
interpretera.

### 7. `*.localhost` wymaga dowodu, nie deklaracji

RFC 6761 jedynie **zaleca** mapowanie `*.localhost` na loopback. `getaddrinfo` przez glibc
(bez systemd-resolved) potrafi wysłać taką nazwę do zwykłego DNS — a przy skonfigurowanej
domenie przeszukiwania nawet do strefy atakującego. Nazwa `mcp.localhost` uznana za loopback
„po sufiksie" omijałaby pin, wymóg `https` **i** allowlistę egress. Dlatego tylko dokładnie
`localhost` oraz literały IP idą wprost; `*.localhost` jest rozwiązywane i **każdy** adres
musi być loopbackiem.

### 8. `trust_env=False` — środowisko nie może przekierować przypiętego połączenia

Domyślne `httpx.Client(trust_env=True)` honoruje `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`
(i `SSLKEYLOGFILE`). Zmienna środowiskowa przekierowałaby połączenie do przypiętego IP
przez cudzy serwer — czyli obeszłaby **całą** tę warstwę oraz deny-all egress, i to bez
śladu w konfiguracji. Egress Husarza ma pochodzić z configu, nie ze środowiska procesu.

### 9. Nazwa publiczna NIE może rozwiązać się na loopback

Adresy rozwiązanej nazwy klasyfikujemy z `allow_loopback=False` **niezależnie** od ścieżki.
Bez tego zatruty DNS przekierowałby połączenie (z tokenem Bearer wtyczki) do usługi
działającej na maszynie operatora. Intencjonalny loopback konfiguruje się literałem
`127.0.0.1` albo nazwą `localhost` — te idą osobną gałęzią, bez DNS.

## Konsekwencje

- (+) Okno TOCTOU DNS-rebindingu **zamknięte** dla wszystkich trzech ścieżek wychodzących
  (`web`, wtyczki MCP, Git) — ryzyko rezydualne ciągnące się od ADR-0011/0015/0016/0019
  jest domknięte, nie tylko udokumentowane.
- (+) Jedna implementacja zamiast trzech kopii; nowa ścieżka wychodząca = jedno wywołanie
  `build_pinned_target`.
- (+) Domknięta luka w `web`: loopback **przez nazwę** (`http://localhost:.../admin`) był
  wcześniej blokowany tylko jako literał IP — nazwa przechodziła przez `is_local_endpoint`.
- (+) `HttpxFetcher` czyta ciało strumieniowo, chunkami po 64 KiB, z twardym sufitem
  i deadline'em wall-clock (dotąd pobierał całą odpowiedź, dopiero potem przycinał —
  ryzyko OOM i „slow-drip"; bez `chunk_size` pojedyncza iteracja mogła oddać cały
  zdekompresowany blok gzip i przekroczyć limit o rzędy wielkości).
- (+) Komunikaty odmowy nie zawierają rozwiązanego adresu — wynik narzędzia wraca do modelu,
  więc „rozwiązuje się na 10.0.0.7" byłoby skanerem sieci wewnętrznej przez błędy.
- (−) **Zmiana kontraktu**: `Fetcher`, `PluginTransport` i `McpClient` przyjmują
  `PinnedTarget` zamiast `str`. Świadoma: opcjonalny pin byłby fail-open (implementacja
  mogłaby go po cichu zignorować). Dotyczy tylko kodu first-party i testów.
- (−) **Zmiana zachowania**: publiczna nazwa rozwiązująca się na loopback jest teraz
  odrzucana. Lokalne serwery MCP należy adresować `127.0.0.1`/`localhost` (tak robi
  `config/plugins/example-mcp.yaml`).
- (−) Pin dotyczy **jednego** adresu — brak automatycznego przejścia na kolejny rekord A
  przy awarii. Świadome: „spróbuj następnego" wymagałoby drugiego wyboru w czasie
  połączenia, czyli odtworzenia okna, które właśnie zamykamy.
- (+) `git/client.py` (ADR-0011) korzysta z tej warstwy od Etapu 15b — poprzednia
  walidacja sprawdzała tylko LITERAŁY i nie rozwiązywała nazw, więc ścieżka niosąca token
  z prawem zapisu do repozytoriów nie miała żadnej ochrony przed rebindingiem.
- (−) `allow_lan` to świadome ryzyko rezydualne: operator, który wpuści na allowlistę
  egress domenę kontrolowaną przez atakującego, może zostać przekierowany w obręb własnej
  sieci (nie do metadanych chmury). Barierą pozostaje jawna allowlista i to, że `api_base`
  pochodzi z konfiguracji, nie od modelu.

## Rozważone alternatywy

**Własny resolver wstrzyknięty w httpx/httpcore.** Odrzucone: wymaga wejścia w prywatne
API `httpcore` (`connect_tcp`), więc łamie się przy każdej zmianie wersji, i przenosi
decyzję bezpieczeństwa do warstwy, której nie testujemy offline.

**Sprawdzenie adresu po nawiązaniu połączenia (`getpeername`).** Odrzucone: kontrola
następuje, gdy gniazdo do adresu wewnętrznego już zostało otwarte — sam handshake TLS
z usługą wewnętrzną bywa wystarczającym oddziaływaniem, a SSRF „ślepy" nadal działa.

**Cache DNS z wymuszonym minimalnym TTL.** Odrzucone: zmniejsza okno, nie zamyka go;
dokłada stan globalny i zatruwalny cache — czyli nową powierzchnię ataku.

**Poleganie wyłącznie na warstwie sieci (NetworkPolicy/deny-all).** Nie odrzucone —
to warstwa **komplementarna** (Etap 6). Nie zastępuje kontroli aplikacyjnej: nie działa
w trybie desktopowym (launcher bez klastra) i nie odróżnia legalnego egressu do
allowlistowanej domeny od egressu do metadanych.

## Do zrobienia (świadomie poza zakresem)

- ~~`husarz.git` na wspólnej warstwie~~ — **zrealizowane** (Etap 15b): Git korzysta z tej
  samej warstwy z `allow_lan=True`; poprzednia walidacja nie rozwiązywała nazw wcale.
- ~~Pinowanie dla routera modeli~~ i embeddera pamięci — **zrealizowane** (Etap 15c).
  Wszystkie pięć ścieżek wychodzących jest teraz na jednej warstwie.
- Wsparcie dla przekierowań (obecnie `follow_redirects=False` — przekierowanie omijałoby
  walidację i pin; ewentualna obsługa musiałaby przypinać KAŻDY skok osobno).
