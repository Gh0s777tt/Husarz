# Router modeli (Etap 1)

Router dobiera model do żądania i wykonuje wywołanie przez warstwę OpenAI-compat
(vLLM/Ollama/SGLang), z fallbackami i kontrolą kosztów. Wszystko sterowane
konfiguracją (`models.yaml` + `routing.yaml`) — zero hardcode. Kod: `husarz.router`.

## Przepływ żądania

```
ChatRequest ─▶ ModelRouter.complete(agent|model|tags)
                 │
                 ├─ select_candidates()  ── lista modeli (preferencje + fallbacki)
                 ├─ RateLimiter.acquire() ── kontrola kosztów (opcjonalna)
                 ├─ _apply_cost_controls() ── clamp max_tokens
                 └─ dla kolejnych kandydatów:
                        bramka wizyjna    ── obrazy tylko do modelu vision:true
                        bramka budżetu    ── prompt + rezerwa ≤ context_length
                        bramka egress     ── host na allowliście
                        build_client(spec) ─▶ ModelClient.chat() ─▶ ChatResponse
                        (błąd backendu → następny kandydat; wszyscy zawiodą → AllModelsFailedError)
```

## Wybór modelu (`select_candidates`)

Kolejność preferencji (pierwszy wygrywa; reszta to fallbacki):

1. jawny `model=...` (jeśli podany),
2. model agenta z `routing.agent_models` (o ile nie `auto`),
3. modele preferowane przez reguły `routing.rules`, których `match_tags` ⊆ żądane `tags`,
4. dowolny model posiadający wszystkie żądane `tags`, **uporządkowany strategią**,
5. `models.default` — gdy nic nie wybrano.

Do każdego wybranego modelu dołączany jest jego łańcuch `fallback` (o ile
`routing.fallbacks_enabled`). Zwracane są wyłącznie modele **włączone**
(`enabled: true`); model wyłączony jest pomijany, ale jego fallbacki nadal
działają. Rozwijanie fallbacków jest odporne na cykle (każdy model odwiedzany raz).

### Strategia doboru: `tags`, `cost`, `latency`

`routing.strategy` porządkuje **wyłącznie pulę z punktu 4** — modele pasujące tagami.

| Strategia | Porządek puli z punktu 4 |
|---|---|
| `tags` (domyślna) | kolejność rejestru |
| `cost` | rosnąco wg `cost_per_1m_input + cost_per_1m_output` |
| `latency` | rosnąco wg `latency_p50_ms` |

**Zakres jest węższy, niż sugeruje nazwa, i to jest świadome.** Strategia NIE rusza modelu
wskazanego wprost, przypisania z `routing.agent_models` ani kolejności w
`routing.rules[].prefer` — to są jawne decyzje operatora. Gdyby `strategy: cost` je
nadpisywało, przypisanie agenta do konkretnego modelu przestałoby cokolwiek znaczyć.

Sortowanie jest **stabilne**: przy równych wartościach obowiązuje kolejność rejestru, czyli
zachowanie `tags`.

**Dane są wymagane, inaczej start się nie powiedzie.** Przy `cost`/`latency` walidacja
krzyżowa żąda odpowiednich pól od KAŻDEGO modelu włączonego i otagowanego — bo dokładnie te
tworzą porządkowaną pulę. Model bez tagów i model wyłączony są zwolnieni, bo nigdy do niej
nie trafiają. Brak danych oznaczałby politykę opartą na luce w konfiguracji, nie na
rzeczywistości.

**Cena jest sumą obu składowych i jest to przybliżenie.** W chwili DOBORU nie wiadomo, ile
tokenów wyjścia wygeneruje żądanie — rozstrzyga się to dopiero po odpowiedzi. Każda waga
byłaby więc zgadywaniem kształtu ruchu; suma jest jawna i monotoniczna (model tańszy w obu
składowych zawsze wypada wcześniej).

**Jednostka ceny jest umowna i celowo nienazwana.** Husarz jest hostowany samodzielnie, więc
„cena" znaczy co innego dla modelu lokalnego (prąd, amortyzacja, czas zajętości) niż dla
dostawcy zewnętrznego. Router porównuje wyłącznie względnie — wystarczy jedna skala dla
całego rejestru. `latency_p50_ms` to POMIAR operatora, nie obietnica dostawcy: zależy od
sprzętu i obciążenia, więc wpisana liczba jest ważna tylko dla tej instalacji.

> **SPROSTOWANIE (Etap 18h).** Stała tu wcześniej uwaga, że `routing.strategy` przyjmuje
> wyłącznie `tags`, a `cost`/`latency` są odrzucane, bo „wymagają danych o modelach, których
> `models.registry` nie przechowuje". Dane doszły (`cost_per_1m_input`, `cost_per_1m_output`,
> `latency_p50_ms`), `selection.py` faktycznie je czyta i obie strategie działają — więc
> tamta uwaga przestała być prawdziwa i została zdjęta.
>
> Historia jest warta zapamiętania: `cost`/`latency` były wcześniej **przyjmowane** i po
> cichu dawały zachowanie `tags`. Kolejność naprawy wynikała wprost z tej lekcji — najpierw
> dane, potem czytelnik. Dodanie pól ceny bez strategii, która je czyta, byłoby tą samą
> wadą przeniesioną o poziom niżej.

## Bramka egress (deny-all)

Przed połączeniem z modelem router sprawdza endpoint względem `security.egress`
([egress.py](https://github.com/Gh0s777tt/Husarz/blob/main/src/husarz/router/egress.py)):

- endpointy **lokalne/prywatne** (loopback, RFC 1918, `.local`) są zawsze dozwolone
  (lokalny vLLM/Ollama nie jest ruchem do WAN),
- host **zdalny** wymaga `default_policy: allow` albo obecności na `egress.allowlist`
  (dokładnie lub jako subdomena); w przeciwnym razie kandydat jest pomijany
  (`EgressError`) i router próbuje kolejnego (np. lokalnego fallbacku).

To kontrola na poziomie aplikacji (defense-in-depth). Pełne wymuszenie sieciowe
(NetworkPolicy, sandbox) należy do Etapu 4/6.

## Kontrola kosztów

- `routing.cost_controls.max_tokens_per_request` — `max_tokens` żądania jest
  przycinany do limitu (albo ustawiany, gdy nie podano).
- `routing.cost_controls.max_requests_per_minute` — `RateLimiter` (token bucket)
  z uzupełnianiem w tempie/min. Przekroczenie → `RateLimitExceededError`.

## Budżet okna kontekstu

Router sprawdza dla KAŻDEGO kandydata, czy prompt **razem z rezerwą na odpowiedź** zmieści się
w jego `context_length`. Bez tego backend zwracał błąd albo po cichu ucinał kontekst, a agent
w pętli narzędziowej wypalał limit iteracji, nie wiedząc dlaczego — problem zaobserwowany
przy modelu 7B, gdzie rozmowa rośnie o wyniki narzędzi.

**Niezmieszczenie się jest POMINIĘCIEM kandydata, nie błędem.** Prompt za duży dla modelu 7B
może wejść do fallbacku o większym oknie, więc router traktuje to jak każdą inną przyczynę
pominięcia i próbuje dalej. Dopiero gdy nie starczy okna u NIKOGO, leci `AllModelsFailedError`
z powodem zawierającym liczby.

Rezerwa na odpowiedź bierze się kolejno z: `max_tokens` żądania (już po kontroli kosztów),
`max_tokens` z rejestru modelu, a na końcu z wartości zapasowej 512. Bez tej rezerwy prompt
mógłby wypełnić okno co do tokena, zostawiając model bez miejsca na odpowiedź.

### Oszacowanie, nie pomiar — i dlaczego

Dokładne policzenie tokenów wymaga tokenizera KONKRETNEGO modelu, a rdzeń Husarza ma pięć
zależności runtime i żadna nim nie jest. Dokładanie `transformers` czy `tiktoken` wyłącznie po
to, by zmierzyć długość, byłoby złą wymianą. Szacujemy więc — ale **zachowawczo i na podstawie
pomiaru**, nie intuicji.

Kalibracja wykonana realnym tokenizerem (`qwen2.5-coder:7b` przez Ollamę, licznik
`prompt_eval_count`):

| Rodzaj treści | znaków na token |
|---|---|
| polski, proza | 2,19 |
| polski, techniczny | 2,08 |
| kod (Python) | 2,88 |
| angielski | 2,70 |
| **JSON / wpis dziennika** | **1,68** |

Dzielnik bierzemy z ostatniego wiersza (zaokrąglony do 1,6), bo to najgorszy przypadek dla NAS:
wyniki narzędzi w pętli agentowej są właśnie JSON-em. Doliczamy też zmierzony narzut szablonu
czatu — 32 tokeny stałe i 6 na wiadomość.

**Skutek: szacujemy Z GÓRY.** Dla typowej polskiej rozmowy wychodzi ~30% więcej, niż naliczy
tokenizer, więc odmówimy nieco wcześniej, niż trzeba. To świadomy wybór — fałszywa odmowa
z czytelnym komunikatem jest tańsza niż cicha awaria backendu w środku pętli narzędziowej.

!!! warning "Obrazy: „nie wiem" zaokrąglone w BEZPIECZNĄ stronę"
    Modele wizyjne liczą obrazy osobno i zależnie od rozdzielczości, czego nie da się
    odtworzyć bez tokenizera konkretnego modelu.

    **SPROSTOWANIE.** Poprzednia wersja tego akapitu mówiła „obrazy NIE są liczone" — i tak
    było: obraz kosztował **zero** tokenów. To najgorsze możliwe zaokrąglenie niewiedzy,
    bo bramka istnieje właśnie po to, żeby nie wysłać promptu przekraczającego okno,
    a przy obrazach meldowała „mieści się" dla żądania, które model odrzuci albo po cichu utnie.

    Obraz kosztuje teraz **stałą, celowo wysoką liczbę tokenów**. To **nie jest pomiar
    i nie udaje pomiaru** — to zaokrąglenie w stronę, która nie kłamie. Konstrukcja stałej:
    obraz ma kosztować nie mniej niż strona gęstego tekstu (≈3000 znaków ÷ 1,6 znaku/token),
    bo model wizyjny, dla którego obraz byłby tańszy, i tak zmieści się w oknie z zapasem.
    Skutek jest zgodny z resztą modułu: przy obrazach odmówimy wcześniej, niż trzeba, a router
    wypróbuje model o większym oknie.

    Kalibracja pomiarem — tak jak dla tekstu (`prompt_eval_count` z Ollamy) — wymaga
    uruchomionego modelu **wizyjnego**; wtedy stałą należy zastąpić funkcją rozdzielczości.
    Zapisane w ROADMAP.

## Klienci i transport

- `ModelClient` — protokół: `chat(ChatRequest) -> ChatResponse`.
- `OpenAICompatClient` — buduje payload `chat/completions`, scala parametry
  (`spec.params` + nadpisania z żądania), dodaje nagłówek `Authorization`
  z klucza API i parsuje odpowiedź.
- `MockClient` — backend `mock`: deterministyczna odpowiedź, **bez sieci**
  (używany w testach i dev bez wag).
- `Transport` — warstwa HTTP oddzielona od logiki. `HttpxTransport` (produkcja,
  leniwy import `httpx`) albo transport wstrzyknięty w testach. Dzięki temu testy
  **nie wykonują połączeń sieciowych** (zgodnie z deny-all egress).

Wszystkie backendy sieciowe (`vllm`, `ollama`, `sglang`, `openai_compat`)
używają jednego `OpenAICompatClient` — różni je tylko endpoint z `models.yaml`.

## Sekrety (klucz API)

Klucz API pochodzi z `ModelSpec.api_key_ref` (np. `env:GLM_API_KEY`,
`vault:...`) i jest rozwiązywany w runtime przez dostawcę sekretów
(`husarz.config.secrets`). **Nigdy** nie jest wpisywany w plik ani w `params`.

## Przykład użycia

```python
from husarz.config import load_config
from husarz.router import ModelRouter, ChatRequest, ChatMessage

config = load_config("./config")
router = ModelRouter(config)  # domyślna fabryka klientów (mock/OpenAI-compat)

resp = router.complete(
    ChatRequest(messages=[ChatMessage("user", "Napisz test w pytest.")]),
    tags=["code"],           # albo agent="kopijnik", albo model="glm-main"
)
print(resp.model, resp.content)
```

> Ten przykład wymaga **działającego endpointu** modelu (vLLM/Ollama/SGLang pod
> adresem z `models.yaml`). Do pracy bez sieci użyj modelu z `backend: mock`
> (wtedy `MockClient` zwraca deterministyczną odpowiedź) albo wstrzyknij własną
> `client_factory`.

## Testowanie

- Logika wyboru i fallbacków — czysta, testowana bez sieci.
- Klient — testowany z wstrzykniętym transportem (payload/parsing/błędy).
- `ModelRouter` — testowany z wstrzykniętą fabryką klientów (fallback, limity, koszty).
- Test integracyjny — selekcja na realnej konfiguracji repo.

Szczegóły decyzji: [ADR-0003](adr/0003-router-modeli.md).

## Wyłącznik bezpiecznikowy (`routing.health`)

Model, który przed sekundą przekroczył limit czasu, był przy następnym żądaniu **nadal
pierwszym kandydatem**. Każde kolejne żądanie płaciło więc pełny limit czasu, zanim spadło
na fallback — a limity bywają liczone w dziesiątkach sekund. Przy padniętym modelu głównym
cała platforma zwalniała przy każdym zapytaniu, i to w sposób dla użytkownika
niewytłumaczalny: odpowiedzi przychodziły, tylko bardzo wolno.

Po `failures_to_open` **kolejnych** awariach model trafia na koniec listy kandydatów na
`cooldown_seconds`. `cooldown_seconds: null` wyłącza mechanizm.

### Trzy rozstrzygnięcia, które decydują o zachowaniu

**Odsunięcie, nie wykluczenie.** Kandydat z otwartym wyłącznikiem spada na koniec listy, ale
z niej nie znika. Różnica ujawnia się w przypadku, który przy awarii zdarza się najczęściej:
gdy padło wszystko (sieć, wspólny host silników), wykluczanie zostawiłoby pustą listę
kandydatów i `NoModelAvailableError` — twardą odmowę zamiast próby, która mogłaby się
powieść.

**Awarią jest tylko błąd realnego wywołania.** Liczy się `ModelBackendError`: limit czasu,
brak połączenia, błąd silnika. **Nie liczą się** pominięcia wynikające z właściwości
ŻĄDANIA — brak `vision` przy obrazie, prompt niemieszczący się w oknie kontekstu, blokada
egress. Model pominięty, bo prompt był za długi, jest w pełni zdrowy; karanie go
zdegradowałoby go za cudzy błąd i przy następnym, krótszym żądaniu wysłałoby ruch w gorsze
miejsce.

**Licznik jest kolejnych awarii, nie sumy.** Pojedynczy sukces zeruje go w całości. Model
działający z przerwami nie ma się dogrywać do wyłączenia przez tydzień drobnych potknięć —
wyłącznik łapie awarię trwającą TERAZ.

### Zasięg stanu

Rejestr zdrowia żyje tak długo jak instancja routera, czyli od startu do najbliższego
nadpisania konfiguracji w runtime (`POST /api/config/runtime`). To świadome: zmiana
konfiguracji może zmienić endpointy, więc odziedziczenie po niej starych liczników
odsuwałoby model, który przed chwilą został naprawiony.

Stan **nie jest** współdzielony między procesami. Przy wielu instancjach każda uczy się
osobno — mechanizm jest optymalizacją opóźnienia, nie rozproszonym stanem klastra.
