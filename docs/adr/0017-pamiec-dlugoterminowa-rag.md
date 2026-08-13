# ADR-0017: Pamięć długoterminowa (RAG) — własny EmbeddingRagBackend

- Status: przyjęty
- Data: 2026-08-13
- Etap: 14

## Kontekst

Protokół `RagBackend` (`tools/rag.py`) istniał z `InMemoryRagBackend` (dopasowanie słowne),
a pakiet `husarz/memory/` był pustym stubem. Etap 13 (pętla narzędziowa) uczynił `rag.add`/
`rag.search` realnie wywoływanymi przez agentów — czas na produkcyjny, wektorowy backend.
Pamięć to zarazem powierzchnia wrażliwa: treść jest NIEZAUFANA (trwały kanał injekcji
cross-agent), embeddingi są **odwracalne do PII**, a dane wymagają szyfrowania at-rest.
Projekt powstał z panelu 3 architektur + adwersaryjnej krytyki suwerenności.

## Decyzja

### Własny `EmbeddingRagBackend`, NIE wrapper biblioteki

Za NIEZMIENIONYM `Protocol RagBackend` (drop-in za `InMemoryRagBackend`; `RagTool` bez
zmian) składamy trzy WSTRZYKIWALNE szwy (wzór `Transport`/`Fetcher`/`SandboxExecutor`):
`Embedder` (tekst → wektor), `VectorStore` (upsert/search po cosinusie). ~kilkaset linii,
zero ciężkich zależności rdzenia (PyInstaller-friendly), pełna kontrola egress/izolacji.
`mem0`/`graphiti`/`cognee`/`letta` NIE są owijane — łamią minimalizm i niezmienniki
(mem0=telemetria PostHog, graphiti=Neo4j, letta=framework agentowy). Wchodzą PÓŹNIEJ jako
lazy-importowane adaptery ZA tym samym Protocol, za twardymi bramkami adopcji.

### Suwerenność embeddingów — twardo, nie deklaratywnie

Domyślny/testowy: `FakeEmbedder` (deterministyczny, offline — test-double, NIE produkcja).
Produkcyjny: `OllamaEmbedder` (lokalny `/api/embeddings`, transport WSTRZYKIWALNY) z bramką
`check_endpoint_allowed` PRZED każdym wywołaniem (deny-all egress — embeddingi ~ PII nie
wychodzą na WAN) i walidacją wymiaru wektora (fail-closed, anty-korupcja magazynu). Klucz
(embedder za proxy) WYŁĄCZNIE jako referencja do sekretu. W profilu **airgap** `_cross_validate`
odrzuca nielokalny endpoint embeddera już przy starcie (domknięcie luki configu).

### Izolacja treści NIEZAUFANEJ

`VectorStore` partycjonuje po `namespace` (= `collection`); `search` skanuje WYŁĄCZNIE swój
namespace — zatruty `add` jednej kolekcji nie wypływa w `search` innej. `_cross_validate`
wymusza **rozłączne kolekcje** między narzędziami rag (zderzenie namespace = kanał injekcji
cross-agent). Wzrost bounduje `max_items` + ewikcja FIFO; dedup po `sha256(text)`. Wynik
`search` jest re-injektowany przez pętlę zawsze jako **ogrodzone DANE** (`fence_untrusted`,
ADR-0016). Domyślny backend pozostaje słowny `memory` (zero regresji, zero-dep); wektorowy
`embedding` jest opt-in.

### Trwałość i szyfrowanie at-rest — ODŁOŻONE do Etapu 14b (świadomie)

Krytyka wykazała, że szyfrowanie at-rest bez przewleczenia `SecretsProvider` do produkcji
(`create_app`/`cli` podają `NullSecretsProvider`) byłoby **teatrem** — klucz nigdy by się nie
rozwiązał. Dlatego `SqliteVectorStore` (trwałość) + `AesGcmCipher` (at-rest) wchodzą w
Etapie 14b RAZEM ze zmianą wiązania sekretów (`cli` → `create_app` → `_build_stack` →
`build_tool_loop`) — szyfrowanie ma być realne, gdy trafi do repo, nie martwym polem. MVP
używa `InMemoryVectorStore` (RAM — brak powierzchni at-rest, niezmiennik trywialnie spełniony).

## Konsekwencje

- (+) Realna, suwerenna pamięć semantyczna, w pełni wpięta i offline-testowalna, zero nowych
  zależności rdzenia (embedder na core httpx).
- (+) Izolacja cross-agent egzekwowana walidacją (nie tylko dyscypliną), egress-gate embeddera,
  fail-closed na wymiar i airgap.
- (−) MVP ulotny (RAM) — „długoterminowość" (trwałość) i at-rest dochodzą w 14b (uczciwie
  odłożone, nie udawane).
- (−) `FakeEmbedder` NIE jest realnym wyszukiwaniem semantycznym — produkcja wymaga Ollamy
  (`kind: ollama`, `nomic-embed-text`); default `memory` (słowny) zapewnia brak regresji.
- (−) Deszyfruj-przed-scoringiem (14b) da koszt O(N) na search — stąd `max_items` jako sufit.

## Bramki adopcji zewnętrznego backendu (mem0/graphiti/cognee) — każda = ADR + test

Telemetria OFF i zweryfikowana (mem0=PostHog); wszystkie wywołania przez `check_endpoint_allowed`
(deny-all); biblioteka MUSI używać NASZEGO lokalnego Embeddera (nie zdalnego API); licencja
permisywna; 100% samo-hostowalne; treść nadal NIEZAUFANA (wynik ogradzany); offline-testowalne
z fakiem. mem0 = najlepszy kandydat na „memory layer"; graphiti/cognee = ciężkie (odłożone);
letta = NIE adoptować (framework). Bez `entry_points`/`importlib` (obcy kod = RCE) — adaptery
in-tree, lazy-import, opcjonalny extra.

## Alternatywy odrzucone

- **Wrapper mem0/pgvector w MVP**: ciężkie zależności/serwer, telemetria, słabsza offline-
  testowalność — odłożone za szew.
- **SQLite jako domyślny store**: wymaga wiązania sekretów (blocker at-rest) — 14b.
- **`kind: fake` jako produkcyjny default**: hash-ranking ~ losowy (regres vs słowny) — fake
  tylko w testach; produkcyjny default to `memory`, wektorowy opt-in `embedding`+`ollama`.
