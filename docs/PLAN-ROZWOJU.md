# Plan rozwoju — co jeszcze można zrobić i jak ulepszyć platformę

Ten dokument jest **przeglądem otwartych możliwości** dla całej platformy: rdzenia AI
(router, orkiestrator, agenci, pętla narzędziowa, pamięć, ewaluacja) oraz launchera
(CLI, diagnoza, bootstrap, konsola WWW, wdrożenie).

Nie zastępuje [ROADMAP.md](https://github.com/Gh0s777tt/Husarz/blob/main/ROADMAP.md).
ROADMAP jest **rejestrem zobowiązań** — co zostało postanowione i w jakim stanie jest. Ten dokument jest **mapą przestrzeni** — co
w ogóle da się zrobić, ile to kosztuje i co konkretnie stoi na przeszkodzie. Pozycja
przechodzi stąd do ROADMAP w chwili, gdy zostaje podjęta decyzja o jej realizacji.

## Jak czytać

Każda pozycja ma **blokadę** — jedną z trzech. To rozróżnienie jest sednem dokumentu,
bo bez niego lista otwartych pozycji sugeruje, że wszystkie czekają na te same warunki:

| Znak | Blokada | Znaczenie |
|---|---|---|
| 🟢 | **brak** | czysta praca programistyczna, można wziąć od zaraz |
| 🟡 | **decyzja operatora** | wymaga świadomej decyzji człowieka (koszt, publikacja, nieodwracalność) |
| 🔴 | **brakujący zasób** | wymaga sprzętu, modelu, klastra albo systemu, którego tu nie ma |

Koszt szacowany zgrubnie: **S** — do kilku godzin, **M** — dzień, **L** — kilka dni,
**XL** — osobny etap.

Szacunek jest szacunkiem. W tym projekcie trzykrotnie zdarzyło się, że pozycja opisana
jako S okazała się M, bo poprawka wzorca wymagała przeszukania repozytorium (patrz
CLAUDE.md, „Dwa wzorce, które w tym projekcie kosztowały najwięcej").

## Dziesięć pozycji o najlepszym stosunku wartości do kosztu

Kolejność jest rekomendacją, nie zobowiązaniem.

| # | Pozycja | Obszar | Blokada | Koszt |
|---|---|---|---|---|
| 1 | ~~`husarz audit verify`~~ — **zrobione** (Etap 18e) | Launcher | ✅ | S |
| 2 | ~~Kubełek limitu tempa per `principal`~~ — **zrobione** (Etap 18f) | Bezpieczeństwo | ✅ | M |
| 3 | ~~Routing świadomy zdrowia modelu~~ — **zrobione** (Etap 18j) | Router | ✅ | M |
| 4 | ~~Rotacja klucza HMAC audytu~~ — **zrobione** (Etap 18a) | Bezpieczeństwo | ✅ | M |
| 5 | ~~Równoległa delegacja niezależnych kroków planu~~ — **zrobione** (Etap 18k) | Orkiestrator | ✅ | L |
| 6 | ~~`husarz config explain`~~ — **zrobione** (Etap 18i) | Launcher | ✅ | S |
| 7 | ~~Strumieniowanie odpowiedzi~~ — **zrobione** SSE, nie WebSocket (Etap 18l–m) | API | ✅ | L |
| 8 | Chunkowanie dokumentów w RAG | Pamięć | 🟢 | L |
| 9 | ~~`mkdocs --strict` i `black scripts` w CI~~ — **zrobione** (Etap 18) | Operacje | ✅ | S |
| 10 | ~~Pola kosztu i opóźnienia w `ModelSpec`~~ + strategie — **zrobione** (Etap 18h) | Router | ✅ | S |

---

## 1. Launcher — wiersz poleceń

Dziś `husarz` udostępnia: `validate`, `eval`, `doctor`, `bootstrap`, `version`, `up`,
`roe sign`, `roe verify`, `useradd`. To pokrywa uruchomienie i diagnozę, ale zostawia
operatora bez narzędzi do trzech rzeczy, które robi się najczęściej: sprawdzenia
integralności audytu, zarządzania sekretami i zrozumienia, skąd wzięła się wartość
w konfiguracji.

### ✅ `husarz audit verify` (S) — ZREALIZOWANE (Etap 18e)

Pozycja opisywała lukę tak: operator podejrzewający naruszenie musiał albo uruchomić całą
platformę, albo napisać własny skrypt. Realizacja pokazała, że sformułowanie było **za
słabe w dwóch miejscach**:

1. Uruchomienie platformy nie jest alternatywą, tylko ślepą uliczką — od Etapu 18b
   `husarz up` na uszkodzonym dzienniku **odmawia startu**. Operator zostawał więc
   z platformą, która nie wstaje, i bez sposobu, by dowiedzieć się dlaczego. Stąd osobna
   ścieżka wglądu (`otworz_do_wgladu`), otwierająca dziennik wyłącznie do odczytu.
2. Werdykt „coś jest nie tak" jest bezużyteczny. Przy prawdziwej awarii liczyła się
   wyłącznie odpowiedź „GDZIE" — polecenie podaje numer wpisu, czas, akcję i rodzaj
   niezgodności.

Zakres zrealizowany: liczba wpisów, stan kotwicy, pokolenia kluczy, wynik, miejsce i rodzaj
niezgodności; kody wyjścia 0/1/2 nadające się do crona. Opis: [LAUNCHER](LAUNCHER.md).

**`husarz audit anchor` świadomie NIE powstało.** Miało przestawiać kotwicę „po zamierzonej
rotacji", ale po Etapie 18c jest to pomysł wprost szkodliwy: kotwica dostała **zapadkę**
właśnie po to, żeby nie dało się jej cofnąć, a polecenie do jej przestawiania byłoby gotowym
narzędziem do zacierania śladu odcięcia ogona — i to podpisanym przez sam projekt. Jeżeli
rotacja pliku dziennika ma być wspierana, musi to być operacja, która STARY dziennik
zachowuje i wiąże z nowym, a nie taka, która przestawia licznik.

### ✅ `husarz config explain <ścieżka>` (S) — ZREALIZOWANE (Etap 18i)

Zrealizowane szerzej, niż zapowiadała pozycja. Zapis mówił o wskazaniu warstwy; przy pisaniu
okazało się, że sama warstwa nie wystarcza — sedno pytania leży w RÓŻNICY między warstwami,
więc raport pokazuje, co mówi KAŻDA z nich, także milcząca. Doszły dwa rozróżnienia, których
pozycja nie przewidywała: `null` odróżniony od braku wpisu (w YAML-u bywa wartością znaczącą)
oraz jawna odmowa rozwijania referencji do sekretów. Opis: [LAUNCHER](LAUNCHER.md).

### 🟢 `husarz secrets set|list|rm` (M)

Zapisywalny magazyn (`husarz:`) jest dziś zasilany wyłącznie przez kreator połączeń
git w konsoli. Operator chcący wstawić token do innego celu nie ma ścieżki. Lista musi
pokazywać **nazwy i metadane, nigdy materiał**.

### 🟢 `husarz models list|pull|rm` (M)

`bootstrap` działa w trybie wszystko-albo-nic wobec brakujących modeli. Zarządzanie
pojedynczym modelem wymaga dziś sięgnięcia po CLI silnika, czyli wyjścia poza Husarza —
a wtedy polityki egressu i zgody z `bootstrap.sources` nie obowiązują.

### 🟢 `--json` dla `doctor` i `validate` (S)

Wyjście nadające się do monitoringu i do skryptów. Dziś format jest przeznaczony dla
człowieka, więc każdy monitoring musiałby parsować tekst — czyli psuć się przy pierwszej
korekcie językowej.

### 🟢 `husarz init` (S)

Rusztowanie katalogu konfiguracyjnego. Dziś operator kopiuje `config/` ręcznie, co jest
niezapisane w README jako krok i łatwo je pominąć.

### 🟢 `husarz status` / nadzór nad `up` (M)

`up` działa na pierwszym planie i nie wstaje po awarii. Generator jednostki systemd
(Linux) i launchd (macOS) plus `status` czytający `/api/health` domykają uruchamianie
produkcyjne bez wprowadzania zależności od zewnętrznego supervisora.

### 🟢 Uzupełnianie w powłoce (S)

Bash i zsh. Drobiazg, który znacząco zmienia komfort pracy z rozrastającym się CLI.

---

## 2. Diagnoza (`doctor`) i bootstrap

Diagnoza jest najmłodszą i najszybciej rosnącą częścią launchera. Ma trzy znane
ograniczenia, wszystkie zapisane w ROADMAP.

### 🟢 Modele wybierane wyłącznie przez dopasowanie tagów (M)

`_role_modeli` pokrywa czat, model domyślny, `agent_models`, `routing.rules[].prefer`
i łańcuchy fallback. **Nie pokrywa** modeli, które trafiają do wyboru jedynie przez
posiadanie wszystkich wymaganych tagów (punkt 4 w `select_candidates`). Taki model może
być niedostępny, a diagnoza tego nie pokaże.

### 🟢 Sonda głęboka w konsoli WWW (L)

Wymaga zadania w tle i odpytywania o stan — sonda potrafi trwać dziesiątki sekund, więc
nie zmieści się w synchronicznym żądaniu HTTP. Warunek wstępny: limit tempa per
`principal` (pozycja 2 z listy dziesięciu).

### 🟢 Zawężenie ładunku `GET /api/doctor` (S)

Odpowiedź zawiera pełne endpointy i ścieżki. Dla CLI to właściwe, dla przeglądarki —
nadmiarowe. Rozdzielenie widoku CLI i widoku HTTP.

### 🟢 Sonda ocenia obecność odpowiedzi, nie jej jakość (L)

„Odpowiedział" znaczy dziś tyle, że backend zwrócił niepustą treść w limicie czasu. Model
odpowiadający bełkotem przechodzi. Ocena jakości wymaga zestawu kontrolnego z oczekiwaną
własnością odpowiedzi — to w praktyce ta sama maszyneria, co weryfikator
`malformed_ratio` w ewaluacji, więc pozycje warto robić razem.

### 🟢 Historia diagnozy (M)

Diagnoza jest migawką. Nie ma odpowiedzi na pytanie „od kiedy to nie działa", choć
`husarz.runs` już zbiera przebiegi i infrastruktura zapisu istnieje.

### 🟡 Pobranie silnika przez `bootstrap` (L)

Świadomie odłożone — patrz [ADR-0025](adr/0025-pobieranie-wag-za-zgoda.md). Oznaczałoby
ściąganie i uruchamianie **binarki wykonywalnej**, nie danych, co jest zupełnie inną
klasą ryzyka niż pobranie wag. Wymaga własnego ADR i decyzji operatora o modelu zaufania
(podpisy, sumy kontrolne, źródła).

---

## 3. Router i modele

### ✅ Pola kosztu i opóźnienia + strategie `cost`/`latency` — ZREALIZOWANE (Etap 18h)

Pozycja zapowiadała kolejność: „najpierw dane w schemacie (S), potem strategia je czytająca
(M)". Kolejność została zachowana, ale **w jednym kroku** — i to nie było przyspieszaniem
na skróty, tylko wnioskiem z tej samej lekcji, która pozycję zrodziła. Dodanie pól ceny
w osobnym commicie zostawiłoby na jakiś czas dokładnie tę wadę, którą Etap 17m usuwał:
pole konfiguracji, które wygląda na działające i nic nie robi.

Zrealizowane: `cost_per_1m_input`, `cost_per_1m_output`, `latency_p50_ms` w `ModelSpec`;
`routing.strategy: cost` i `latency` porządkujące pulę dopasowaną tagami; walidacja żądająca
danych od modeli, które do tej puli trafiają. Opis: [ROUTER](ROUTER.md).

Przy okazji wyszło, że odmowa dla `cost_controls.max_cost_per_task` powoływała się na tę samą
brakującą cenę — więc jej uzasadnienie stało się nieprawdziwe i zostało poprawione. Samo pole
NADAL nie jest egzekwowane, ale z innego powodu: `UsageMeter` nie prowadzi atrybucji zużycia
per model. Pozycja przeszła do ROADMAP z nową, właściwą przyczyną.

**Kalibracja realnych wartości pozostaje otwarta** 🔴 — dostarczony `config/models.yaml` ma
pola wykomentowane, bo wpisanie zmyślonych liczb byłoby gorsze niż ich brak.

### ✅ Routing świadomy zdrowia — wyłącznik bezpiecznikowy (M) — ZREALIZOWANE (Etap 18j)

Zrealizowane zgodnie z opisem, z jednym rozstrzygnięciem, którego pozycja nie przesądzała:
kandydat z otwartym wyłącznikiem jest ODSUWANY na koniec, a nie wykluczany. Wykluczanie
byłoby prostsze, ale w przypadku, który przy awarii zdarza się najczęściej — gdy padło
wszystko — zostawiałoby pustą listę kandydatów i twardą odmowę zamiast próby.

Drugie rozstrzygnięcie: awarią jest wyłącznie błąd realnego wywołania. Pominięcia wynikające
z właściwości żądania (brak wizji, prompt poza oknem, blokada egress) nie liczą się, bo model
pominięty z tych powodów jest w pełni sprawny. Opis: [ROUTER](ROUTER.md).

### 🟢 Limit współbieżności per model (M)

Husarz nie ogranicza liczby jednoczesnych żądań do jednego modelu. Ile z nich silnik
obsłuży naprawdę równolegle, zależy od jego własnej konfiguracji (w Ollamie
`OLLAMA_NUM_PARALLEL`), a nadmiar ustawia się w kolejce **po stronie silnika** — gdzie
Husarz go nie widzi i nie może nim zarządzać. Przy równoległej delegacji kroków planu
(sekcja 4) stanie się to odczuwalne.

### 🟢 Podtrzymanie modelu w pamięci (S)

Silnik zwalnia model po okresie bezczynności; pierwsze żądanie po przerwie płaci pełne
wczytanie wag. Sterowane konfiguracją podtrzymanie jest tanie i zauważalne.

### 🟢 Natywny adapter `tool_calls` (L)

Dziś akcje narzędziowe jadą przez blok tekstowy parsowany odpornie. Function-calling API
daje strukturę wymuszaną po stronie silnika i znosi całą klasę błędów parsowania.
Warunek: nie wolno usunąć ścieżki tekstowej — nie każdy backend ją wspiera.

### 🟢 Obsługa przekierowań (M)

`follow_redirects=False` jest domyślnie **poprawną** decyzją: przekierowanie omijałoby
pinowanie IP z [ADR-0020](adr/0020-pinowanie-ip-anty-ssrf.md). Obsługa przekierowań musi
oznaczać ponowne przejście pełnej bramki dla każdego skoku, nie jej wyłączenie.

---

## 4. Orkiestrator i agenci

Pętla to dziś: plan → deleguj → obserwuj → refleksja → synteza, z `max_extra_rounds=1`
i **szeregowym** wykonaniem kroków.

### 🟢 Równoległa delegacja niezależnych kroków (L) — **największy zysk jakościowy**

`Plan` jest płaską listą `PlanStep(agent, task)`. Nie ma pojęcia zależności, więc nie ma
sposobu, by stwierdzić, że dwa kroki mogą pójść równolegle — a bardzo często mogą
(zwiadowca sprawdzający dwa niezależne wątki, kopijnik i kanclerz pracujący na
rozłącznych plikach).

Zakres: opcjonalne `depends_on` w kroku planu, wykonanie warstwami grafu, limit
współbieżności z konfiguracji. Wymaga wcześniejszego limitu współbieżności per model
(sekcja 3), inaczej równoległość tylko przeniesie kolejkę do silnika.

### 🟢 Naprawa nieudanego kroku (M)

Krok zakończony błędem daje obserwację i pętla idzie dalej. Nie ma ponowienia ani
przeformułowania zadania. Jedna próba naprawy z treścią błędu w kontekście to typowo
duża poprawa skuteczności przy małym koszcie.

### 🟢 Walidacja planu przed wykonaniem (S)

`_Tally` **zlicza** `plan_unknown_agent`, ale nie reaguje. Plan wskazujący nieistniejącego
agenta wykonuje się z pominiętym krokiem. Jedna runda korekty („ci agenci są dostępne,
popraw plan") jest tańsza niż stracony krok.

### 🟢 Budżet całej orkiestracji (M)

Limity działają dziś na **pojedyncze żądanie** (`max_tokens_per_request`, okno kontekstu
z `router/budget.py`, `max_requests_per_minute`) i na **liczbę wywołań narzędzi**
(`ToolCallBudget`). Nie ma limitu na **całą orkiestrację**: `UsageMeter` sumuje zużycie
wszystkich faz i kroków, ale nikt tej sumy z niczym nie porównuje. Plan o dwudziestu
krokach przemnaża koszt przez dwadzieścia, nie napotykając żadnej bariery.

Zakres: twardy limit tokenów i czasu z konfiguracji, z czytelnym przerwaniem i syntezą
częściową z tego, co zdążyło powstać — przerwanie bez syntezy zmarnowałoby całą pracę.

### 🟢 Anulowanie i limit czasu (M)

Nie ma sposobu, by przerwać trwającą orkiestrację. W konsoli oznacza to zakładkę, która
wisi.

### 🟢 Postęp na żywo (L)

Operator widzi wynik dopiero po syntezie. Strumieniowanie kroków (sekcja 6) zmienia
odbiór platformy bardziej niż jakakolwiek zmiana w samych modelach.

### 🟢 Pamięć między orkiestracjami (L)

Każdy przebieg zaczyna od zera. Wnioski z poprzedniego uruchomienia nad tym samym
repozytorium przepadają. Naturalne połączenie z RAG (sekcja 7) i z `husarz.runs`.

### 🟢 Testy regresji promptów agentów (M)

Prompty w `prompts/*.md` sterują zachowaniem siedmiu agentów, a zmiana w nich nie ma
własnego testu. Edycja promptu może po cichu zmienić zachowanie — to dokładnie ta klasa
zmiany, którą CLAUDE.md nazywa „zmianą zachowania" i wymaga dla niej testu skutku.

---

## 5. Pętla narzędziowa

### 🟢 Buforowanie wyników narzędzi (M)

Dwukrotne `web.fetch` tego samego adresu w jednym przebiegu to dwa wyjścia na zewnątrz.
Bufor w obrębie przebiegu zmniejsza egress — czyli powierzchnię ryzyka, nie tylko czas.

### 🟢 Prośba o naprawę zniekształconego bloku akcji (M)

Model, który wygenerował niepoprawny blok, traci iterację. Jedno ponowienie z komunikatem
o błędzie parsowania jest tanie. Warunek: **ponowienie musi zużywać budżet**, inaczej
tworzy pętlę bez ograniczenia.

### 🟢 Równoległe wywołania narzędzi (L)

Pętla wykonuje jedno wywołanie na iterację (`for index in range(max_iterations)`).
Niezależne odczyty plików mogłyby iść równolegle. Uwaga: to dotyka bramki narzędziowej,
więc wchodzi w trzeci wiersz tabeli audytu w CLAUDE.md — pełny przegląd adwersaryjny.

---

## 6. API i konsola WWW

### 🟢 Strumieniowanie po WebSocket (L)

Największa pojedyncza poprawa odbioru platformy. Dziś czat czeka na pełną odpowiedź.

### 🟢 Przeglądarka historii przebiegów (M)

`husarz.runs` zapisuje `RunRecord` i `OrchestrationRecord`, ale **nie ma endpointu, który
by je udostępniał** — jest `/api/usage`, nie ma `/api/runs`. Dane są zbierane i nikt ich
nie ogląda.

### 🟢 Wizualizacja planu (M)

Orkiestracja zwraca kroki i obserwacje jako tekst. Widok drzewa planu z wynikiem każdego
kroku to niewielka praca, a bardzo duża różnica w zrozumieniu, co się stało.

### 🟢 Podział `console.html` (M)

886 linii w jednym pliku statycznym. Jeszcze się utrzymuje, ale każda kolejna zakładka
pogarsza sytuację. **Warunek brzegowy: bez zewnętrznych zależności i bez kroku budowania** —
samowystarczalność konsoli jest cechą, nie przypadkiem.

### 🟢 Dostępność i układ mobilny (M)

Nie było przeglądu pod kątem czytników ekranu, kontrastu ani wąskich ekranów.

### 🟡 Wersja angielska interfejsu (L)

Repozytoria są publiczne, a suwerenna platforma AI ma odbiorców poza Polską. To jednak
zmiana o charakterze produktowym: dokumentacja i komentarze są po polsku z wyboru
zapisanego w CLAUDE.md, a i18n samego UI bez i18n dokumentacji daje połowiczny efekt.
Decyzja należy do operatora.

---

## 7. Pamięć i RAG

### 🟢 Chunkowanie dokumentów (L)

Dziś dokument trafia do indeksu w całości. Duży plik albo nie mieści się w kontekście,
albo rozmywa trafność. Podział na fragmenty z zachowaniem granic zdań to warunek wstępny
użyteczności RAG na realnym repozytorium.

### 🟢 Wyszukiwanie hybrydowe i ponowne rankowanie (L)

Samo cosine na embeddingach gubi dopasowania dokładne (nazwy funkcji, identyfikatory).
BM25 obok wektorów plus ponowne rankowanie wyników to standardowa i skuteczna poprawa.

### 🟢 Przyrostowe indeksowanie katalogu (M)

Nie ma śledzenia zmian. Ponowne indeksowanie folderu jest pełne, więc kosztowne, więc
robione rzadko, więc indeks jest nieaktualny.

### 🟢 Pochodzenie fragmentów w odpowiedzi (M)

Model dostaje fragmenty, ale odpowiedź ich nie cytuje. Operator nie ma jak sprawdzić, czy
odpowiedź pochodzi z dokumentu, czy z wyobraźni modelu — a to jest dokładnie ta klasa
zaufania, którą projekt buduje wszędzie indziej.

### 🟢 Polityka usuwania poza cap+FIFO (M)

Najstarszy wpis nie jest tym samym co najmniej wartościowy.

### 🟢 Backend pgvector (XL)

Zapisany w ROADMAP. Adapter za istniejącym protokołem `VectorStore`, więc rdzeń się nie
zmienia — ale wprowadza zależność od serwera bazy, czyli zmienia model wdrożenia.

---

## 8. Ewaluacja

Dziś `husarz eval` sprawdza trzy rzeczy: niezmienniki routingu, decyzje bramki narzędziowej
i uruchomienie testów. Wszystkie bez modelu i bez sieci — i to jest jego siła, bo dzięki
temu jest bramką jakości, a nie osobnym przedsięwzięciem.

### 🟢 Linia bazowa i trend (M)

Wynik jest binarny: przeszło albo nie. Nie ma zapisu, że wczoraj przechodziło 40 z 42,
a dziś 38 — czyli regresja bez twardego progu jest niewidoczna.

### 🟢 Wzorcowe zapisy przebiegów (L)

Zestawy porównujące strukturę przebiegu z zapisanym wzorcem złapałyby zmiany w pętli
narzędziowej, których dziś nie łapie nic poza testami jednostkowymi.

### 🔴 Weryfikator `malformed_ratio` (M po odblokowaniu)

Metryki są już zbierane w `husarz.runs`. Brakuje zestawów i **realnego modelu**, na
którym miałyby sens — na atrapie wskaźnik zniekształceń wynosi zawsze zero.

### 🟢 Wskaźnik ukończenia planu (S)

`_Tally` zlicza już delegacje, pominięcia i odmowy ROE. Wystawienie tego jako metryki
ewaluacyjnej to niewielka praca na gotowych danych.

### 🟡 Sędzia LLM z ocenianiem relatywnym (XL)

Zapisany w ROADMAP jako **hipoteza**, nie plan. Wymaga poprzedzenia pomiarem, czy sędzia
w ogóle zgadza się z człowiekiem na tym materiale — inaczej dodaje warstwę pozornej
obiektywności. Dodatkowo koliduje z zasadą „bez modelu i bez sieci" dla `eval`, więc
musiałby być osobnym trybem.

---

## 9. Bezpieczeństwo

Wszystkie pozycje z tej sekcji podlegają **trzeciemu wierszowi** tabeli audytu
w CLAUDE.md: samokontrola + mutacja + pełny przegląd adwersaryjny, z notatką w
[BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).

### ✅ Rotacja klucza HMAC audytu (M) — ZREALIZOWANE (Etap 18a)

Wpisy niosą etykietę pokolenia (`key_id`), konfiguracja wskazuje klucz bieżący i listę
kluczy historycznych. Przy realizacji okazało się, że wersjonowanie klucza — czyli to,
co ta pozycja opisywała — jest **dopiero połową rzeczy**: samo dobieranie klucza po
etykiecie zostawiałoby posiadaczowi klucza wycofanego możliwość dopisania się do końcówki
dziennika. Sednem jest reguła niemalejącego pokolenia.
Projekt: [ADR-0026](adr/0026-rotacja-klucza-hmac-audytu.md).

### ✅ Fail-closed przy uszkodzonym łańcuchu bez klucza HMAC (M) — ZREALIZOWANE (Etap 18b)

`security.audit.integrity` z domyślnym `blocking`. Pozycja została zrealizowana szerzej,
niż ją opisano: przy okazji wyszło, że **nieczytelny** plik dziennika był po cichu
połykany, po czym Husarz dopisywał od nowego genesis w środku istniejącego pliku.

### ✅ Rozgałęziony łańcuch przy dwóch procesach — ZREALIZOWANE (Etap 18c), **nie było na tej liście**

Wada, której ten przegląd nie przewidział, bo nie wynikała z żadnej znanej luki. Wyszła
przy realizacji pozycji wyżej: po włączeniu blokującej integralności realny dziennik
projektu przestał się weryfikować. `AuditLog` miał blokadę wątkową, ale trzymał głowę
łańcucha w pamięci — dwa procesy na jednej ścieżce rozgałęziały dziennik.

Warto to odnotować jako ograniczenie samego przeglądu: **lista otwartych możliwości
wymienia to, o czym wiadomo, że brakuje.** Wady tej klasy — działający kod, który przestaje
działać dopiero w konfiguracji, o jakiej nikt nie pomyślał — nie trafiają na takie listy
i znajduje je dopiero uruchomienie.

### 🟢 Kubełek limitu tempa per `principal` (M) — **warunek wstępny RBAC**

Limit jest dziś globalny. Jeden użytkownik może wyczerpać budżet sond wszystkim. To jest
zapisany w ROADMAP **twardy warunek wstępny** dla jakiegokolwiek rozszerzenia
`diagnostics:read` poza administratora — panel sędziowski ocenił rozszerzenie bez tego
jako niebezpieczne (4,7/10).

### 🟢 Rotacja, wygasanie i limit wpisów w magazynie sekretów (M)

Ponowny zapis pod tą samą nazwą nadpisuje bez śladu i bez pojęcia ważności. Brak limitu
liczby wpisów to powierzchnia wyczerpania zasobów.

### 🟢 Bundle CA dla pozostałych ścieżek wychodzących (M)

Skonfigurowany jest dla routera. `web`, konektory MCP i embedder nadal używają domyślnego
magazynu zaufania systemu — czyli w środowisku z własnym CA zachowują się inaczej niż
router, co jest niespójnością trudną do zdiagnozowania.

### 🟢 Autoryzacja na cel w przepływie Puszkarza (L)

`RoeGate.evaluate` jest gotowe. Brakuje wpięcia decyzji per cel w przepływ, więc bramka
działa na poziomie zlecenia, nie pojedynczego celu.

### 🟡 Ślad po usunięciu całego pliku dziennika (L)

Skasowanie dziennika **wraz z kotwicą** nie zostawia śladu wewnątrz Husarza — i nie może,
bo obrona przed tym z definicji wymaga świadka na zewnątrz procesu. Rozwiązania (dopisywanie
do syslog, zdalny odbiornik, nośnik tylko-do-zapisu) zmieniają model wdrożenia, więc to
decyzja operatora, nie wybór implementacyjny.

### 🔴 mTLS i pełny OIDC (XL)

Schemat dziś **jawnie odrzuca** `mtls.enabled: true` i `oidc_enabled: true` — uczciwie,
bo za tymi polami nic nie stało. Implementacja wymaga infrastruktury tożsamości
i certyfikatów do przetestowania; bez niej powstałby kolejny martwy przełącznik.

---

## 10. Wdrożenie i operacje

### 🔴 Realne uruchomienie na klastrze (XL)

CNI z NetworkPolicy, gVisor, odpieczętowanie Vaulta. Manifesty są testowane po
przekształceniu przez kustomize, ale **nigdy nie działały na prawdziwym klastrze**. To
jest dokładnie ta klasa różnicy, o której mówi tabela „sprawdzaj skutek, nie deklarację".

### 🔴 Weryfikacja gVisor (M po odblokowaniu)

`runsc` nie jest tu zainstalowany, więc pomiary na realnym kontenerze wykonano na zwykłym
Dockerze. Domyślnym silnikiem jest dziś `docker`, właśnie dlatego, że para
`docker+gvisor` bez `runtime_class` była niespójna od początku.

### 🔴 Weryfikacja blokady na Windowsie (S po odblokowaniu)

Ścieżka `msvcrt.locking` jest napisana i nigdy nie została **wykonana**. Wymaga maszyny
z Windowsem.

### 🟢 CI sprawdza mniej niż lokalne bramki (S) — **łatwe i zaskakujące**

CLAUDE.md wymienia sześć bramek lokalnych. CI uruchamia `ruff`, `black`, `mypy`, `pytest`,
`husarz eval`, `gitleaks`, `pip-audit`, `hadolint` i `docker build` — czyli **nie uruchamia
`mkdocs build --strict` w ogóle**, a `black` sprawdza tylko `src tests`, podczas gdy bramka
lokalna obejmuje `src tests scripts`.

Skutek jest konkretny: martwy odnośnik w dokumentacji albo źle sformatowany skrypt operatora
przechodzi przez CI. Bramka opisana w CLAUDE.md jako równoprawna nie jest egzekwowana tam,
gdzie egzekwowanie ma największą wartość — u kogoś, kto nie pamięta o uruchomieniu jej
lokalnie.

### 🟢 SBOM i skan obrazu w CI (M)

CI uruchamia `pip-audit --strict` i `hadolint`. Nie generuje SBOM ani nie skanuje
zbudowanego obrazu — czyli podatność w warstwie bazowej systemu przechodzi.

### 🟡 Podpisywanie obrazów (cosign) (M)

Wymaga klucza podpisującego, czyli decyzji operatora o materiale kryptograficznym —
tej samej klasy, co decyzja o podpisywaniu commitów.

### 🟡 Podpis kodu i notaryzacja, desktop Tauri (XL)

Zapisane w ROADMAP jako zadanie operatora. Wymaga konta dewelopera i klucza.

### 🟡 GitHub Actions zatrzymane w kolejce

Przebiegi wiszą w stanie `queued` od 15–17 sierpnia. To sprawa rozliczeń albo dostępnych
runnerów po stronie konta — **nie kodu**. Do czasu odblokowania nie ma dowodu z CI, że
`pip-audit`, `hadolint` i `docker build` przechodzą, a te trzy bramki nie są dostępne
lokalnie.

### 🟡 Rozjazd z GitLabem

Gałąź główna rozeszła się: **94 commity lokalnie wobec 1 po stronie GitLaba**, którego
nie ma w historii lokalnej. Uzgodnienie wymaga wypchnięcia z nadpisaniem, czego CLAUDE.md
wprost zabrania wykonywać samodzielnie — nadpisanie kasuje tamten commit. Przygotowane
polecenie z `--force-with-lease` czeka na decyzję operatora.

---

## 11. Dokumentacja

### 🟡 Wiki — brak źródła i brak opisanej procedury (M)

CLAUDE.md wymaga utrzymywania wiki i PDF na bieżąco, ze zrzutami ekranu, z `docs/` jako
źródłem prawdy. **Te dwa wymogi mają dziś różny stan i trzeba je rozdzielić.**

**PDF jest zrobiony.** `mkdocs-print-site-plugin` generuje interaktywną wersję do druku
(okładka, spis treści z odnośnikami, zakładki) prosto z `docs/`, więc rozjazdu treści
z definicji nie ma. Wpis w `mkdocs.yml` i zależność w `pyproject.toml` są na miejscu,
a odnośnik prowadzi z [`docs/index.md`](index.md).

**Wiki nie ma źródła w repozytorium ani opisanej procedury.** Wiki GitLaba i GitHuba to
osobne repozytoria, więc brak katalogu `wiki/` sam w sobie niczego nie dowodzi — ale nie
ma też generatora ani opisu, jak wiki miałaby powstawać z `docs/`. W praktyce oznacza to,
że nikt poza operatorem nie jest w stanie jej odtworzyć ani zaktualizować, a przy każdej
zmianie w `docs/` rozjazd narasta po cichu.

Zakres pracy: skrypt przenoszący wybrane strony z `docs/` do repozytorium wiki wraz
z zasobami, z przepisaniem odnośników względnych. Publikacja na zdalne pozostaje decyzją
operatora — patrz CLAUDE.md, sekcja o synchronizacji repozytoriów.

### 🟢 Uzupełnienie zrzutów ekranu (S)

Sześć zrzutów pokrywa część zakładek. Brakuje czatu i wtyczek. Każdy nowy zrzut wymaga
przeglądu pod kątem danych wrażliwych przed dołączeniem — repozytoria są publiczne.

### 🟡 README po angielsku (M)

Ta sama uwaga, co przy i18n konsoli: decyzja produktowa, nie techniczna.

### 🟢 Przegląd pozostałych pól bez czytelnika (M)

Sesja przeglądowa znalazła sześć pól konfiguracji, które wyglądały na aktywne i nic nie
robiły. Świadomie zostawione zostały `ca_cert_ref`, `cert_ref` i pokrewne — mają czytelnika
w kodzie, ale nie na wszystkich ścieżkach (patrz bundle CA w sekcji 9). Warto do nich
wrócić z tą samą metodą.

**Uwaga metodologiczna z tamtej sesji:** automatyczny przegląd AST dał trzy fałszywe
alarmy — `telemetry_enabled` czytane przez walidator, `roe.window` czytane przez **metodę**
modelu, `roe.authorized_by` konsumowane przez `model_dump()` w ładunku podpisu. Narzędzie
szukające przypisań i odczytów po nazwie nie widzi tych trzech dróg. Kolejny przegląd musi
o tym pamiętać, inaczej powtórzy te same trzy pomyłki.

---

## Czego świadomie NIE robimy

Zapisane, żeby nie wracały jako „przeoczenia":

- **Rola „NOC" (podgląd + diagnoza)** — rozstrzygnięte: nie teraz. Panel trzech stanowisk
  ocenił rozszerzenie uprawnień przed limitem per `principal` jako niebezpieczne.
- **Pobranie silnika przez `bootstrap`** — odłożone świadomie, patrz ADR-0025.
- **Sędzia LLM** — hipoteza, nie plan; wymaga poprzedzenia pomiarem zgodności z człowiekiem.
- **Telemetria w jakiejkolwiek postaci** — sprzeczna z zasadą suwerenności danych.

## Jak utrzymywać ten dokument

Pozycja żyje tutaj, dopóki jest **możliwością**. W chwili podjęcia decyzji o realizacji
przenosi się do [ROADMAP.md](https://github.com/Gh0s777tt/Husarz/blob/main/ROADMAP.md)
jako zobowiązanie i znika stąd — inaczej
powstanie druga lista stanu, a duplikat, jak zapisano w CLAUDE.md, i tak przestanie być
aktualizowany.

Blokady sprawdzaj przy każdej rewizji. Blokada 🔴 znika, gdy pojawi się zasób (klaster,
model wizyjny, maszyna z Windowsem); blokada 🟡 znika wraz z decyzją operatora. Pozycja,
której blokada zniknęła, a nikt tego nie zauważył, jest tym samym rodzajem cichego długu,
co pole konfiguracji bez czytelnika.
