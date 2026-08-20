# ADR-0022: Zewnętrzne narzędzia agentowe — kryteria przyjęcia i dwa odrzucenia

- Status: przyjęty
- Data: 2026-08-20
- Etap: przekrojowy (dotyczy każdej przyszłej decyzji „czy wziąć narzędzie X")
- Powiązania: [ADR-0016](0016-petla-narzedziowa.md) (pętla narzędziowa, deny-by-default),
  [ADR-0020](0020-pinowanie-ip-anty-ssrf.md) (pinowanie IP), `docs/BEZPIECZENSTWO.md`

## Kontekst

Ekosystem narzędzi agentowych rośnie szybciej, niż da się go rzetelnie ocenić. Operator
przyniósł do oceny dwa projekty o dużej popularności — **OpenPipe ART** (framework treningu
RL dla agentów) i **OmniRoute** (bramka do katalogu dostawców LLM). Oba są na licencjach
zgodnych z naszą, oba mają realną społeczność, oba rozwiązują problemy brzmiące jak nasze.

Pojedyncza odpowiedź „nie" na każde takie pytanie jest bezwartościowa, bo za pół roku
pytanie wraca od zera przy kolejnym narzędziu. Potrzebujemy **reguły**, którą da się
zastosować powtarzalnie, i zapisu, co konkretnie wykazała ocena tych dwóch — żeby nie
powtarzać tej samej pracy.

## Decyzja

Przyjmujemy **pięć kryteriów odrzucenia**. Naruszenie choćby jednego wystarcza, by nie
przyjmować narzędzia jako zależności rdzenia ani jako procesu w naszym wdrożeniu:

1. **Wymusza cofnięcie deklaracji platformy.** Podniesienie minimalnej wersji Pythona,
   dołożenie runtime'u (Node, JVM), zmiana modelu konfiguracji. Ogon nie merda psem.
2. **Wprowadza niepoliczalną powierzchnię wyjścia.** Komponent, który sam nawiązuje
   połączenia poza naszą bramką egress, unieważnia jedyne pytanie definiujące ten projekt:
   *dokąd poszły dane*. Dotyczy to również procesu za loopbackiem — nasza bramka widzi wtedy
   `127.0.0.1` i milczy o reszcie trasy.
3. **Wymaga sieci albo uprawnień w miejscu, gdzie deklarujemy ich brak.** Instalacja
   wykonująca kod i pobierająca binaria, auto-update, instalacja własnego CA do magazynu
   zaufania systemu — każde z osobna wyklucza profil `airgap`.
4. **Niesie ryzyko regulaminowe lub prawne.** Obchodzenie zabezpieczeń antybotowych,
   podszywanie się pod odciski TLS, zautomatyzowany dostęp do konsumenckich interfejsów
   webowych. Projekt publikowany publicznie nie może mieć takiej zależności w drzewie.
5. **Nieproporcjonalny dług utrzymaniowy.** Rdzeń Husarza ma **pięć** zależności runtime.
   Każda kolejna musi obronić się wartością, której nie da się uzyskać stukilkudziesięcioma
   liniami własnego kodu.

Osobno przyjmujemy **regułę pozytywną**: pomysł wolno przejąć zawsze, gdy licencja na to
pozwala. Przepisanie logiki po swojemu w Pythonie, z naszymi typami i testami, nie jest
marnotrawstwem — to jedyny sposób, by nowa funkcja odziedziczyła nasze bramki (egress,
`UsageMeter`, audyt, RBAC) zamiast je omijać.

## Ocena: OpenPipe ART — odrzucone jako zależność

[github.com/openpipe/ART](https://github.com/openpipe/ART), Apache-2.0. Framework treningu
RL: uruchamia agenta N razy, zbiera trajektorie, nagradza je i metodą GRPO dotrenowuje
adapter LoRA na własnym serwerze vLLM.

Zweryfikowane fakty rozstrzygające:

| Ustalenie | Kryterium |
|---|---|
| `requires-python >= 3.12`; Husarz deklaruje `>= 3.11` | 1 |
| Zależności **bazowe** obejmują `anthropic`, `openai`, `litellm` i `weave` (śledzenie W&B) | 2, 5 |
| RULER woła sędziego przez `litellm`, omijając nasz `Router`, bramkę egress, `UsageMeter` i audyt | 2 |
| Jedyna działająca alternatywa sprzętowa (`ServerlessBackend`) wymaga `WANDB_API_KEY` i wysyła pełne trajektorie na zewnątrz | 2, 3 |
| 13 zależności bazowych rozwija się do dziesiątek pakietów wobec naszych 5 | 5 |

Odrzucenie nie zależy od sprzętu operatora, ale warto odnotować: stos treningowy wymaga
CUDA (Unsloth), a operator pracuje na Apple Silicon oraz na maszynie z udokumentowanym
bugiem sterownika, przez który nawet **inferencja** modelu 7B nie mieści się w całości na GPU.

**Do przejęcia jako pomysł:** relatywne, grupowe ocenianie N przebiegów tego samego zadania
jednym promptem, oraz zasada, że twardy sygnał deterministyczny **nigdy** nie jest nadpisywany
oceną sędziego LLM.

**Czego świadomie nie przejmujemy:** formatu trajektorii ART. Kusi obietnicą „darmowej opcji
na trening później", ale jest pusta — GRPO uczy się z logprobów bieżącej polityki, a nasz
`ChatResponse` niesie czysty tekst (`grep -rn logprob src/` → zero trafień).

## Ocena: OmniRoute — odrzucone jako zależność i jako proces

[github.com/diegosouzapw/OmniRoute](https://github.com/diegosouzapw/OmniRoute), MIT.
Bramka agregująca setki dostawców LLM za jednym endpointem.

Zweryfikowane fakty rozstrzygające (gałąź domyślna `release/v3.8.50`):

| Ustalenie | Kryterium |
|---|---|
| Brak pól `main`/`module`/`exports`/`types` — to **aplikacja Next.js**, nie biblioteka. Da się ją tylko uruchomić | 1 |
| 79 zależności runtime, Node ≥ 22, stan runtime wyłącznie w SQLite (konfiguracji nie da się wersjonować w repo) | 1, 5 |
| `postinstall` wykonuje kod i potrafi pobierać prekompilowane binaria (`node-pre-gyp`) | 3 |
| „Transparent MITM decrypt (TPROXY)" z instalatorem własnego CA do magazynu zaufania systemu | 3 |
| `optionalDependencies`: `tls-client-node`, `wreq-js` — wg komentarza w ich własnym `postinstall` to „TLS client for chatgpt-web/claude-web/grok-web/lmarena/perplexity-web" | 4 |
| `open-sse/services/browserPool.ts` deklaruje w nagłówku obchodzenie „Claude web's Cloudflare Turnstile" i „DuckDuckGo's anti-bot" przez binarne łatanie odcisku przeglądarki | 4 |

Uczciwie po stronie autorów: pula przeglądarek jest **opt-in** (`OMNIROUTE_BROWSER_POOL=off`
wyłącza ją całkowicie), `cloakbrowser` jest opcjonalny, projekt obsługuje też zwykłe API
z kluczem oraz lokalną Ollamę, a sklejanie nazwy pakietu w runtime (`["cloak","browser"].join("")`)
jest — wedle komentarza w kodzie — obejściem Turbopacka, nie ukrywaniem przed skanerami.
To jednak nie zmienia wniosku: zdolność łamania zabezpieczeń antybotowych **jest w produkcie**,
a sztandarowa obietnica „darmowych dostawców" na niej stoi.

Dodatkowo Husarz **ma już** własny router modeli (`husarz.router`): rejestr, wybór po tagach,
łańcuchy fallback odporne na cykle, clamp `max_tokens`, bramka egress z pinowaniem IP.
Pytanie nie brzmiało więc „czy dodać brakującą funkcję", tylko „czy zastąpić działający,
otypowany komponent aplikacją z drugim runtime i drugą bazą".

**Podprojekty** (`@omniroute/opencode-plugin`, `opencode-provider` — oznaczony jako
*deprecated*, `packages/browser-pool`, `open-sse` — `"private": true`, `electron/`, `skills/`):
żaden nie ma samodzielnej wartości dla Husarza.

**Do przejęcia jako pomysł:** budżet okna kontekstu (dziś clampujemy `max_tokens`, ale nie
sprawdzamy, czy prompt mieści się w oknie modelu) oraz routing wyprzedzająco omijający model
z wyczerpanym limitem (dziś mamy wyłącznie fallback po błędzie).

## Konsekwencje

**Pozytywne.** Rdzeń zostaje przy pięciu zależnościach i Pythonie 3.11. Reguła jest zapisana,
więc kolejna ocena narzędzia to praca na godziny, nie na dni. Trzy niezależne analizy wskazały
ten sam brak — **warstwę pomiaru jakości** — co czyni go najlepiej uzasadnionym kolejnym etapem.

**Negatywne i przyjęte świadomie.** Rezygnujemy z gotowego katalogu dostawców chmurowych
(dla platformy o deny-all egress to koszt pozorny) oraz z gotowej implementacji kompresji
kontekstu i ewaluacji — będziemy je pisać sami, co jest wolniejsze.

**Ryzyko.** Reguła może zostać użyta jako wygodna wymówka przed każdą nową zależnością.
Przeciwwaga: kryteria są konkretne i falsyfikowalne, a reguła pozytywna wprost dopuszcza
przejmowanie pomysłów.

## Odrzucone alternatywy

- **Uruchomienie OmniRoute za loopbackiem jako „lokalnego dostawcy".** Odrzucone: nasza
  walidacja airgap uznałaby `http://127.0.0.1:<port>/v1` za endpoint lokalny, audyt zapisałby
  wywołanie do `127.0.0.1`, a rzeczywista trasa danych byłaby poza naszą kontrolą. Patrz
  notatka o asymetrii walidacji w `docs/BEZPIECZENSTWO.md`.
- **Przyjęcie ART jako opcjonalnej extry (`[training]`).** Odrzucone: extra nie zdejmuje
  wymogu `requires-python >= 3.12` z metadanych pakietu.
- **Dwa osobne ADR-y.** Odrzucone: wartość jest w regule wspólnej dla obu przypadków.
