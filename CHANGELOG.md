# Changelog

Wszystkie istotne zmiany w projekcie Husarz. Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Dodane (kontrola dostarczonej konfiguracji po sesji zmian w schemacie)

- W tej sesji usunięto ze schematu **cztery pola** i dołożono **sześć odmów**. Po każdej
  takiej zmianie sprawdzałem ręcznie, czy dostarczona konfiguracja nadal się wczytuje.
  Ten wpis zamienia rytuał w kontrolę — jeśli kolejna zmiana zepsuje `config/`, zestaw
  zaczerwieni się od razu, a nie po pierwszym uruchomieniu u kogoś.
- **Wszystkie trzy profile**, nie tylko domyślny: `prod` i `airgap` mają dodatkowe wymagania,
  więc dostarczona konfiguracja mogła przestać się w nich mieścić bez żadnego sygnału
  w `dev`, w którym pracuje się na co dzień.
- **Odmowy obowiązują także w warstwie ENV.** To nie jest szczegół: w kontenerze nadpisywanie
  konfiguracji ENV-em jest DROGĄ DOMYŚLNĄ (`deploy/k8s/configmap.yaml`), więc walidacja
  działająca tylko dla YAML-a zostawiałaby obejście dokładnie tam, gdzie system pracuje
  produkcyjnie. Zweryfikowane uruchomieniem dla czterech zmiennych.
- **Żaden plik w `config/` nie może być OSIEROCONY.** Wykryte kontrolą nośności: usunięcie
  jednego wpisu z mapy loadera nie psuło niczego widocznego — konfiguracja ładowała się
  dalej z wartościami domyślnymi, a plik stawał się dekoracją. To ta sama klasa co pola bez
  czytelnika, tylko o piętro wyżej: tam martwe było POLE, tu martwy bywa CAŁY PLIK.
- Weryfikacja end-to-end po całej sesji: platforma startuje, `GET /api/health`,
  `/api/config/summary` i `/api/doctor` odpowiadają, limit tempa działa (6× 200, potem 429),
  a deklarowany silnik sandboxa zgadza się z tym, co trafia do `docker run`.

### Zmienione (deklarowany silnik sandboxa musi odpowiadać rzeczywistości)

- **`security.sandbox.engine` nie sterował NICZYM.** `build_tools` zawsze buduje executor
  Dockera, a o gVisorze decyduje wyłącznie `runtime_class` (trafia do `docker run --runtime`).
  Sprawdzone na czterech kombinacjach: `none` też uruchamiał kontener, a `docker+gvisor`
  z pustym `runtime_class` dawał zwykły runc.
- To ostatnie jest najgroźniejsze, bo `engine` **jest pokazywany operatorowi** — w linii
  startowej CLI i w `GET /api/config/summary`. Konfiguracja mogła więc meldować gVisora,
  gdy kontener biegł na runc: fałszywe zapewnienie o SILE izolacji.
- Walidacja pilnuje teraz pary: `docker+gvisor` **wymaga** `runtime_class`, samo `docker`
  go **zabrania**. `none` odrzucane w KAŻDYM profilu (nie tylko prod/airgap): wyłączenia
  izolacji w tym kodzie nie ma i świadomie go nie dodajemy — byłoby poszerzeniem powierzchni
  ataku, a nie naprawą. `firecracker` odrzucany jako niezaimplementowany.
- **Domyślna wartość była niespójna od początku** (`docker+gvisor` przy `runtime_class: None`,
  czyli deklaracja gVisora przy zachowaniu runc). Wyszło dopiero wtedy, gdy nowy walidator
  odrzucił WŁASNĄ wartość domyślną — najuczciwszy możliwy sygnał. Domyślnie jest teraz
  `docker`; zachowanie nie zmieniło się o nic, zmieniła się prawdziwość deklaracji.
- **Usunięta martwa gałąź** w bazowej linii profili: sprawdzenie `engine is NONE` stało się
  nieosiągalne, bo walidator pola jest ściśle silniejszy (odrzuca we wszystkich profilach,
  a rewalidacja zagnieżdżonego modelu nie pozwala obejść go przez `model_copy` — sprawdzone).
  Zostawienie jej „na wszelki wypadek" byłoby tym samym, co pola usunięte wcześniej: kodem,
  który wygląda na działającą kontrolę.

### Poprawione (awaria sandboxa wywracała pętlę zamiast dać modelowi błąd)

- **`ToolDispatcher.dispatch` łapał trzy zaplecza z czterech.** `MemoryError_`,
  `EgressError` i `PluginError` degradowały się do `ToolResult(ok=False)`; `SandboxError`
  przechodził na wylot. Skutek: `security.sandbox.image: null` albo niedostępny silnik
  wywracał CAŁĄ pętlę narzędziową i orkiestrację — za błąd konfiguracji płaciła przerwana
  praca, a nie jedno nieudane wywołanie. Odtworzone na realnych narzędziach: ani `ShellTool`,
  ani `RunTestsTool` nie łapią tego same.
- Łapiemy teraz **całą hierarchię `ToolError`**, nie wyliczankę klas. Wyliczanka już raz
  zawiodła; dodanie piątego rodzaju narzędzia nie może wymagać pamiętania o dopisaniu jego
  wyjątku do `except`. Test przechodzi po WSZYSTKICH podklasach `ToolError` — nowa podklasa
  bez pokrycia zaczerwieni go sama z siebie.
- Sprawdzone, że komunikaty tych wyjątków są bezpieczne do pokazania modelowi: `FetchError`
  jest z założenia generyczny, `PathNotAllowedError` echuje wyłącznie ścieżkę podaną przez
  model, `SandboxError` mówi o konfiguracji operatora, nie o jego danych.
- **SPROSTOWANIE do własnego opisu tej pozycji w ROADMAP.** Zapowiadałem ją jako „złamanie
  kontraktu z docstringa". Docstring był węższy, niż go zacytowałem: obiecywał brak wyjątku
  dla ZŁYCH ARGUMENTÓW, nie dla awarii zaplecza. Wada była realna (trzy zaplecza obsłużone,
  czwarte nie, bez uzasadnienia), ale jej pierwotny opis — nieprecyzyjny. Docstring
  rozszerzony tak, żeby mówił to, co kod teraz robi.

### Dodane (bazowa linia profili prod/airgap — obietnica bez ani jednego testu)

- **`docs/ARCHITEKTURA.md` obiecuje, że w profilach `prod` i `airgap` sandboxa, audytu
  i szyfrowania „nie można cicho wyłączyć". Walidacja tego pilnuje — nie pilnował tego
  ŻADEN test.** Gdyby ktoś przy refaktorze usunął ten blok, cały zestaw pozostałby zielony,
  a obietnica zamieniłaby się w nieprawdę bez sygnału.
- Dopisane testy SKUTKU (czy konfiguracja daje się wczytać), parametryzowane po czterech
  wymaganiach i obu profilach — jeden test na „bazową linię" przechodziłby dalej, gdyby ktoś
  usunął trzy z czterech warunków.
- Dwie kontrole w drugą stronę: profil `dev` zachowuje elastyczność (inaczej walidator
  odrzucający wszystko wyglądałby na poprawny) oraz dostarczona konfiguracja daje się
  podnieść do `prod` bez zmian w plikach.

### Poprawione (przegląd testów „wartość zamiast skutku" — wynik w większości negatywny)

- Podejrzenie z Etapu 17m sprawdzone co do jednego. **Cztery z pięciu podejrzanych testów
  mają dowód SKUTKU gdzie indziej** (telemetria, sandbox bez sieci, szyfrowanie at-rest,
  ROE Puszkarza). Asercje o wartościach zostają — mówią coś prawdziwego o DOSTARCZONEJ
  konfiguracji — ale dostały w docstringach odnośnik do miejsca, gdzie stoi dowód skutku,
  żeby macierz w `docs/BEZPIECZENSTWO.md` nie odsyłała do najsłabszego dowodu.

### Dodane (audyt: klucz HMAC domyka przekucie łańcucha)

- **`security.audit.hmac_key_ref`.** Docstring `AuditLog` zalecał klucz HMAC „w produkcji"
  od dawna, ale `AuditConfig` nie miało pola na klucz, a `build_audit_log` go nie przekazywało
  — **zalecenie było nieosiągalne z konfiguracji**. Rada, której nie da się wykonać, jest
  gorsza niż jej brak.
- Kotwica (poprzedni wpis) wykrywa usunięcie wpisów; **nie wykrywa przekucia całego łańcucha**.
  Kto ma prawo zapisu, może przeliczyć goły SHA-256 od nowa i nadpisać kotwicę. Odtworzone:
  podrobiony dziennik jest wewnętrznie spójny, a mimo to start z kluczem zostaje odmówiony.
- **Wyłącznie referencja ZEWNĘTRZNA** (`env:`/`file:`/`vault:`/`sops:`). Schemat `husarz:`
  zabroniony: klucz integralności audytu nie może pochodzić z magazynu należącego do systemu,
  którego dziennik ma pilnować.
- **Start fail-closed przy włączonym HMAC**: dziennik nieweryfikujący się kluczem blokuje
  uruchomienie, bo nie da się odróżnić „plik sprzed HMAC" od „ktoś bez klucza przepisał
  historię". Komunikat podaje wyjście: zarchiwizować stary dziennik wraz z kotwicą.
- **Brak dostawcy sekretów albo nierozwiązywalna referencja → odmowa, nie cicha praca.**
  Ciche przejście w tryb bez klucza zamieniłoby zabezpieczenie w jego pozór.
- Bez klucza zachowanie BEZ ZMIAN — uszkodzenie widać jako `verified: false`, start się nie
  wywraca. Świadome rozróżnienie: konfiguracja klucza jest deklaracją „integralność jest
  blokująca".

### Poprawione (audyt: odcięcie ogona przestało być niewykrywalne)

- **Łańcuch skrótów wykrywał EDYCJĘ wpisu, ale nie USUNIĘCIE końcówki.** Pozostały prefiks
  jest wewnętrznie spójny, więc `verify()` meldowało „brak manipulacji" na dzienniku,
  z którego wpisy zniknęły. Zmierzone: 5 wpisów → usunięto 2 → `verify(): True`. Dla
  dziennika opisywanego jako „niemodyfikowalny" to luka istotna, bo najłatwiejszym sposobem
  zatarcia śladu jest właśnie usunięcie końcówki.
- **Kotwica** obok dziennika: liczba wpisów **i skrót** ostatniego. Sam licznik dałoby się
  obejść (usunąć końcówkę i dopisać własną o tej samej długości) — ma to osobny test.
- Kolejność zapisu jest częścią projektu: wpis najpierw, kotwica potem. Odwrotna zostawiałaby
  po zaniku zasilania kotwicę wskazującą na wpis, którego nie ma — fałszywy alarm manipulacji
  po zwykłej awarii.
- **Uszkodzona kotwica NIE unieważnia dziennika**, a błąd jej zapisu nie przerywa audytu.
  Fałszywy alarm w mechanizmie ostrzegawczym jest kosztowny: operator, który raz zobaczy
  „łańcuch USZKODZONY" bez powodu, nie zareaguje, gdy komunikat będzie prawdziwy.
- **SPROSTOWANIE:** `README.md` i `docs/ARCHITEKTURA.md` mówiły „niemodyfikowalny audit log"
  bez zastrzeżenia. Poprawione na precyzyjne: dopisujący, z łańcuchem skrótów i kotwicą.
  README przestał też obiecywać mTLS i OIDC jako działające.
- Ograniczenie zapisane wprost: kto ma prawo zapisu do katalogu, ma je też do kotwicy.
  Prawdziwym domknięciem jest `hmac_key` spoza systemu plików — pozycja otwarta w ROADMAP.

### Usunięte / zmienione (przeszukanie systematyczne: pola konfiguracji, które KŁAMAŁY)

Trzy kolejne commity naprawiały pole „wygląda na działające, nie robi nic". CLAUDE.md nakazuje
przy poprawce WZORCA przeszukać repozytorium — przeszukanie wykazało, że **na 155 pól schematu
siedemnaście nie miało odwołania poza `schema.py`**. Dochodzenie per pole, z wymogiem
rozstrzygnięcia uruchomieniem i adwersaryjną weryfikacją każdego werdyktu „dziura".

**Potwierdzonych dziur w bezpieczeństwie: ZERO.** Ochrony, które wyglądały na warunkowe,
okazały się bezwarunkowe. Naprawiono natomiast sześć pól, które o tym kłamały:

- **`tools.*.requires_sandbox` USUNIĘTE — najgorsze z listy.** `config/tools/web.yaml`
  i `file_edit.yaml` deklarowały `requires_sandbox: true`, a **oba narzędzia działają
  w procesie Husarza**, nie w kontenerze (sprawdzone: żaden z modułów nie woła executora).
  Operator czytający te pliki miał pełne prawo sądzić, że ruch wychodzący idzie z izolowanego
  kontenera. Izolacja nie była naruszona — naruszona była prawda o tym, gdzie przebiega.
- **`security.sandbox.workspace_only` i `path_allowlist` USUNIĘTE.** Obiecywały
  konfigurowalność ograniczeń plikowych, której nie było: kontener dostaje DOKŁADNIE jeden
  montaż, a dopisanie ścieżki do `path_allowlist` nie dawało do niej dostępu.
- **`routing.cost_controls.max_cost_per_task` ODRZUCANE.** Pomyłka szczególnie prawdopodobna,
  bo oba sąsiednie limity w tym samym bloku DZIAŁAJĄ. Ten nie działał.
- **`security.mtls.enabled: true` i `security.auth.oidc_enabled: true` ODRZUCANE.**
  To nie było zwykłe „pole nic nie robi": konfiguracja z włączonym mTLS **startowała**,
  a API nasłuchiwało po zwykłym HTTP — token Bearer szedł jawnym tekstem.
- **`docs/NARZEDZIA.md` dostał tabelę, czego dotąd nie było nigdzie:** które narzędzia
  naprawdę biegną w kontenerze (`shell`, `git`, `run_tests`), a które w procesie Husarza —
  i co chroni te drugie. Usunięcie fałszywej informacji bez podania prawdziwej byłoby połową
  roboty.

### Poprawione (test, który utrwalał złudzenie)

- `test_sandbox_has_no_network_by_default` asercjonował `sandbox.workspace_only is True` —
  **wartość pola, którego nic nie czytało**. Zielony test przy martwym polu jest gorszy niż
  brak testu: utrwala przekonanie, że ograniczenie zostało sprawdzone. Zastąpiony testem
  SKUTKU (kontener dostaje dokładnie jeden montaż, i jest nim katalog roboczy), z asercją na
  LICZBIE montaży — inaczej przeszedłby także po dodaniu kolejnego bind-mounta hosta.

### Sprostowane (fałszywe alarmy mojego własnego przeszukania)

- **`platform.telemetry_enabled`, `roe.*.window`, `roe.*.authorized_by` NIE SĄ martwe** —
  mój grep tego nie widział. Pierwsze jest czytane przez walidator w samym `schema.py`
  (zabrania wartości `true`), drugie przez METODĘ modelu (`is_active_at` — **okno ROE jest
  w pełni egzekwowane**), trzecie wchodzi do payloadu podpisu przez `model_dump()`.
- Wniosek metodyczny zapisany w `docs/BEZPIECZENSTWO.md`: „nazwa pola nie występuje poza
  schematem" NIE jest równoważne „pole jest martwe".

### Zmienione (konfiguracja odrzuca `routing.strategy`, którego router nie realizuje)

- **`routing.strategy: cost` / `latency` nie robiło NIC.** `selection.py` nie czyta tego pola
  ani razu (sprawdzone przeszukaniem `src/`), więc operator ustawiał politykę doboru modelu
  po koszcie i dostawał po cichu zachowanie `tags`. Ta sama klasa co usunięte `weights_path`,
  tylko gorsza: nazwa obiecuje POLITYKĘ, a nie ścieżkę.
- Konfiguracja z taką wartością **nie wczyta się**, a komunikat mówi, co się stało, co
  ustawić zamiast i dlaczego strategie nie działają (brak danych o cenie i opóźnieniu
  w `models.registry` — to właściwy powód, nie przeoczenie w routerze).
- **SPROSTOWANIE w `docs/ROUTER.md`:** akapit nazywał je „placeholderami" i na tym
  poprzestawał. Były jednak placeholderami PRZYJMOWANYMI. Uczciwa uwaga w dokumentacji to
  najsłabsza z możliwych kontroli — nie czyta jej ten, kto edytuje YAML.
- Nowy test wymusza rozstrzygnięcie statusu KAŻDEJ wartości enuma: dopisanie kolejnej
  strategii musi skończyć się albo implementacją, albo świadomym odrzuceniem — nie cichym
  placeholderem.

### Poprawione (budżet kontekstu: obraz przestał kosztować ZERO)

- **Obrazy nie były liczone w budżecie okna kontekstu.** `ChatMessage.images` idzie do modelu
  wizyjnego tak samo jak treść, a bramka budżetu udawała, że nic nie waży — czyli meldowała
  „mieści się" dla żądania, które model odrzuci albo po cichu utnie kontekst. Bramka zawodziła
  dokładnie w przypadku, dla którego istnieje.
- Obraz kosztuje teraz **stałą, celowo wysoką liczbę tokenów**. To **nie jest pomiar i nie
  udaje pomiaru** — to zaokrąglenie „nie wiem" w stronę, która nie kłamie. Konstrukcja stałej
  jest zapisana wprost w kodzie i w `docs/ROUTER.md`: obraz ma kosztować nie mniej niż strona
  gęstego tekstu, bo model, dla którego byłby tańszy, i tak zmieści się z zapasem.
- **SPROSTOWANIE w `docs/ROUTER.md`:** akapit „Obrazy NIE są liczone" opisywał stan zgodnie
  z prawdą, ale przedstawiał go jako znane ograniczenie, a nie jako wadę do naprawy. Zero
  kosztu to nie jest neutralne „nie liczymy" — to aktywnie fałszywe „nic nie kosztuje".
- Kalibracja POMIAREM wymaga modelu wizyjnego (`prompt_eval_count`, jak dla tekstu). Tu
  takiego nie ma, więc jej nie wykonano — i nie udajemy, że wykonano. ROADMAP.

### Sprostowane (uzasadnienie granicy `diagnostics:read` było niepełne)

- **`viewer` nadal bez `diagnostics:read` — ale z INNEGO powodu, niż zapisano.** Uzasadnienie
  „podgląd nie wysyła pakietów" przestało obowiązywać wraz z limitem tempa: sufit ruchu
  wychodzącego jest ten sam bez względu na to, ile ról ma uprawnienie. Granica stoi dziś
  wyłącznie na UJAWNIENIU aktualnej topologii — adresy i porty silników, ścieżki katalogów
  operatora, katalog silnika (nazwy modeli spoza konfiguracji Husarza) — i to na ścieżce
  SZCZĘŚLIWEJ, nie tylko przy awarii. Sprostowane w `rbac.py`, `docs/API.md`,
  `docs/LAUNCHER.md` i w notatce `docs/BEZPIECZENSTWO.md` (Etap 17l).
- **Decyzja poddana panelowi trzech stanowisk** (rozszerzyć `viewer` / rola `noc` / zostawić),
  każde ocenione przez trzy soczewki. Wynik: status quo 7,3/10, rola `noc` 7,0/10,
  rozszerzenie `viewer` **4,7/10 i ocenione jako niebezpieczne**. Wniosek był przeciwny do
  mojej wyjściowej skłonności.
- **Nowy, twardy warunek wstępny** dla jakiegokolwiek rozszerzenia `diagnostics:read`:
  kubełek limitu per `principal` z rezerwą dla operatora. Limit jest globalny, więc konto
  podglądowe odpytujące w pętli odbiera diagnozę operatorowi w trakcie awarii — limit usunął
  argument o amplifikacji i stworzył argument o wygłodzeniu.

### Dodane / poprawione (konsola i konfiguracja diagnozy)

- **`config/security.yaml` dostał sekcję `diagnostics:`** z komentarzem. Klucza tam nie było,
  więc jedyne zabezpieczenie tego endpointu było niewidoczne dla operatora czytającego pliki
  konfiguracji — a zabezpieczenie, o którym nikt nie wie, nie zostanie ani dostrojone, ani
  świadomie wyłączone.
- **Wejście w zakładkę „Diagnoza" NIE odpala już sondowania.** Kliknięcie w nawigacji nie jest
  świadomym żądaniem wysłania pakietów, a limit jest wspólny dla instalacji. Panel pokazuje
  ostatni wynik; świeży wymaga kliknięcia „Sprawdź ponownie".
- **Wynik niesie znacznik czasu** („stan z 14:32:05"). Pokazujemy stan sprzed chwili, więc
  operator ma wiedzieć, sprzed której — stary pomiar udający bieżący to ta sama klasa
  nieprawdy, co zaokrąglanie „nie wiem" do „w porządku".
- **`test_rola_viewer_NIE_widzi_diagnozy` sprawdza SKUTEK, nie deklarację** — liczy zapytania
  sondy i wymaga zera. Sam kod 403 przechodziłby także wtedy, gdyby bramka RBAC stała ZA
  sondowaniem, czyli gdyby odmowa przychodziła po wygenerowaniu ruchu.
- **SPROSTOWANIE własnej pomyłki:** twierdziłem, że `viewer` widzi PEŁNY dziennik audytu.
  Nieprawda — `api/audit_view.py` działa deny-by-default i przepuszcza wąską allowlistę pól.
  Asymetria, na którą się powoływałem, jest mniejsza, niż napisałem.

### Poprawione (wdrożenie: obraz przypięty, wersja pilnowana testem)

- **`deploy/k8s/deployment.yaml` używał `husarz-api:latest`.** W parze
  z `imagePullPolicy: IfNotPresent` to najgorsze możliwe połączenie: węzeł trzyma obraz,
  który pobrał jako pierwszy, więc wdrożenie nie jest ani odtwarzalne, ani aktualizowalne.
  Przypięte do wersji.
- **Kontrola przypięcia obejmowała tylko compose.** Test `test_compose_images_are_pinned_
  not_latest` istniał od dawna i sprawdzał JEDNĄ z dwóch powierzchni wdrożenia — przez co
  manifest k8s przechodził przez lukę w zabezpieczeniu, które powstało dokładnie po to.
  Zabezpieczenie zastosowane do połowy przypadków jest gorsze niż jego brak, bo usypia.
- **Nowy test spójności wersji** we wszystkich czterech miejscach wymienionych w CLAUDE.md
  (`pyproject.toml`, `husarz.__version__`, `deploy/compose`, `deploy/k8s`). Do tej pory
  pilnowała ich wyłącznie czyjaś pamięć przy wydaniu — a `deploy/k8s` i tak z niej wypadł.
- `docs/DEPLOY.md` budował obrazy jako `:latest` wbrew tej samej zasadzie — poprawione.

### Dodane (limit tempa dla `GET /api/doctor`)

- **`security.diagnostics.max_requests_per_minute`** (domyślnie 6/min). Każde wywołanie
  diagnozy otwiera połączenia wychodzące do endpointów z konfiguracji, więc bez limitu
  uprawnienie `diagnostics:read` było dźwignią: żądanie tanie dla wywołującego, kosztowne
  dla instalacji i dla silników, do których Husarz się odzywa. Limit dobrany pod CZŁOWIEKA
  klikającego „Sprawdź ponownie" (jedno na dziesięć sekund), nie pod automat.
- **Limit sprawdzany PRZED sondowaniem**, nie po. To jest cała treść zabezpieczenia: 429
  zwrócone po odpytaniu silników nie zmniejszyłoby ani jednego pakietu. Ma osobny test.
- **Ogranicznik budowany RAZ, z konfiguracji startowej.** Przebudowa przy nadpisaniu
  konfiguracji zerowałaby kubełek, więc `POST /api/config/runtime` przeplatany diagnozą
  byłby obejściem limitu. Też ma test.
- `None` wyłącza limit — świadoma REZYGNACJA z zabezpieczenia (np. instalacja jednoosobowa
  na loopbacku), nie jego brak.
- **Konsola odróżnia 429 od awarii**: pokazuje ostrzeżenie i **nie kasuje** poprzedniego
  wyniku, bo ten jest nadal prawdziwy — diagnoza tylko odmówiła kolejnego przebiegu.

### Poprawione (diagnoza odróżnia problem blokujący od ostrzeżenia — w OBU nośnikach)

- **Terminal:** `[!!]` blokujący, **`[! ]` ostrzeżenie**, `[??]` nie wiadomo, `[ok]` w porządku.
  Dotąd oba rodzaje problemu miały identyczne `[!!]`. Dopóki ostrzeżenia były rzadkie,
  uchodziło to płazem; od czasu diagnozowania modeli zapasowych (niedziałający zapas to
  ostrzeżenie, nie blokada) mieszana lista zdarza się regularnie — a gdy wszystko krzyczy
  tak samo głośno, nic nie jest pilne. Znaczniki mają stałą szerokość, żeby kolumna
  identyfikatorów była wyrównana.
- **Konsola:** mapa znaków kluczowana na STANIE **i WADZE**, nie na samym stanie. Kolor
  błędu (czerwony ✕) jest teraz zarezerwowany dla problemu blokującego; ostrzeżenie dostaje
  bursztynowy `!`.
- **Legenda tylko wtedy, gdy jest co odróżniać** — przy liście zawierającej oba rodzaje.
  Stała legenda przy każdym uruchomieniu byłaby szumem.
- Poprawka objęła oba nośniki NARAZ i ma test pilnujący, że **odróżniają te same przypadki**:
  rozjazd w ocenie tej samej instalacji jest tu groźniejszy niż sam wygląd. Zrzut ekranu
  w dokumentacji odświeżony.

### Usunięte / poprawione (dwie nieprawdy w dostarczonej konfiguracji)

- **`models.registry[...].weights_path` USUNIĘTE.** Pole żyło w schemacie i **nic go nie
  czytało** — jedyne wystąpienie w całym repozytorium było w jego własnej definicji. Wyglądało
  przy tym, jakby wskazywało silnikowi lokalne wagi (nazwa, typ `Path`, komentarz „ścieżka do
  lokalnych wag"), więc operator mógł je ustawić i uwierzyć, że coś z tego wynika. Martwe pole
  udające działające jest gorsze niż jego brak.

  Konfiguracja używająca tego pola **nie wczyta się** — świadomie, z czytelnym komunikatem
  wyjaśniającym, co zniknęło i dlaczego, zamiast generycznego „extra fields not permitted".
  Sam komunikat od razu się przydał: wyłapał dwa wystąpienia w `config/models.yaml`, których
  przeszukanie `src/` nie widziało.
- **Rozbieżność wersji Bielika rozstrzygnięta — pomiarem, nie wyborem wersji.** ROADMAP
  zapisywał ją jako „nie da się rozstrzygnąć bez sięgnięcia do rejestru"; `husarz bootstrap`
  dał do tego narzędzie. Odpytany rejestr Ollamy nie zna **ani** `bielik-11b-v3.0-instruct`
  (z `config/models.yaml`), **ani** `SpeakLeash/bielik-11b-v2.3-instruct` (z
  `ollama/Husarz.Modelfile`). Problemem nie była więc zła wersja, tylko to, że **oba pliki
  obiecywały model, którego żadną udokumentowaną drogą nie da się zdobyć** — i każdy obiecywał
  inaczej. Oba mówią teraz to samo i prawdę: model dostarcza operator, a dopóki tego nie zrobi,
  `husarz doctor` słusznie zgłasza brak.

### Dodane (Etap 12f — `husarz bootstrap`: pobranie wag za zgodą operatora)

- **Pętla diagnostyczna domknięta.** `husarz doctor` mówi „NIE MA modelu X",
  `husarz bootstrap` proponuje go pobrać, `husarz doctor --probe` potwierdza skutek.
  Braki ustala JEDNA funkcja (`brakujace_modele`), więc bootstrap nie może zaproponować
  pobrania modelu, o którym diagnoza mówi „jest".
- **Pobieramy WAGI, nie SILNIK.** Wagi ściąga silnik operatora (`POST /api/pull`); Husarz
  o to prosi i pilnuje zgody. Nie dotykamy cudzego kodu wykonywalnego, sum kontrolnych
  binarek ani ścieżek instalacyjnych per system — instalacja silnika należy do menedżera
  pakietów. Decyzja i odrzucone alternatywy: [ADR-0025](docs/adr/0025-pobieranie-wag-za-zgoda.md).
- **Rozmiar PRZED pobraniem, z manifestu.** Ekran zgody podający GB byłby fikcją, gdyby
  liczbę poznawać ze strumienia — bajty już by leciały. Manifest to zmierzone **857 bajtów**
  metadanych dla `qwen2.5-coder:1.5b`, z których wynika dokładne 0,986 GB. Model o nieustalonym
  rozmiarze jest POKAZANY z powodem, ale nie pobierany.
- **Domyślna odpowiedź to odmowa**, a brak terminala (potok, usługa) też znaczy „nie".
  `--yes` istnieje dla skryptów i jej użycie JEST zgodą.
- **Dwie allowlisty, nie jedna.** `bootstrap.sources` jest osobne od
  `security.egress.allowlist`: zgoda na odczyt rozmiaru z rejestru nie może otwierać tej
  domeny narzędziu `web`, wtyczkom ani agentom — i odwrotnie. Zapytanie o manifest przechodzi
  dodatkowo pin IP (ADR-0020) z zakazem loopbacku i LAN-u.
- **Profil `airgap`: twarda odmowa**, sprawdzana PRZED włącznikiem — operator ma usłyszeć,
  że zabrania profil, a nie że „wystarczy włączyć bootstrap".
- Nowa sekcja konfiguracji `config/bootstrap.yaml` (domyślnie `enabled: false`). Włączenie
  bez `registry` albo bez `sources` jest błędem walidacji, a nie atrapą, która odmówi później.
- Testy: +16 (dopuszczalność, rozmiar przed pobraniem, rozdzielenie allowlist, zgoda).
  Nośność: 12 mutacji, 12 czerwonych.

### Dodane (Etap 12e — diagnoza obejmuje modele ZAPASOWE)

- **Łańcuchy `fallback` są diagnozowane.** Model zapasowy przejmuje ruch, gdy główny padnie,
  więc diagnoza milcząca na jego temat milczała o ratunku — a `docs/LAUNCHER.md` twierdziła
  przy tym, że sprawdzany jest „CAŁY łańcuch". Przechodzimy je tak samo jak router: **
  rekurencyjnie** (zapas zapasu też jest osiągalny), z ochroną przed cyklem i tylko gdy
  `routing.fallbacks_enabled`. Etykieta mówi, czyim zapasem model jest: `zapasowy dla 'glowny'`.
- **Modele z `routing.rules[].prefer`** też są diagnozowane — to jawne przypisanie operatora.
- **Nowa waga dla zapasów: ostrzeżenie, nie blokada.** Model pełniący WYŁĄCZNIE rolę zapasową
  nie obsługuje dziś ruchu, więc jego awaria nie przerywa pracy. Ma to skutek praktyczny:
  `husarz doctor` zwraca kod 1 tylko przy problemie blokującym, a komenda bywa w skrypcie
  startowym — zrównanie zepsutego zapasu z martwym modelem czatu zatrzymywałoby uruchomienie
  DZIAŁAJĄCEJ instalacji. Model wskazany i jako zapasowy, i w roli głównej pozostaje blokujący.
- Ograniczenie zapisane wprost: modele wybierane wyłącznie przez **dopasowanie tagów** nie są
  diagnozowane — ten warunek spełnia dowolny włączony model z pasującym tagiem, więc diagnoza
  objęłaby cały rejestr, łącznie z modelami trzymanymi świadomie nieużywanymi.

### Poprawione (Etap 12e)

- **`agent_models: {x: auto}` dawało zmyślony problem blokujący.** `auto` znaczy „wybierz sam"
  i jest poprawną wartością (schemat ją dopuszcza, `resolve_agent_model` ją obsługuje), ale
  diagnoza brała ją za identyfikator modelu i zgłaszała „Model 'auto' nie istnieje w rejestrze
  modeli" — problem, którego operator nie miałby jak naprawić. Wada sprzed tej zmiany,
  zauważona przy rozszerzaniu mapy ról.

### Poprawione (higiena repozytorium — `git fsck` znów czytelny)

- **`scripts/clean_sidecars.py --include-git`.** Dysk z repozytorium odłączył się w trakcie
  pracy. Pierwsze, po co się wtedy sięga, to `git fsck` — a ten zwrócił **539 linii błędów**
  (`badRefName`, „zły plik SHA-1"), wszystkie o plikach `._*` w `.git`. Narzędzie do
  sprawdzania spójności przestawało być czytelne dokładnie w sytuacji, dla której istnieje.
  Po sprzątnięciu 554 sidecarów `fsck` jest czysty: zero błędów, 27 wiszących blobów
  (normalne). **Repozytorium przetrwało odłączenie bez uszkodzeń.**
- Katalog `objects/pack` pozostaje wyłączony ze sprzątania — nie „na wszelki wypadek", lecz
  dlatego, że git zarządza nim sam (zmierzone: `count-objects` zalicza taki plik do `garbage`,
  a `gc` go usuwa). Wyłączenie sprawdza PARĘ sąsiadujących segmentów ścieżki, więc
  `assets/pack/._logo.png` w drzewie projektu nadal jest sprzątane.
- **SPROSTOWANIE.** Skrypt pomijał dotąd `.git` z uzasadnieniem, że kasowanie sidecarów
  „potrafi uszkodzić indeks paczek (obserwowane: `error: non-monotonic index` po skasowaniu
  `._pack-*.idx`)". Nie udało się tego odtworzyć, a pomiar pokazuje zależność ODWROTNĄ: to
  OBECNOŚĆ takiego pliku sprawia, że git zgłasza `warning: no corresponding .pack`, po czym
  `gc` sam go usuwa. Prawdopodobnie plik zniknął z ręki gita, a skutek przypisano skasowaniu.

### Dodane (Etap 12d — `husarz doctor --probe`: jedyna kontrola SKUTKU)

- **Sonda głęboka.** Dotąd diagnoza pytała silnik, czy WYMIENIA model w katalogu — to
  deklaracja. `husarz doctor --probe` wysyła do modelu prawdziwe żądanie uzupełnienia.
  Realny przypadek, który kontrola katalogu przepuszczała: endpoint bez `/v1` odpowiada na
  `GET /api/tags` (więc katalog się zgadza), ale `POST /chat/completions` daje 404 i czat
  zwraca 502. Sonda to wyłapuje i podaje przyczynę.
- **Opt-in STRUKTURALNY, nie flaga.** Sonda to osobny protokół (`SondaGleboka`) — bez
  przekazanego obiektu diagnoza nie ma czym zapytać modelu. Flaga logiczna działałaby tylko
  dopóty, dopóki nikt jej nie przeoczy, a kontrola ma skutki uboczne: wczytuje wagi do
  pamięci. Zmierzone na modelu 7B: **18,9 s przy zimnym starcie, 0,9 s zaraz potem**.
- **Ta sama droga, co czat.** Sonda używa `build_client` z routera: ten sam klient, ten sam
  pin IP (ADR-0020), to samo rozwiązywanie `api_key_ref`, ta sama bramka egress. Świadomie
  NIE używa `ModelRouter` — router ma fallbacki, więc przy modelu, który nie odpowiada,
  dostalibyśmy odpowiedź z INNEGO modelu i uznali ją za dowód sprawności tego.
- **Dwanaście kategorii przyczyny**, każda z własną instrukcją naprawy: timeout,
  uwierzytelnienie, brak endpointu, błąd silnika, rozwiązanie nazwy (pin IP), zła odpowiedź,
  brak sekretu, egress, budżet sondy i inne. Komunikat transportu jest celowo generyczny, więc
  diagnoza sięga po przyczynę przez `__cause__` — ale **nigdy nie przepisuje jej treści**
  do wyjścia, tylko mapuje na kategorię.
- `--probe-timeout` (domyślnie 60 s, wartość ≥ 1). Limit sondy nigdy nie jest NIŻSZY od
  produkcyjnego: model z `request_timeout_seconds: 120` dostaje 120 s, choćby flaga mówiła 60.
- Ślad postępu podczas sondowania — narzędzie do diagnozowania zawieszeń nie może samo
  wyglądać na zawieszone.
- **Sonda głęboka jest ŚWIADOMIE poza `GET /api/doctor`** — wczytywanie wag na żądanie HTTP
  byłoby dźwignią do wyczerpania zasobów. Niezmiennik ma test SKUTKU, nie deklarację w docs.
  Decyzje: [ADR-0024](docs/adr/0024-sonda-gleboka-diagnozy.md).

### Poprawione (Etap 12d — 13 wad z przeglądu adwersaryjnego, wszystkie w kodzie tej zmiany)

Cztery niezależne perspektywy, każde zgłoszenie weryfikowane przez agenta mającego je OBALIĆ.
**Z 36 zgłoszeń 33 potwierdzono uruchomieniem, 3 obalono** — po odjęciu duplikatów 13 wad,
wszystkie przy komplecie zielonych testów. Pełny opis: `docs/BEZPIECZENSTWO.md`, sekcja 17i.

- **Fałszywe OK dla modelu wolniejszego, niż czat czeka.** Kontrola porównywała czas
  z `request_timeout_seconds`, a to pole jest `None` w KAŻDYM modelu dostarczonej
  konfiguracji — i `None` nie znaczy „bez limitu", tylko `DEFAULT_TIMEOUT_SECONDS = 60`.
  Warunek nie odpalał się nigdy, a droga do fałszywego OK prowadziła przez radę SAMEGO
  narzędzia („powtórz z dłuższym `--probe-timeout`").
- **Wstrzyknięcie ANSI z odpowiedzi modelu.** Treść modelu szła na terminal po samym
  spłaszczeniu białych znaków, a `\x1b[2J\x1b[H` białych znaków nie zawiera — model mógł
  wyczyścić ekran i domalować własne „[ok] wszystkie kontrole przeszły" nad wypisanymi
  problemami. Usuwamy znaki kategorii Unicode `C*`.
- **`backend: mock` dawał „ODPOWIEDZIAŁ" w kilka mikrosekund** — czyli jedyna kontrola skutku
  nie sprawdzała żadnego skutku. Mock jest pomijany ze stanem NIEZNANY i wyjaśnieniem.
- **Blokada anty-SSRF raportowana jako „zły format odpowiedzi"** — `EgressError` nie jest
  wyjątkiem httpx, więc wpadał do złej kategorii i operator dostawał radę o zgodności
  z OpenAI zamiast o DNS.
- **`--probe-timeout` bez walidacji**: `model_copy(update=...)` OMIJA `ge=1` ze schematu, więc
  `0` docierało do klienta i diagnozowało sprawny silnik jako awarię.
- **Sonda obcinała limit PONIŻEJ produkcyjnego** — fałszywy timeout dla modelu, na który
  router by poczekał.
- **Opis mówił „silnik nie odpowiedział" tam, gdzie nic nie wysłano** (egress, brak sekretu,
  brak endpointu) — ta sama klasa błędu, którą naprawiono wcześniej dla kontroli katalogu.
- **Sonda strzelała do modelu `enabled: false`**, obok ustalenia, że jest wyłączony.
- **Pusta odpowiedź przy `finish_reason: length` obwiniana na model** — to skutek NASZEGO
  limitu 32 tokenów sondy, więc stan NIEZNANY z wyjaśnieniem, nie problem blokujący.
- **Nieoczekiwany wyjątek z `build_client` wywracał CAŁĄ diagnozę.** Komentarz „jedyny powód,
  dla którego build_client zawodzi" był nieuprawniony — fabryka woła kod dostawcy sekretów.
- **Kill-switch `security.secret_store.enabled` obchodzony przez sondę** (domyślne
  `magazyn_dostepny=True`).
- **Brak endpointu kategoryzowany jako „404 z endpointu"** — instrukcja twierdziła, że
  endpoint odpowiedział, choć nic nie wysłano.
- **`docs/LAUNCHER.md` twierdził nieprawdę** („nie wysyła żądania do modelu") — sprostowane.

### Poprawione (Etap 12d — wada SPRZED tej zmiany, ujawniona przez sondę)

- **Router w `husarz up` nie potrafił rozwiązać `api_key_ref` ŻADNEGO modelu.**
  `_router_factory` budował `ModelRouter(cfg)` bez dostawcy sekretów, więc router dostawał
  `NullSecretsProvider` i `build_client` zgłaszał „Nie udało się rozwiązać sekretu klucza API".
  Każdy model za bramą API (zdalny vLLM z tokenem, usługa komercyjna) był w produkcji
  nieużywalny. Dostarczona konfiguracja nie używa `api_key_ref`, więc nic tego nie wywoływało.
  Ujawniła to sonda: rozwiązywała klucz i meldowała OK dla drogi, której router nie potrafił
  przejść — fałszywe OK wynikające z tego, że narzędzie pomiarowe było SPRAWNIEJSZE od
  mierzonego systemu. Test regresyjny: `test_up_przekazuje_sekrety_do_routera`.

### Dodane (Etap 12c — diagnoza w konsoli: `GET /api/doctor` + zakładka Diagnoza)

- **`GET /api/doctor`** — ta sama funkcja, którą wykonuje `husarz doctor`, wystawiona przez
  API. Zwraca `findings[]` (`id`, `state`, `severity`, `description`, `remedy`) oraz liczniki
  `blocking`/`warnings`/`unknown` policzone z TEJ SAMEJ listy. Domyka obietnicę „jedno źródło
  prawdy dla CLI i konsoli", która dotąd była spełniona w połowie.
- **Zakładka „Diagnoza" w konsoli WWW** — tabela ustaleń z instrukcją naprawy przy każdym,
  własny kolor dla stanu NIEZNANY (ani sukces, ani problem) i przycisk „Sprawdź ponownie".
  Panel WYŚWIETLA gotowe ustalenia; oceny nie liczy sam, żeby oba nośniki nie rozjechały się
  w ocenie tej samej instalacji.
- **Błąd czatu i orkiestracji kieruje do diagnozy** — `502 Backend modelu zawiódł` to
  dokładnie ten komunikat, dla którego diagnoza powstała; teraz niesie odnośnik do zakładki.
- **Nowe uprawnienie RBAC `diagnostics:read`** (`admin`, `operator`). ŚWIADOMIE osobne od
  `config:read`: to uprawnienie ma rola `user` zakładana samodzielną rejestracją, a odpowiedź
  diagnozy niesie **endpointy silników** i **ścieżki katalogów operatora**, których warstwa
  `config:read` celowo nie wystawia (`GET /api/models` podaje backend i tagi, ale nie
  endpoint). Wywołanie dodatkowo otwiera połączenia wychodzące, więc nie jest zwykłym
  odczytem — z tego samego powodu uprawnienia nie dostał `viewer`.
- **Wywołanie jest audytowane** (akcja `doctor`, z referencją wywołującego). W szczególe
  wyłącznie trzy liczby — żadnych endpointów ani ścieżek, bo dziennik jest niemodyfikowalny.
  Allowlista `audit_view` działa deny-by-default, więc `GET /api/audit` pokazuje ten wpis
  z pustym `detail` (sprawdzone testem, nie założone).
- **Kontrola kolizji portu używa REALNEGO adresu nasłuchu** — `create_app` dostaje
  `listen_host`/`listen_port` z launchera (`--host`/`--port`). Świadomie NIE czytamy nagłówka
  `Host` z żądania: pochodzi od klienta, więc kontrola bezpieczeństwa oparta na nim dawałaby
  wynik sterowany przez pytającego.
- **Sonda diagnozy jest wstrzykiwalna także przez API** (`create_app(doctor_probe=...)`).
  Bez tego API zaszywałoby `SondaSystemowa` na sztywno i odebrało modułowi diagnozy pełną
  testowalność offline — żaden z nowych testów nie dotyka sieci.
- Zrzut ekranu w dokumentacji (`docs/assets/screenshots/console-diagnoza.png`) i wpis
  w `scripts/screenshots.py`, żeby odświeżał się razem z pozostałymi.
- Testy: +22 (RBAC, ślad w audycie, zgodność liczników z listą, przewleczenie portu, panel
  konsoli). Nośność: 9 mutacji, 9 czerwonych. Notatka weryfikacyjna: `docs/BEZPIECZENSTWO.md`,
  sekcja „Etap 17h".

### Poprawione (Etap 12c)

- **Konsola nie ufa kluczowi z odpowiedzi API** — mapa stanów była indeksowana wartością
  `state`, więc stan o nazwie `constructor` sięgnąłby do prototypu, zwrócił funkcję i wywrócił
  destrukturyzację, usuwając CAŁĄ tabelę. Osłonięte `Object.hasOwn`. Wartość spoza `Stan` nie
  powstanie przez `zdiagnozuj`, ale konsola nie zakłada niczego o wejściu.
- Błędy czatu i orkiestracji przechodzą przez `opisBledu`, jak reszta konsoli — dotąd sklejały
  samo `d.detail`, więc odpowiedź 422 (tablica) gubiła treść.

### Dodane (Etap 12b — `husarz doctor`, diagnoza instalacji)

- **`husarz doctor` — jedno źródło prawdy dla CLI i konsoli.** Po pobraniu binarki czat
  odpowiadał `502 Backend modelu zawiódł`, a w logu startowym nie było NIC (odtworzone).
  Diagnoza zamienia tę ciszę w listę ustaleń z instrukcją naprawy. Kod wyjścia 1 przy
  problemie blokującym — nadaje się do skryptu startowego.
- Ta sama diagnoza pokazuje się przy `husarz up` (tylko ustalenia wymagające uwagi).
- **Trzy stany, nie dwa:** `[ok]`, `[!!]` i **`[??]` (nieznany)**. Ostatni jest osobny celowo —
  pomiar NIE MOŻE zaokrąglać „nie dało się sprawdzić" do „w porządku". Podsumowanie wymienia
  stany nieznane osobno i nigdy nie kończy się „wszystkie kontrole przeszły", gdy którejś
  kontroli nie wykonano.
- Kontrole wykrywają m.in. **dwie luki, których walidacja schematu NIE łapie** (sprawdzone
  osobno): `models.chat` wskazujący model z `enabled: false` oraz model bez endpointu. Obie
  przechodzą walidację i wywracają się dopiero przy pierwszym żądaniu.
- **Diagnoza NIE jest obejściem bramki egress** — sondowanie endpointu przechodzi tę samą
  kontrolę co ruch routera; endpoint spoza allowlisty nie jest odpytywany. Bez tego `doctor`
  wystawiony w konsoli byłby skanerem portów.
- **Diagnoza obejmuje CAŁY łańcuch**, nie tylko model czatu: `models.chat`, `models.default`
  (orkiestracja) i `routing.agent_models` (agenci). Pierwsza wersja sprawdzała wyłącznie czat
  i na dostarczonej konfiguracji kończyła się „ostrzeżeń: 1", podczas gdy orkiestracja
  i WSZYSTKICH SIEDMIU agentów wskazywało na serwery vLLM, których nikt nie uruchomił —
  obraz prawdziwy co do litery, mylący co do całości. Sprawdzone na realnej konfiguracji repo.
- Ustalenia grupowane po MODELU, nie po roli (siedmiu agentów na jednym modelu = jeden wpis
  z listą ról); silnik pytany RAZ na endpoint, nawet gdy dzieli go kilka modeli.
- **Sprostowanie komentarza w kodzie**: twierdził, że schemat nie pilnuje wartości
  w `routing.agent_models`. Pilnuje — sprawdziłem; pilnuje też `routing.rules`. Gałąź obronna
  zostaje (funkcja przyjmuje dowolny obiekt konfiguracji), ale opis mówi teraz prawdę.

- Sonda jest wstrzykiwana, więc cały zestaw testów działa offline. Testy: +21.

### Poprawione (Etap 12b — trzy wady wykryte NA PIERWSZYM uruchomieniu narzędzia)

Wszystkie trzy polegały na tym samym: diagnoza mówiła nieprawdę o stanie, który miała opisać.

- **Fałszywe „nie ma modelu"** — Ollama zwraca `husarz:latest`, konfiguracja mówi `husarz`.
  Normalizacja etykiety siedziała w ekstraktorze JEDNEGO z dwóch endpointów, a odpowiadał ten
  drugi (OpenAI-compat). Normalizacja jest teraz w jednym miejscu — przy porównaniu.
- **Fałszywe „OK"** — obcinanie etykiety po OBU stronach zrównywało `qwen2.5-coder:7b`
  z `qwen2.5-coder:1.5b`, więc diagnoza meldowała „model jest", gdy stał tam inny wariant.
  Fałszywe OK jest gorsze niż fałszywy alarm: operator przestaje szukać. Porównujemy teraz
  przez kanonizację (brak etykiety = `:latest`), nie przez obcinanie.
- **Podsumowanie przeczyło liście** — przy problemie NIEBLOKUJĄCYM kończyło się zdaniem
  „wszystkie kontrole przeszły", mając wypisany problem dwie linie wyżej.
- **Diagnoza kłamała o przyczynie** — przy zablokowanym egressie mówiła „silnik nie
  odpowiedział" i radziła `ollama serve`, choć nikt silnika nie pytał. Odmowa sondowania jest
  teraz osobnym ustaleniem, z instrukcją dotyczącą allowlisty.

### Poprawione (Etap 17g — magazyn sekretów przy dwóch procesach)

- **Dwa procesy na tym samym pliku gubiły sobie zapisy.** Magazyn zakładał wyłączność jednego
  procesu, ale nic jej nie egzekwował: każdy trzymał wpisy w pamięci, więc zapis drugiego
  nadpisywał plik wersją bez sekretu zapisanego przez pierwszy. Objawiało się to jako „token
  przestał działać", bez żadnego błędu.
- Naprawa: **odczyt-modyfikacja-zapis pod blokadą międzyprocesową** (`flock` na POSIX,
  `msvcrt.locking` na Windowsie) plus przeładowanie przy odczycie, gdy plik zmienił się od
  ostatniego wczytania. Rozważane i ODRZUCONE: blokada wyłączna na czas życia procesu — byłaby
  prostsza, ale zamykałaby drogę narzędziom chcącym tylko odczytać magazyn.
- Blokujemy OSOBNY plik `.lock`, nie sam magazyn: magazyn jest podmieniany przez `os.replace`,
  więc blokada trzymana na nim dotyczyłaby po chwili i-węzła, którego już nikt nie widzi.
- Testy: +6, na PRAWDZIWYCH procesach potomnych (`flock` jest per deskryptor, więc test
  wątkowy mógłby przejść z innego powodu).
- **Nośność wskazała lukę w moich testach:** mutacja zdejmująca `flock` nie czerwieniła
  żadnego z pierwszych pięciu, bo wszystkie są sekwencyjne i blokada nigdy nie jest w sporze.
  Dopisany test, w którym proces potomny TRZYMA blokadę, a my mierzymy, czy zapis zaczekał.
- Ograniczenie zapisane wprost: ścieżka windowsowa NIE jest zweryfikowana uruchomieniem.

### Dodane (Etap 16 — budżet okna kontekstu)

- **Router sprawdza, czy prompt WRAZ Z rezerwą na odpowiedź mieści się w oknie modelu.**
  Dotąd clampowaliśmy wyłącznie `max_tokens` wg kontroli kosztów, a rozmiar promptu nie był
  sprawdzany wcale. Przy modelu 7B i pętli narzędziowej to realny problem, zaobserwowany
  w tym projekcie: rozmowa rośnie o wyniki narzędzi (JSON, gęsty tokenowo), przekracza okno,
  backend zwraca błąd albo po cichu ucina kontekst, a agent wypala limit iteracji.
- **Niezmieszczenie się to POMINIĘCIE kandydata, nie błąd** — prompt za duży dla modelu 7B
  może wejść do fallbacku o większym oknie, więc router próbuje dalej, tak jak przy bramce
  wizyjnej i egressowej. Dopiero brak okna u wszystkich daje `AllModelsFailedError`
  z powodem zawierającym liczby i podpowiedzią, co zrobić.
- Rezerwa na odpowiedź: `max_tokens` żądania → `max_tokens` modelu → 512. Bez niej prompt
  mógłby wypełnić okno co do tokena, zostawiając model bez miejsca na odpowiedź.
- **Estymator SKALIBROWANY realnym tokenizerem, nie zgadnięty.** Pomiar przez Ollamę
  (`qwen2.5-coder:7b`, `prompt_eval_count`): polski ~2,1 znaku/token, kod ~2,9, angielski
  ~2,7, **JSON 1,68**. Dzielnik bierzemy z JSON-a, bo to najgorszy przypadek dla nas — wyniki
  narzędzi w pętli agentowej są JSON-em. Doliczony zmierzony narzut szablonu czatu (29 tokenów
  stałych, ~3 na wiadomość). Szacujemy Z GÓRY: fałszywa odmowa z czytelnym komunikatem jest
  tańsza niż cicha awaria backendu w środku pętli.
- **Ograniczenie zapisane wprost:** obrazy NIE są liczone (modele wizyjne liczą je osobno,
  zależnie od rozdzielczości — nie da się tego odtworzyć bez tokenizera modelu), więc prompt
  z obrazami jest niedoszacowany. Docs: `docs/ROUTER.md`.
- Testy: +14, w tym walidacja estymatora wobec ZMIERZONYCH liczb (nie zaniża i nie zawyża
  absurdalnie). Nośność: 5 mutacji, wszystkie czerwienią zestaw.

### Zmienione (Etap 17g — ZMIANA ZACHOWANIA: wyłączenie magazynu to kill-switch)

- **`security.secret_store.enabled: false` odcina teraz także ODCZYT.** Dotąd zamykało
  wyłącznie ZAPIS: rozwiązywanie referencji szło obok bramki, więc dotychczasowe tokeny nadal
  uwierzytelniały operacje Gita. Operator wyłączający magazyn robi to zwykle w reakcji na
  incydent i oczekuje, że przestanie on wydawać materiał. Skutek jest GŁOŚNY — operacja Gita
  kończy się „Nie udało się rozwiązać tokenu połączenia" (zweryfikowane na realnej ścieżce),
  nie cichą degradacją. Ponowne włączenie działa natychmiast, bez restartu.

### Poprawione (Etap 17g — ostatnie zgłoszenia drugiego przeglądu)

Cztery potwierdzone, **jedno obalone** — pierwszy taki przypadek w tej serii. Bilans:
z dziewiętnastu zgłoszeń sprawdzonych osobno osiemnaście okazało się realnych.

- **Bramka magazynu sprawdzana POZA zamkiem.** Żądanie, które przeszło ją tuż przed
  wyłączeniem magazynu, zapisywało token JUŻ PO tym wyłączeniu. Sprawdzana ponownie pod
  zamkiem — kontrola bezpieczeństwa nie może mieć okna „prawie zamkniętego".
- **`ca_bundle` wracał dosłownie w odpowiedzi 400** — druga droga echa obok tej, którą zamknął
  handler walidacji. Komunikat wskazuje teraz pole, nie powtarzając wartości.
- **`POST /api/git/connections` był poza zamkiem** `_mutex_polaczen` i mógł wyścigać się ze
  sprzątaniem sekretu w `DELETE`. Objęty.
- **Konsola wyświetlała błędy walidacji jako `[object Object]`** — odpowiedź 422 niesie TABLICĘ
  obiektów, a panel sklejał ją ze stringiem. Komunikat ginął dokładnie tam, gdzie użytkownik
  pomylił się w formularzu. Regresja własna, wprowadzona razem z handlerem z Etapu 17c.
- **OBALONE:** zgłoszenie o bezwzględnych ścieżkach operatora w odpowiedziach konfiguracji nie
  potwierdziło się — odpowiedź nie zawiera ani `config_dir`, ani przedrostków ścieżek.
- Testy: +8. Nośność: 4 mutacje czerwienią zestaw. Piąta poprawka (objęcie zamkiem drugiej
  drogi dodawania) **nie ma testu na SKUTEK** i jest to zapisane wprost — okno to dwie
  sąsiednie instrukcje, nieodtwarzalne bez pauzy wstrzykniętej w kod produkcyjny. Zostaje
  kontrola strukturalna z komentarzem, że jest słabszym dowodem.

### Poprawione (Etap 17f — dokończona weryfikacja drugiego przeglądu)

Faza weryfikacji potwierdziła pięć zgłoszeń; dwa dotyczyły wad naprawionych już w `cab4d12`.
Bilans: z czternastu zgłoszeń sprawdzonych osobno **czternaście okazało się realnych**.

- **SPROSTOWANIE — regresja z `cab4d12`: sprzątanie sierot niszczyło DZIAŁAJĄCE poświadczenie.**
  Rozszerzając warunek usuwania sekretu o „połączenia nie ma, więc to sierota", pominąłem
  przypadek, w którym referencję współdzieli INNE połączenie (np. po zmianie nazwy).
  `DELETE /api/git/connections/gh` kasowało wtedy wpis używany przez połączenie `produkcja`
  i raportowało sukces. Sekret kasujemy teraz tylko wtedy, gdy po usunięciu połączenia żadne
  inne nie wskazuje tej referencji.
- **Nadpisania runtime, których nie da się zastosować, odpowiadały `ok: true`.** Bramka
  z Etapu 17d czytała wyłącznie `enabled`; ścieżka magazynu i klucz główny pozostawały
  domknięciem z chwili startu. Operator „przenosił" magazyn na inny wolumen i rotował klucz,
  dostawał 200, a token szedł do STAREJ ścieżki starym kluczem. Sceptyk wykazał, że to własność
  CAŁEGO endpointu — identycznie milczało nadpisanie `security.audit.path`. Bramka odmawia
  teraz zmian pól niezmiennych w runtime, porównując WARTOŚCI wobec konfiguracji startowej
  (powtórzenie dotychczasowej wartości i wyłączenie magazynu nadal przechodzą).
- **Token wklejony w pole `name` był PRZYJMOWANY i trwale zapisywany.** Wzorzec nazwy
  z Etapu 17d przepuszczał dokładnie kształt obsługiwanych tokenów, więc omyłkowa wartość
  lądowała w NIEMODYFIKOWALNYM dzienniku audytu, w pliku połączeń oraz jako JAWNY klucz
  w magazynie sekretów — jedynym wyjściem byłoby unieważnienie tokenu u dostawcy. Odrzucamy
  nazwy zaczynające się prefiksem poświadczenia, na obu endpointach.
- **Awaria zapisu przy `DELETE` nie zostawiała śladu** — surowe 500 i zero wpisów w dzienniku,
  mimo że żądanie dotyczyło poświadczenia. Teraz 503 i wpis `git.connection.remove.failed`.
- Testy: +20. Nośność: 5 mutacji, wszystkie czerwienią zestaw.

### Poprawione (Etap 17e — trzy wady z drugiego przeglądu adwersaryjnego)

Przegląd objął commity 1bb2191 i 5277d49. Szesnaście zgłoszeń; trzy o największej wadze
sprawdzono osobno i **wszystkie trzy potwierdzono uruchomieniem**. Dwie przetrwały poprzednie
przeglądy, bo dotyczyły magazynu POŁĄCZEŃ, a uwagę skupiał magazyn SEKRETÓW.

- **Przebudowa serwisu Git kasowała połączenia dodane przez API.** Fabryka w launcherze
  domykała na `git_service` z chwili STARTU; gdy Git był wtedy wyłączony (domyślnie jest),
  domknięta wartość zostawała `None`, więc każde nadpisanie runtime budowało PUSTY magazyn.
  Token zostawał wtedy na dysku jako sierota, a `DELETE` zwracał `ok: true`, nie usuwając
  niczego. Komentarz w kodzie twierdził, że magazyn jest przekazywany dalej — było to prawdą
  tylko dla ścieżki, w której Git działał od startu. **Zmiana kontraktu:**
  `git_service_factory` przyjmuje teraz drugi argument (bieżący magazyn połączeń).
- **`FileGitConnectionStore` miał tę samą wadę, którą domknięto w magazynie sekretów** —
  mutację pamięci przed zapisem — plus wypuszczał surowy `OSError`. Kreator łapie
  `GitConnectionError`, więc awaria zapisu dawała 500 i POMIJAŁA sprzątanie świeżo zapisanego
  sekretu. Wniosek: gdy poprawka dotyczy WZORCA, trzeba przeszukać repo pod jego kątem,
  a nie poprzestać na module, w którym wadę zgłoszono.
- **Sekret trwały przy ulotnym magazynie połączeń = gwarantowana sierota.** Przy domyślnym
  `git.connections_path: null` kreator produkował przy każdym restarcie token bez połączenia,
  nie do usunięcia przez API. Kreator odmawia teraz (409) z instrukcją, a `DELETE` sprząta
  sierotę, gdy nazwa należy do przestrzeni `husarz:git/`. Referencji zewnętrznej nie rusza.
- Odpowiedź `DELETE /api/git/connections/{name}` niesie teraz SKUTEK (`removed`,
  `secret_removed`), a nie samo `ok: true`, które przy sierocie było nieprawdziwe.
- `GitConnectionStore.persistent` — trwałość deklarowana jawnie, nie zgadywana po typie.
- `tmp.unlink(missing_ok=True)` w blokach sprzątających obu magazynów sam potrafił rzucić
  `NotADirectoryError` i przesłonić właściwą przyczynę awarii.
- Testy: +10. Nośność: 5 mutacji, z czego **jedna ujawniła wadę w moim teście** (awaryjne
  przejście na ten sam plik maskowało utratę połączeń) — przepisany na magazyn ulotny.

### Poprawione (Etap 17d — domknięcia odcięte przez limit weryfikacji przeglądu)

Sześć zgłoszeń, których przegląd nie zweryfikował z powodu capu. Każde sprawdzono osobno —
**wszystkie sześć okazało się realne.**

- **Fail-open: wyłączenie magazynu sekretów w runtime nic nie robiło.** `POST /api/config/runtime`
  przebudowuje router, orkiestrator, wtyczki i serwis Gita, ale magazyn był domknięciem
  z chwili startu. Odtworzone na żywej instancji: wyłączenie kończyło się `ok: true`, a kreator
  NADAL zapisywał token (HTTP 200) — kontrola wyglądała na wyłączoną, będąc włączoną.
  Bramka czyta teraz bieżącą konfigurację; instancja zostaje, więc ponowne włączenie działa
  bez restartu (klucz główny bywa rozwiązywalny wyłącznie w procesie launchera).
- **`create_app(secret_store=…)` mógł być parametrem MARTWYM** przy wyłączonej konfiguracji —
  ta sama klasa pułapki, co `internal: true` cicho wyłączające publikowanie portów w compose.
  Sprzeczność jest teraz wykrywana przy konstrukcji i zgłaszana wyjątkiem.
- **Ukośnik w nazwie połączenia czynił je NIEUSUWALNYM.** Nazwa jest segmentem ścieżki URL-a
  i nie była walidowana: utworzenie `grupa/projekt` zwracało 200, a `DELETE` — 404, także
  z `%2F`. Połączenie zostawało na liście i trzymało token bezterminowo. Dodano wzorzec na
  OBU endpointach dodających.
- **Wpisy audytu bez `principal`** przy wprowadzeniu i usunięciu poświadczenia — czyli przy
  zdarzeniach, w których pytanie „kto to zrobił" jest jedynym istotnym.
- **Mutacja stanu w pamięci przed udanym zapisem pliku.** Nieudany zapis rozjeżdżał magazyn:
  proces widział sekret, którego w pliku nie było, więc po restarcie referencja przestawała
  się rozwiązywać. Praca na kopii; stan podmieniany PO udanym zapisie.
- **`os.replace` bez `fsync` — atomowość bez trwałości.** Po awarii zasilania plik bywa pusty
  albo obcięty, co (fail-closed) blokuje start i traci WSZYSTKIE sekrety naraz. Dodano `fsync`
  pliku i katalogu oraz pełną pętlę `os.write` (krótszy zapis jest legalny).
- **Zerowe pokrycie `SecretStoreConfig` i sklejenia w launcherze** — cały walidator dało się
  usunąć, a zestaw zostawał zielony. To jedyny kod czyniący kreator użytecznym.
- Testy: +41. Nośność: 8 mutacji, z czego **dwie ujawniły wady w moich własnych testach**
  (mutacja obejmująca jeden z dwóch modeli; asercja na obecność pola `principal` zamiast na
  jego wartość — `AuditLog` zapisuje je zawsze, także puste). Oba testy poprawione.

### Poprawione (Etap 17c — po adwersaryjnym przeglądzie Etapu 17)

Commit 5f4039d przeszedł przegląd: pięć niezależnych soczewek, każde zgłoszenie oceniane przez
dwóch sceptyków (jeden próbuje obalić, drugi odtworzyć awarię). Pięć zgłoszeń potwierdzono
uruchomieniem kodu; trzy z nich to wady wprowadzone albo utrwalone przez tamten commit.

- **SPROSTOWANIE — „token nie występuje w komunikatach błędów" było NIEPRAWDĄ.** Rezygnacja
  z ograniczeń Pydantica na polu `token` zamykała JEDEN wariant z sześciu. Domyślna obsługa
  `RequestValidationError` w FastAPI zwraca `input` z odrzuconą wartością, a wychodzi ono także
  gdy: brakuje INNEGO wymaganego pola (echo całego ciała wraz z tokenem), nazwa pola ma
  literówkę, ciało przyszło jako formularz `x-www-form-urlencoded` (zwykłe `curl -d`) albo jako
  lista JSON, oraz — najbardziej prawdopodobne w praktyce — gdy operator wklei surowy token
  w pole `token_ref` na `POST /api/git/connections`. Ostatni wariant istniał PRZED Etapem 17;
  nowy był wyłącznie fałszywy zapis, że kanał jest zamknięty.
  Naprawa: handler `RequestValidationError` dla CAŁEJ aplikacji zwraca tylko `type`, `loc`
  i `msg`. Bramka na poziomie aplikacji obejmuje też endpointy, które dopiero powstaną.
- **SPROSTOWANIE — pre-check kolizji NIE chronił pod współbieżnością.** Opisaliśmy go jako
  kontrolę bezpieczeństwa przed cichą utratą poświadczenia; jest to wzorzec check-then-act
  i umożliwiał dokładnie to, przed czym miał chronić. Dwa równoległe żądania kreatora o tej
  samej nazwie: oba przechodzą sprawdzenie, drugie NADPISUJE sekret pierwszego, jego `add`
  zawodzi na kolizji, a sprzątanie kasuje token ZWYCIĘZCY — zostaje połączenie z referencją,
  która nie rozwiązuje się na nic. Ten sam wyścig między kreatorem a `DELETE`.
  Naprawa: zamek obejmujący obie sekwencje w całości (magazyn połączeń + magazyn sekretów
  jako jedna operacja niepodzielna).
- **Regresja fail-closed przy braku `cryptography`.** Przeniesienie prymitywu do
  `husarz.core.crypto` zgubiło kontrolę dostępności backendu przy budowie szyfru, a komentarz
  w kodzie nadal twierdził, że ona istnieje. Magazyn pamięci i magazyn sekretów budowały się
  bez przeszkód, a awaria wychodziła przy pierwszym zapisie. Kontrola mieszka teraz
  w konstruktorze `AesGcmCipher` — w jednym miejscu, obejmującym wszystkich wołających.
- **Test wyścigu `DELETE` w pierwszej wersji nic nie chronił** — przechodził także bez zamka,
  bo pauzę wstrzyknięto po niewłaściwej stronie `remove`. Przebudowany; teraz czerwieni się
  bez poprawki.
- Testy: +16 (`tests/security/test_walidacja_bez_echa.py`,
  `tests/security/test_git_wizard_wyscigi.py`). Nośność sprawdzona: usunięcie handlera
  czerwieni 6 testów, usunięcie zamka — 3.

### Dodane (Etap 17b — własne CA dla połączeń Git)
- Pole `ca_bundle` na połączeniu Git — ścieżka do pliku PEM z certyfikatem urzędu, który
  podpisał certyfikat serwera. Odblokowuje **samodzielnie hostowanego GitLaba z prywatnym CA**,
  udokumentowanego wcześniej jako ograniczenie. Dostępne w obu ścieżkach dodawania (kreator
  i referencja) oraz w konsoli, jako pole „własne CA" i kolumna w tabeli połączeń.
- **Zaufanie ZAWĘŻONE do jednego połączenia**: `ssl.create_default_context(cafile=…)` zastępuje
  magazyn systemowy, zamiast się do niego dokładać. Semantyka `SSL_CERT_FILE` (dołożenie)
  byłaby wygodniejsza i wyraźnie gorsza — prywatny urząd zyskałby prawo poświadczania
  dowolnego hosta na wszystkich ścieżkach wychodzących, w tym `api.github.com`.
- Brak przełącznika „ignoruj błędy certyfikatu" i brak takich planów. Kontekst zachowuje
  `check_hostname=True` i `CERT_REQUIRED`; przy przypiętym IP weryfikacja nadal idzie po NAZWIE.
- Błędna ścieżka wykrywana PRZY DODAWANIU połączenia (HTTP 400), nie przy pierwszej operacji —
  inaczej literówka objawiałaby się jako niepowiązany błąd TLS. Komunikat nie zawiera treści
  pliku (operator może omyłkowo wskazać klucz prywatny).
- Zgodność wstecz: pliki połączeń zapisane przed zmianą wczytują się bez `ca_bundle`.
- Testy: +18, w tym `tests/integration/test_git_ca_bundle_tls.py` — REALNY serwer TLS na
  loopbacku i REALNE uzgodnienie z certyfikatem podpisanym przez wygenerowany na miejscu urząd.

### Poprawione (Etap 17b — luka w pokryciu wykryta testem mutacyjnym)
- **Nic nie sprawdzało, czy kontekst TLS dociera do httpx.** Podmiana
  `verify=self._ssl_context` na `verify=True` w `HttpxGitTransport` przechodziła przez CAŁY
  zestaw testów na zielono: kontekst był poprawnie budowany i poprawnie wstawiany do
  transportu, ale ostatnie ogniwo — to, które faktycznie decyduje — nie było sprawdzone wcale.
  Ten sam wzorzec (weryfikacja deklaracji zamiast skutku) przepuścił w tym projekcie sześć
  wcześniejszych wad. Domknięte testem realnego uzgodnienia TLS; dowodem nie jest to, że
  połączenie z własnym CA działa, tylko że **bez niego zawodzi**.

### Dodane (Etap 17 — zapisywalny magazyn sekretów i kreator połączeń Git)
- Nowy moduł `husarz.security.secret_store` — szyfrowany, ZAPISYWALNY magazyn sekretów.
  Pierwsze miejsce, w którym Husarz PRZYJMUJE materiał sekretu, zamiast wyłącznie rozwiązywać
  referencje do materiału umieszczonego gdzie indziej ręką operatora. Nowy schemat referencji
  `husarz:<nazwa>`; magazyn implementuje `SecretsProvider`, więc wpina się w istniejący łańcuch
  jako kolejne źródło, a nie obok niego. Docs: ADR-0023.
- **Niezmiennik „config nie zawiera materiału" zachowany**: token wklejony w kreatorze trafia
  zaszyfrowany do osobnego pliku, a do magazynu połączeń idzie WYŁĄCZNIE wygenerowana
  referencja `husarz:git/<nazwa>` — dokładnie tak, jak wcześniej szła tam `env:`/`vault:`.
- `POST /api/git/connections/wizard` — kreator: przyjmuje token, zwraca połączenie z referencją.
  `GET /api/secrets/store` — stan magazynu dla panelu (nazwy wpisów i daty, NIGDY wartości).
- Konsola WWW, zakładka **Połączenia**: przełącznik trybu „wklej token" / „podaj referencję".
  Pole tokenu jest hasłowe (`type=password`, bez autouzupełniania) i czyszczone po zapisie —
  także po to, by nie trafiło na zrzuty ekranu do dokumentacji. Przy wyłączonym magazynie
  konsola sama przełącza się na referencję i wyjaśnia, czego brakuje, zamiast udawać, że działa.
- `security.secret_store` w konfiguracji (`enabled`, `path`, `key_ref`). Domyślnie **wyłączony**
  (deny-by-default). Włączenie WYMAGA `key_ref`; walidacja odrzuca dla niego schemat `husarz:` —
  magazyn odblokowywany własnym sekretem byłby zamkniętym kręgiem.
- Usunięcie połączenia kasuje jego sekret, ale WYŁĄCZNIE gdy używało referencji
  `husarz:git/<ta sama nazwa>`. Referencji zewnętrznej (`env:`/`vault:`) nie ruszamy — nie jest
  własnością Husarza, a operator mógł jej użyć także gdzie indziej.
- `ChainedSecretsProvider` (`husarz.config.secrets`) — pyta dostawców po kolei; wyjątek jednego
  nie przerywa łańcucha (awaria jednego źródła nie może unieruchomić pozostałych).
- Testy: +36 (`tests/security/test_secret_store.py`, `tests/security/test_git_wizard_secrets.py`),
  w tym niezmiennik zbiorczy przeszukujący WSZYSTKIE pliki powstałe podczas operacji.
  **Nośność sprawdzona**: 13 mutacji kodu, każda czerwieni odpowiedni test.
- `scripts/screenshots.py --only <zakładka>` — odświeżanie podzbioru zrzutów. Zrzut czatu
  wymaga działającego modelu, więc komplet zmuszał do podnoszenia Ollamy przy zmianie w innej
  zakładce. Nowy zrzut `console-polaczenia.png`.

### Zmienione (Etap 17 — warstwowanie)
- Prymityw szyfrowania at-rest przeniesiony z `husarz.memory.crypto` do **`husarz.core.crypto`**
  (warstwa 0): `Cipher`, `IdentityCipher`, `AesGcmCipher` oraz nowe `derive_key`. Powód: tego
  samego szyfru potrzebują DWA niezależne podsystemy warstwy 3 — pamięć długoterminowa
  i magazyn sekretów — a żaden nie może być zależnością drugiego. `husarz.memory.crypto`
  re-eksportuje klasy, więc **wszystkie dotychczasowe importy działają bez zmian**; w pakiecie
  pamięci zostaje sama POLITYKA (`build_cipher`).
- **Zmiana kontraktu (drobna)**: metody `AesGcmCipher` zgłaszają teraz `CryptoError`
  (`husarz.core.errors`) zamiast `RagBackendError`. Kontrakt PAMIĘCI jest nienaruszony —
  `build_cipher` nadal zgłasza `RagBackendError`, a `SqliteVectorStore` tłumaczy błąd
  deszyfrowania na własny typ. Dotyczy wyłącznie kodu wołającego prymityw bezpośrednio.

### Bezpieczeństwo (Etap 17)
- Pole `token` w żądaniu kreatora **celowo bez ograniczeń Pydantica**: domyślna obsługa
  `RequestValidationError` w FastAPI zwraca odrzuconą wartość w polu `input`, więc
  `max_length` sprawiłoby, że przekroczenie limitu odsyła token w treści odpowiedzi 422 —
  a stamtąd trafia on do dziennika dostępu serwera. Długość sprawdza endpoint, komunikatem,
  który wartości nie powtarza.
- **Kolejność operacji jako kontrola bezpieczeństwa**: kolizja nazwy połączenia sprawdzana
  PRZED zapisem sekretu. Naiwna kolejność (zapisz → dodaj → posprzątaj po błędzie) przy
  zajętej nazwie nadpisywała token istniejącego połączenia, a sprzątanie kasowało go
  zupełnie — cicha utrata działającego poświadczenia. Wykryte w trakcie implementacji.
- Plik magazynu tworzony przez `os.open` z trybem `0600` (nie `write_text`), w katalogu
  `0700`; zapis atomowy przez plik tymczasowy + `os.replace`. `AAD` = nazwa wpisu (anti-swap).
  Uszkodzony plik to BŁĄD, nie „pusty magazyn" — inaczej awaria wyglądałaby jak wygaśnięcie
  tokenu. Notatka weryfikacyjna z przebiegiem na uruchomionej aplikacji i lista ograniczeń:
  `docs/BEZPIECZENSTWO.md`, sekcja „Etap 17".


### Dodane (Etap 15 — pinowanie IP / domknięcie TOCTOU DNS-rebindingu)
- Nowy moduł `husarz.ssrf` — WSPÓLNA warstwa anty-SSRF dla ścieżek wychodzących (`web`,
  wtyczki MCP): klasyfikacja hostów (literał/loopback/nazwa), `resolve_and_pin`, `pin_fields`
  i `PinnedTarget`. Bez zależności od `httpx` (czysty stdlib) → w pełni testowalny offline;
  resolver DNS wstrzykiwalny. Koniec trzech rozjeżdżających się kopii tej logiki.
- **Pinowanie IP**: nazwa rozwiązywana DOKŁADNIE RAZ, KAŻDY zwrócony adres sprawdzany, jeden
  adres przypinany. Transport łączy się z literałem IP, a nagłówek `Host` i **SNI/weryfikacja
  certyfikatu** idą po ORYGINALNEJ nazwie (`extensions={"sni_hostname": ...}` → `server_hostname`
  w `start_tls`), więc `verify=True` pozostaje w mocy — pin ZAWĘŻA powierzchnię ataku, nie
  osłabia TLS. Domyka ryzyko rezydualne z ADR-0015/0016/0019. Docs: ADR-0020.
- Fail-closed w każdym rozgałęzieniu: pusta odpowiedź DNS, JAKIKOLWIEK adres wewnętrzny
  (także w mieszanych A/AAAA), niesparsowalny wynik resolvera lub URL bez hosta → `EgressError`.
  Świadomie NIE wybieramy „czystego" adresu z odpowiedzi zawierającej adres wewnętrzny.
- Kolejność bram taka, by ODMOWA nie kosztowała nawet zapytania DNS: schemat/userinfo →
  literał wewnętrzny → loopback → https → allowlista egress → dopiero DNS + pin.
- Publiczna nazwa NIE może rozwiązać się na loopback (ochrona przed zatrutym DNS kierującym
  token Bearer wtyczki do usługi na maszynie operatora). Loopback intencjonalny konfiguruje
  się literałem `127.0.0.1`/`localhost` — idzie osobną gałęzią, bez DNS.
- Kontrakt „narzędzie NIGDY nie rzuca" utrzymany także dla chorych URL-i: port spoza
  0–65535 lub nieliczbowy (`https://host:99999/x`) daje `EgressError` → `ToolResult(ok=False)`,
  a nie surowy `ValueError` ze stdlib wywracający pętlę agenta (`safe_port`, sprawdzany PRZED
  rozwiązaniem nazwy).
- Testy: +118 (offline; `tests/unit/test_ssrf.py`, `tests/security/test_ssrf_pinning.py`) —
  w tym testy REALNYCH transportów httpx przez `MockTransport` (dotąd produkcyjna ścieżka
  `HttpxFetcher`/`HttpxPluginTransport` nie była pokryta wcale).

### Poprawione (Etap 15 — luki domknięte przy okazji)
- **`web`: loopback przez NAZWĘ** (`http://localhost:8000/admin`) był blokowany wyłącznie jako
  literał IP — nazwa przechodziła przez `is_local_endpoint` jako „endpoint lokalny" i, przy
  `localhost` na allowliście narzędzia, otwierała dostęp do usług na maszynie operatora.
  Teraz odrzucana (`allow_loopback=False` dla tej ścieżki).
- **`HttpxFetcher`: pobieranie całej odpowiedzi przed przycięciem** (`response.text[:max_bytes]`)
  — ryzyko OOM przy złośliwym/przejętym serwerze. Teraz odczyt strumieniowy z twardym sufitem
  bajtowym ORAZ bezwzględnym deadline'em wall-clock (anty-„slow-drip", parytet z transportem MCP).
- Dokumentacja: usunięte nieaktualne adnotacje „brak pinowania IP / TOCTOU odłożone" z
  `BEZPIECZENSTWO.md`, `WTYCZKI.md`, `NARZEDZIA.md` oraz z sekcji „Konsekwencje" ADR-0015/
  0016/0019 (przekreślone + odsyłacz do ADR-0020 — ADR-y pozostają zapisem historycznym).
  `ARCHITEKTURA.md` przestała opisywać zaimplementowane pakiety `husarz.memory`/`husarz.plugins`
  jako zaślepki. `README.md`: przykładowy wynik `validate` doprowadzony do stanu faktycznego
  (brakowało `husarz-local`, `husarz-vision`, `plugin_example`) — rozjazd docs↔kod.

### Poprawione (weryfikacja na URUCHOMIONEJ aplikacji — dwie luki niewidoczne w testach)
- **`principal` nie był w ogóle zwracany przez API.** Wpisy audytu niosły „kto zlecił", ale
  `AuditEntryView` nie miało tego pola, więc operator czytający audyt przez `/api/audit`
  albo konsolę WWW nadal nie widział rozliczalności — cała funkcja z Etapu 13c była
  niewidoczna od zewnątrz. Pole dodane do widoku, konsola ma kolumnę „Zlecił".
- **`husarz up` bez `--config` startował z `config_dir=None`**, więc
  `POST /api/config/runtime` odpowiadał „Nadpisania wymagają katalogu konfiguracji" —
  panel konfiguracji w konsoli był martwy, mimo że konfiguracja wczytała się z domyślnego
  `./config`. Launcher rozwiązuje teraz katalog (`resolve_config_dir`) i przekazuje go dalej.
  Uboczny skutek: kotwica profilu z Etapu 4b w ogóle nie dochodziła do skutku w realnym
  uruchomieniu (endpoint kończył się wcześniej) — teraz działa i jest to zweryfikowane na żywo.
- Testy: +2 regresyjne. Obie luki przechodziły przez zestaw testów, bo testy API wstrzykują
  `config_dir` wprost, a widok audytu nie był asertowany — znalazło je dopiero uruchomienie
  serwera i odpytanie endpointów.

### Dodane (udokumentowane granice integracji Git — trzy nieudokumentowane ograniczenia)
- **W profilu `airgap` integracja Git nie działa wcale**, także z GitLabem w sieci lokalnej:
  klient sprawdza allowlistę egress dla KAŻDEGO hosta (świadomie bez skrótu „lokalne = wolno"),
  a walidacja profilu `airgap` wymusza allowlistę PUSTĄ. Zamierzone, ale nieoczywiste —
  operator z własnym GitLabem w LAN-ie spodziewa się, że lokalne zadziała.
- **Nie da się wskazać własnego CA.** `HttpxGitTransport` ma `verify=True` i `trust_env=False`
  na sztywno; drugie jest celowe (zmienne PROXY nie mogą przekierować przypiętego połączenia
  z tokenem), ale powoduje ignorowanie `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`, a pola na bundle
  CA nie ma. Blokuje to samodzielnie hostowanego GitLaba mocniej niż wymóg `https`.
- **`security.mtls` to sekcja czysto deklaratywna** — zero odczytów w kodzie poza schematem
  (mTLS to Etap 6). W połączeniu z powyższym tworzy fałszywy trop: `ca_cert_ref` wygląda na
  rozwiązanie problemu CA i nim nie jest.
- **Zakresy nie są równoważne**: MR na GitLabie wymaga zakresu `api`, czyli pełnego odczytu
  i zapisu całego API użytkownika; GitHub ma węższy `pull_requests:write`. Do listowania
  wystarcza `read_api`.
- Ustalone przy projektowaniu logowania OAuth. Przy okazji zweryfikowano w dokumentacji
  GitHuba, że **PKCE nie zwalnia z `client_secret`** (wymagany także z PKCE; wyjątkiem jest
  wyłącznie device flow), więc wariant „przycisk → przeglądarka → gotowe" jest dla klienta
  publicznego ślepą uliczką. Docs: `docs/GIT.md`, notatka w `docs/BEZPIECZENSTWO.md`.

### Zmienione (standard prowadzenia projektu — CLAUDE.md, SECURITY.md, CONTRIBUTING.md)
- **Push wykonuje się na bieżąco**, a nie wyłącznie ręką operatora — po spełnieniu czterech
  warunków (zielone bramki, czysty gitleaks, zaktualizowana dokumentacja, czysty `git status`).
  Poza tym pozostają decyzją operatora: `push --force` na gałąź główną, usuwanie gałęzi/tagów/
  wydań oraz publikacja wiki, PDF i Release — czyli operacje nieodwracalne albo decydujące
  o tym, co staje się publiczne.
- **Tabela rytmu „na bieżąco"** — 16 obszarów (dokumentacja, README, CHANGELOG, ROADMAP,
  commity, push, tagi, gałąź, docstringi, audyty, spójność wersji, CI, podpisy, kod wrażliwy,
  sekrety, porządek w repo) z jednoznacznym wymogiem: robione W TYM SAMYM kroku co zmiana.
- **Nowa zasada nadrzędna: „sprawdzaj SKUTEK, nie deklarację".** Wyprowadzona z sześciu
  realnych wad, które przeszły przez komplet zielonych testów, bo testy asertowały `argv`
  i YAML zamiast obserwowalnego efektu. Wraz z wymogiem, by test zależny od środowiska
  pomijał się z czytelnym powodem, nigdy nie udając sukcesu.
- **Nośność testów jako obowiązek**: po napisaniu testu cofnij poprawkę i sprawdź, że się
  czerwieni. Plus wymóg asercji „wartości są różne" w testach porównujących stan przed/po.
- **Sprostowania jako obowiązek** — nieprawdziwe twierdzenie w commicie, CHANGELOG-u albo
  notatce poprawia się jawnie, z wyjaśnieniem, co zweryfikowano; dotyczy też wadliwych metod
  pomiaru.
- **Bramki jakości rozszerzone** o `husarz eval`, `mkdocs build --strict` i `gitleaks protect`;
  ścieżki venv rozdzielone na Windows i Linux/macOS (dotychczasowe działały tylko na Windows).
- **Podpisy**: podpisujemy, gdy operator skonfigurował klucz; NIE konfigurujemy klucza za niego
  i nie podpisujemy w jego imieniu — podpis to oświadczenie konkretnej osoby. `Co-Authored-By`
  zawsze, żeby pochodzenie kodu było jawne.
- **SECURITY.md**: sekcje o audytach ciągłych (tabela: co i kiedy), obowiązku opisu kodu
  wrażliwego (po co / jakie ryzyko / co chroni / czy da się usunąć), zapisie sekretów
  (`SecretsProvider` jest jednokierunkowy — rozszerzenie o zapis to nowa powierzchnia ataku)
  oraz podpisach.
- **Porządek w repo** jako wymóg z listą kontrolną, w tym powtarzalne sprzątanie sidecarów
  AppleDouble, które blokują `docker build` i wpadają w globy plików.

### Poprawione (manifesty k8s — pułapka aktualizacyjna w `kustomization.yaml`)
- `commonLabels` jest przestarzałe (kubectl ostrzega), ale groźniejsza jest druga właściwość
  tego pola: **wstrzykuje etykiety do SELEKTORÓW**, a selektor Deploymentu jest
  **niemodyfikowalny po utworzeniu**. Każda przyszła zmiana zablokowałaby aktualizację
  działającego wdrożenia komunikatem `field is immutable`, wymuszając ręczne usunięcie
  zasobu. Zamienione na `labels:` z `includeSelectors: false`. Zmiana jest bezpieczna teraz,
  bo nic nie zostało jeszcze wdrożone — po wdrożeniu byłaby przełomowa.
- **Nowe testy spójności po ZBUDOWANIU overlayu** (`tests/integration/test_k8s_manifests.py`):
  selektory Deploymentu/Service/NetworkPolicy trafiają w etykiety poda, Ingress wskazuje
  istniejącą usługę i port, `targetPort` odpowiada portowi kontenera, `default-deny-all`
  obejmuje wszystkie pody w obu kierunkach, a budowa nie zgłasza przestarzałych pól.
  Dotychczasowe testy parsowały surowe pliki, a kustomize je przekształca — na klastrze
  liczy się wynik przekształcenia. Testy używają `kubectl kustomize` zamiast odtwarzać jego
  semantykę, bo własna reimplementacja mogłaby dawać fałszywe poczucie bezpieczeństwa.
- Same manifesty okazały się spójne — wada dotyczyła wyłącznie pola `commonLabels`.

### Dodane (audyt dokumentacji + niezmiennik warstw importów)
- **Weryfikacja poleceń z dokumentacji** (wymóg CLAUDE.md): wyłuskane 54 polecenia z README,
  CONTRIBUTING i `docs/`; wszystkie polecenia CLI uruchomione i potwierdzone —
  `validate`, `eval`, `eval --set`, `version` zwracają 0, a `roe verify` zwraca 1 zgodnie
  z zasadą fail-closed (brak klucza), czyli tak, jak opisuje dokumentacja.
- **Audyt pokrycia pakietów w `docs/`** wykrył trzy luki: `husarz.core` i `husarz.eval`
  (oba nowe) oraz zaległą `husarz.textjson`. Wszystkie opisane w `ARCHITEKTURA.md`;
  `EWALUACJA.md` nazywa teraz moduły wykonawcze.
- **Nowa sekcja „Warstwy importów"** w `ARCHITEKTURA.md` — tabela sześciu warstw z regułą
  „moduł niższej warstwy nie importuje z wyższej" i wyjaśnieniem, dlaczego jej złamanie
  daje cykl działający wyłącznie przy szczęśliwej kolejności importów. Tabela jest
  **opisowa, nie życzeniowa**: analiza AST całego drzewa wykazała zero naruszeń.
- **Niezmiennik zautomatyzowany.** `test_no_module_imports_from_a_higher_layer` sprawdza
  CAŁE drzewo (AST), a nie sześć wybranych modułów jak poprzedni test — złapie kolejny cykl,
  zanim ktoś na niego wpadnie, a nie po czwartym razie jak przy `husarz.ssrf`. Drugi test
  pilnuje, by nowy pakiet MUSIAŁ dostać warstwę — inaczej pierwszy cicho by go pomijał.
  Ten drugi od razu się przydał: wykrył pominięty `husarz.attachments`.

### Dodane (profile `prod` i `airgap` uruchomione po raz pierwszy)
- Oba profile wdrożeniowe były dotąd sprawdzane wyłącznie przez parsowanie YAML-a.
  **`prod` zweryfikowany w komplecie:** `"profile": "prod"`, API nie publikuje portów
  (`8000/tcp`, bez mapowania), nieosiągalne bezpośrednio z hosta (`HTTP 000` — jedynym
  wejściem jest Caddy), bez tokenu **401**, z tokenem **200**. Wzorzec z `prod` okazał się
  poprawny: sprzeczności `ports` + `internal` tam nie ma, bo API portów nie publikuje.

### Poprawione (profil `airgap` NIE spełniał własnej obietnicy)
- Nakładka deklarowała `ports: "127.0.0.1:8000:8000"` i zapowiadała „dostęp do API wyłącznie
  przez loopback HOSTA", ale dziedziczyła `internal: true` z base. Kontener wstawał jako
  `healthy` z poprawnym profilem w środku, a `curl` z hosta nie łączył się w ogóle. Nakładka
  nadpisuje teraz `internal: false`; uzasadnienie w pliku i w `BEZPIECZENSTWO.md`: na maszynie
  faktycznie odciętej nie ma trasy do WAN, a profil `airgap` w configu wymusza przy starcie
  deny-all egress, pustą allowlistę, brak sieci w sandboxie i lokalne endpointy modeli.
- **Luka we własnym teście z poprzedniego commita.** Niezmiennik sprawdzał wyłącznie główny
  `docker-compose.yaml`, więc przegapił nakładkę. Dołożony `test_overlay_profiles_have_no_port_contradiction`
  scala nakładki z base — bo `internal` bywa nadpisywane właśnie tam.

### Poprawione (tag obrazu w compose kłamał o wersji)
- `docker-compose.base.yml` przypinał `husarz-api:0.1.0`, gdy projekt był w **0.14.0**.
  Compose sam buduje ten obraz i nadaje mu etykietę, więc wdrożony artefakt niósł nieprawdziwą
  wersję. Tag jest teraz parametrem `HUSARZ_IMAGE_TAG` z domyślną wartością sparowaną
  z `husarz.__version__`; `test_compose_image_tag_matches_project_version` pilnuje, żeby nie
  rozjechał się przy kolejnym wydaniu — bez tego rozjazd wraca, bo nikt nie pamięta o pliku
  wdrożeniowym. Wpis dodany do `.env.example`.

### Poprawione (profil `dev` w compose produkował kontener NIEOSIĄGALNY z hosta)
- `docker compose up -d` dawał kontener w stanie **`healthy`**, do którego nie dało się wejść.
  API działało (sprawdzone od środka kontenera), ale nie było do niego drogi. Przyczyna:
  sprzeczność w dostarczonym pliku — `ports: "127.0.0.1:8000:8000"` obok sieci z
  `internal: true`. **Docker cicho wyłącza publikowanie portów dla sieci internal**: zamiast
  `127.0.0.1:8000->8000/tcp` raportuje samo `8000/tcp`. Ten sam mechanizm odcinał dostęp do
  modelu na hoście, więc czat w tym profilu też nie miał prawa działać.
- **Testy to utrwalały.** `test_deploy_invariants.py` asertował OBIE wartości naraz, nie
  zauważając, że się wykluczają — statyczna asercja przechodziła, a rzecz nie działała.
  Zastąpione niezmiennikiem `test_published_ports_are_not_paired_with_internal_only_network`,
  który odrzuca każdą usługę publikującą porty wyłącznie w sieci internal. Nośność
  potwierdzona: przywrócenie `internal: true` czerwieni test.
- Sieć profilu `dev` nie jest już `internal`. Świadomy kompromis udokumentowany w pliku: dev
  nie ma proxy do mostkowania (w `prod` robi to Caddy w `husarz_edge`, a API pozostaje
  wewnętrzne — tam sprzeczności nie było), a egress egzekwuje warstwa aplikacji (deny-all +
  allowlista + pinowanie IP). Wymuszenie sieciowe zostaje w k8s NetworkPolicy i profilu airgap.
- Zweryfikowane po naprawie: `127.0.0.1:8000->8000/tcp`, health OK, konsola 200, 7 agentów.

### Poprawione (cykl importów `husarz.ssrf` — dług, który kosztował cztery razy)
- **Rozcięty utajony cykl** `ssrf → router.egress → router.__init__ → router.client → ssrf`.
  `husarz.ssrf` jest warstwą NIŻSZĄ niż router (korzystają z niego narzędzia, wtyczki MCP,
  klient Gita, embedder), a mimo to importował z niego `EgressError`; import podmodułu pociąga
  w Pythonie import pakietu nadrzędnego. Działało wyłącznie dlatego, że router bywał
  importowany pierwszy — każdy nowy moduł sięgający do `ssrf` wcześniej wywracał się na
  `ImportError`. Zdarzyło się to CZTERY razy (diagnostyka launchera, warstwa ewaluacji,
  dwa skrypty diagnostyczne), zanim usunięto przyczynę.
- `RouterError` i `EgressError` mieszkają teraz w `husarz.core.errors` (sam stdlib), a
  `husarz.router.errors` i `husarz.router.egress` je **re-eksportują**. Wszystkie 19 istniejących
  importów działa bez zmian, klasy są tożsame (`is`), a dziedziczenie zachowane — istotne, bo
  `husarz.api.app` łapie `RouterError` i mapuje go na kod HTTP.
- Testy: +8 (`tests/unit/test_import_layering.py`) — import sześciu modułów niskopoziomowych
  w ŚWIEŻYM procesie (w jednej sesji pytest inny test mógłby zamaskować cykl), kontrola na
  poziomie źródła oraz tożsamości klas po re-eksporcie.

### Dodane (obraz `husarz-api` — hardening zweryfikowany na kontenerze)
- **Niezmienniki obrazu sprawdzone na uruchomionym kontenerze**, nie przez parsowanie plików
  wdrożeniowych: non-root (`uid=1000(husarz)`), rootfs niezapisywalny, konfiguracja działa
  wewnątrz obrazu, liveness otwarty, `/api/agents` bez tokenu → **401**, z tokenem → 7 agentów,
  konsola serwowana.
- Najważniejsze: **fail-closed przy nasłuchu poza loopbackiem działa też w kontenerze.**
  Konteneryzacja z natury nasłuchuje na `0.0.0.0`, więc bez tej bramki pierwsze `docker run`
  wystawiłoby nieuwierzytelnione API. Kontener odmawia startu z czytelnym komunikatem.
- Testy: +4 (`tests/integration/test_api_image.py`), pomijane bez obrazu.

### Poprawione (obrazu NIE DAŁO SIĘ zbudować na macOS)
- `docker build` przerywa przy wysyłaniu kontekstu: `failed to xattr ._CHANGELOG.md:
  operation not permitted`. Sidecary AppleDouble (`._*`), które macOS tworzy na wolumenach
  bez natywnych atrybutów rozszerzonych, uniemożliwiają budowę obrazu z takiego wolumenu.
  Wada niewidoczna w CI, bo Linux tych plików nie tworzy.
- **SPROSTOWANIE do poprzedniego wpisu**: napisano tam, że problem rozwiązuje dodanie `._*`
  do `.dockerignore`. To NIEPRAWDA — zweryfikowane empirycznie: z sidecarem build kończy się
  kodem 1 mimo wpisu, bez sidecara kodem 0. Błąd powstaje w nadawcy kontekstu, zanim reguły
  ignorowania zostaną zastosowane. Wpis zostaje (jest poprawny co do zasady), ale realnym
  rozwiązaniem jest usunięcie plików przed budową — a że odrastają przy każdym zapisie,
  jest to krok do POWTARZANIA, nie jednorazowe sprzątanie.
- Nowy `scripts/clean_sidecars.py` (narzędzie operatora): kasuje wyłącznie pliki o sygnaturze
  AppleDouble `0x00051607` i pomija wnętrze `.git` — skasowanie tam `._pack-*.idx` potrafi
  uszkodzić indeks paczek. Tryb `--dry-run`. Pliki o zbieżnej nazwie, ale innej treści, są
  raportowane i nietknięte.

### Dodane (realny sandbox — pierwsza weryfikacja na silniku, nie po `argv`)
- **Izolacja sandboxa zweryfikowana na PRAWDZIWYM kontenerze.** Dotąd sprawdzaliśmy wyłącznie
  `argv` (`build_docker_argv`) — czyli to, o co PROSIMY Dockera, a nie to, co silnik
  faktycznie egzekwuje. Cała warstwa L2 stała na niesprawdzonym założeniu. Potwierdzone
  skutki: non-root (`uid=1000`), **brak sieci** (`urlopen` na literał IP pada), rootfs
  tylko-do-odczytu, `/tmp` mimo to zapisywalny, montaż workspace, `run_tests` przez pełną
  warstwę narzędzi (`exit=0` / `exit=1`). Testy: `tests/integration/test_sandbox_real.py`,
  pomijane z czytelnym powodem bez Dockera — nigdy nie udają sukcesu.
- **Weryfikator `tests`** w warstwie ewaluacji — czwarty i ostatni z planowanych. Porównuje
  KOD WYJŚCIA zestawu z oczekiwanym; to najtwardszy sygnał deterministyczny, jaki mamy.
  Świadomie NIE podstawia atrapy egzekutora: sensem przypadku jest to, że polecenie NAPRAWDĘ
  się wykonało. Brak Dockera → niezdany przypadek z powodem, nigdy fałszywy sukces. Nie trafia
  do dostarczonego zestawu `podstawowy`, bo bramka CI ma działać bezwarunkowo.
- Nowe `ToolDispatcher.supports(tool, action)` wykorzystane też przez ten weryfikator.

### Poprawione (ciche pomijanie nieznanych argumentów narzędzia)
- Docstring `ToolDispatcher.dispatch` obiecywał „Nieznane tool/action/args → `ok=False`",
  ale nieznane **argumenty** były w rzeczywistości po cichu odrzucane. Model proszący
  o `run_tests.run(path="x")` dostawał przebieg CAŁEGO zestawu i był przekonany, że zawęził
  zakres. Wykryte dopiero przy uruchomieniu z realnym Dockerem. To ta sama klasa wady, którą
  projekt domknął w Etapie 3b dla martwych kluczy configu: wejście, które WYGLĄDA na
  znaczące, a jest ignorowane.
- Argumenty są teraz walidowane wobec `ActionSpec.params` — zbioru zamkniętego, identycznego
  z tym, który model widzi w manuale, więc komunikat jest dla niego wprost poprawialny:
  `Akcja 'run_tests.run' nie przyjmuje argumentów: path. Dozwolone: extra_args.`
- Klasa `SuiteCase` (a nie `TestsCase`) — nazwa zaczynająca się od `Test` byłaby zbierana
  przez pytest jako klasa testowa i zaśmiecała przebieg ostrzeżeniem.

### Dodane (Etap 16, krok 3 — pomiar orkiestracji, bramka w CI)
- **`OrchestrationRecord`** — pomiar warstwy wyżej niż `RunRecord`: kroki planu, delegacje,
  kroki wskazujące nieistniejącego agenta, pominięcia i odmowy ROE, rundy refleksji, tokeny.
  Metryka `plan_validity` liczona po krokach PIERWOTNEGO planu.
- Zliczanie **strukturalne** (`_Tally`) w miejscu podjęcia decyzji, a nie przez porównywanie
  treści obserwacji z napisami `SKIPPED_*` — te napisy widzi model i mogą się zmienić przy
  pierwszej korekcie językowej.
- Wspólna fabryka `build_run_store_from_config` dla pętli, orkiestratora i API — jedno miejsce
  rozwiązywania ścieżki, więc rekordy agenta i orkiestracji trafiają do tego samego pliku
  i łączą się po `run_id`. Zweryfikowane na żywo.
- **`husarz eval` wpięte w OBA pipeline'y CI** (GitHub + GitLab) plus test-niezmiennik
  pilnujący, że nikt go po cichu nie usunie z żadnego z nich.
- Nowe `ToolDispatcher.supports(tool, action)` — introspekcja wywoływalności pary.

### Poprawione (adwersaryjny przegląd Etapu 16 — 29 agentów, 12 potwierdzonych, 3 blokujące)
- **BLOKER: ewaluacja naprawdę uruchamiała Dockera.** `build_tools` bez jawnego egzekutora
  podstawia `DockerSandboxExecutor` (`tools/loader.py:171`), więc przypadek `expect: allowed`
  dla `shell`/`run_tests` wywołałby `docker run` — w CI i na maszynie operatora, wbrew
  obietnicy „bez modelu, GPU i sieci" z `docs/EWALUACJA.md`. Zweryfikowane empirycznie
  (1 konstrukcja bez egzekutora, 0 z jawnym). Wstrzyknięte atrapy sandboxa i warstwy HTTP.
- **BLOKER: nazwy narzędzia i akcji były kanałem na treść modelu.** Pola `tool`/`action`
  pochodzą z bloku akcji, czyli OD MODELU; zapisywane wprost dawały 64-znakowy kanał na
  dowolny tekst w pliku, który z założenia treści nie niesie. Wpuszczamy je teraz wyłącznie
  ze zbiorów zamkniętych (allowlista agenta / rejestr akcji), inaczej `<nieznane>`.
  Sprostowane też nieprawdziwe zdanie w `BEZPIECZENSTWO.md` — sama nieobecność pola
  tekstowego NIE wystarczała.
- **BLOKER: literówka w akcji dawała fałszywy sukces.** `expect: allowed` przechodziło dla
  `rag.searchh`, bo bramka takiej pary nie blokuje, a nieistnienie akcji było niewidoczne.
  Teraz przypadek jest odrzucany jako błąd zestawu.
- **Pusta bramka świeciła na zielono.** Brak podkatalogu `evals/` (obraz Docker, zły wolumen)
  albo zestaw bez przypadków dawały kod 0 — CI meldowało sukces, choć nic nie sprawdzono.
  Teraz kod 1, z jawną furtką `--allow-empty`.
- **Metryki kłamały w dwóch miejscach.** `tool_calls`/`failed_tool_calls` wliczały odmowy
  bramki, więc wskaźnik awaryjności rósł wprost proporcjonalnie do skuteczności allowlisty —
  metryka nagradzała słabsze zabezpieczenie. `plan_validity` liczyło kroki refleksji jako
  kroki planu; na realnym przebiegu dawało 0,50 zamiast 1,00, obwiniając planistę za błąd
  popełniony w innej fazie.
- **Tura wyczerpująca budżet znikała ze `steps`** — zaniżała sumę tur i zawyżała
  `malformed_ratio`, bo mianownik był mniejszy.
- **Agent `roe_required` nie dawał się zmierzyć.** Nie wchodzi w pętlę (L0), więc nie ma tury
  ACTION, choć narzędzie jest skutecznie zablokowane — niezmiennika „Puszkarz nie ma narzędzi"
  nie dało się wyrazić zdawalnym przypadkiem w ŻADNEJ konfiguracji. `Termination.ROE_REFUSED`
  liczy się teraz jako `denied`.
- Plik pomiarowy tworzony z prawami `0600` w katalogu `0700`; doprecyzowany zakres blokady
  zapisu (chroni przed przeplotem w jednym procesie; między procesami polega na `O_APPEND`).
- Zawężony docstring `denied_tool_calls` do warstwy L1 + sekcja o granicach tej metryki
  w `docs/EWALUACJA.md` — blokady egress i pinowania IP trafiają do `failed_tool_calls`.
- Testy: +11 regresyjnych, każdy z potwierdzoną nośnością (cofnięcie poprawki czerwieni test).

### Dodane (Etap 16, krok 2 — warstwa ewaluacji i podkomenda `husarz eval`)
- **Zestawy ewaluacyjne** w `config/evals/*.yaml` (nowa sekcja wieloplikowa `evals`, wczytywana
  przez loader jak agenci i narzędzia). Literówka w polu albo w rodzaju przypadku jest błędem
  walidacji przy starcie, nie cichym pominięciem.
- **Podkomenda `husarz eval`** — wypisuje raport per przypadek i zwraca kod wyjścia `1`, gdy
  choć jeden nie przeszedł. Nadaje się wprost na bramkę CI: nie woła modelu, nie otwiera gniazd,
  nie potrzebuje GPU. Flagi: `--config`, `--prompts`, `--set`.
- **Weryfikator `routing`** — czy agent trafi na oczekiwany model. Liczony czystą funkcją
  (`select_candidates`), w mikrosekundach. Wychwytuje tę samą klasę wady, którą naprawiał commit
  o panelu Agenci: rozjazd między deklaracją w configu a realnym wyborem routera.
- **Weryfikator `tool_policy`** — uruchamia PRAWDZIWĄ pętlę narzędziową ze skryptowanym routerem
  i sprawdza werdykt bramki (allowlista agenta, ROE, budżet). Skryptowany router jest konieczny:
  bramka bezpieczeństwa musi dawać ten sam werdykt zawsze, a nie zależeć od tego, czy model dziś
  zechce poprosić o narzędzie.
- Agent bez `tool_loop_enabled` raportowany jest **uczciwie** („ma wyłączoną pętlę narzędziową"),
  a nie jako fałszywe „zablokowano" — fałszywy pomiar byłby gorszy niż brak pomiaru.
- Weryfikator NIGDY nie rzuca: wyjątek w jednym przypadku staje się niezdanym przypadkiem
  z komunikatem, a nie przerwaniem całego zestawu.
- Dostarczony zestaw `podstawowy` (3 przypadki routingu) przechodzi; test pilnuje, że przechodzi —
  publikowanie czerwonej bramki byłoby gorsze niż jej brak.
- Docs: nowa sekcja `docs/EWALUACJA.md` z jawnym akapitem **„czego ta warstwa jeszcze NIE mierzy"**.
- Testy: +12 (`tests/unit/test_eval.py`), w tym trzy testy nośności (złe oczekiwanie MUSI paść).

### Zmienione (architektura — modele zestawów w warstwie konfiguracji)
- `husarz.config.evals` zamiast `husarz.eval.cases`: modele konfiguracji należą do warstwy
  konfiguracji. Trzymanie ich w pakiecie wykonawczym tworzyło cykl importów
  (`config.schema` → `eval` → `agents` → `config.schema`) — drugi taki cykl w tym projekcie,
  po `husarz.ssrf`. Moduł zależy wyłącznie od pydantica.

### Dodane (Etap 16, krok 1 — materializacja przebiegu agenta)
- Nowy pakiet **`husarz.runs`**: `RunRecord` (przebieg jednego agenta), `RunStep` (tura),
  `Termination` (powód zakończenia: `final`/`iteration_limit`/`budget`/`roe_refused`) oraz
  magazyn za protokołem `RunStore` — `NullRunStore` (domyślny, nic nie zapisuje) i
  `JsonlRunStore` (JSONL, ten sam format co dziennik audytu, żeby `grep`/`jq` działały na obu).
- **Rekord niesie METRYKI, nie TREŚĆ** — struktura fizycznie nie ma pola na tekst. Zapisujemy
  rodzaj tury, narzędzie i akcję, wynik, czy zablokowała bramka, długości w znakach, tokeny.
  Nie zapisujemy zadania, odpowiedzi modelu ani argumentów narzędzi. To bezpieczeństwo
  z konstrukcji: konfiguracja może zostać źle ustawiona, typ nie.
- Metryki pochodne na rekordzie: `malformed_ratio` (jak model radzi sobie z protokołem — bez
  dzielenia przez zero dla przebiegu bez tur), `tool_calls`, `failed_tool_calls`,
  `denied_tool_calls`. Trzy z czterech weryfikatorów Etapu 16 liczą się z nich wprost.
- Wpięte w `ToolLoop` (wszystkie cztery ścieżki wyjścia) i w orkiestrator: `run()` nadaje
  `run_id`, który dziedziczą **wszystkie** przebiegi tej orkiestracji — stąd semantyka grupy,
  bez której nie da się porównać N przebiegów tego samego scenariusza. `run_id` jest jawnym
  parametrem, żeby testy pozostały deterministyczne.
- **Domyślnie wyłączone** (`platform.runs.enabled: false`) — opt-in, jak pętla narzędziowa
  i pamięć trwała. To NIE telemetria: lokalny plik w `data_dir` (gitignored), nic nie opuszcza
  maszyny. `platform.telemetry_enabled` nadal twardo odrzuca `true`.
- Zapis nie może wywrócić pracy agenta: `JsonlRunStore.save` połyka `OSError`. Utrata pomiaru
  jest kosztem akceptowalnym, utrata odpowiedzi agenta nie jest.
- Testy: +21 (`tests/unit/test_runs.py` 14, `tests/security/test_runs_privacy.py` 7). Nośność
  potwierdzona: po dodaniu do rekordu tymczasowego pola `task` czerwienieją 3 testy na trzech
  niezależnych osiach. Docs: notatka weryfikacyjna w `BEZPIECZENSTWO.md`.

### Poprawione (rozliczalność i pomiar — dwa drobiazgi domknięte przy Etapie 16)
- `toolloop.limit` jako **jedyny** wpis audytu w pętli narzędziowej nie przekazywał
  `principal` — „osiągnięto limit iteracji" gubiło informację, na czyje żądanie przebieg
  powstał. Domknięte.
- Odmowa spoza allowlisty agenta sygnalizowana jest teraz strukturalnie
  (`ToolResult.metadata["denied"]`), a nie treścią komunikatu błędu. Pomiar nie może zależeć
  od brzmienia napisu, który widzi model.

### Dodane (ADR-0022 — kryteria przyjmowania narzędzi zewnętrznych, dwa odrzucenia)
- **ADR-0022** zapisuje **pięć kryteriów odrzucenia** narzędzia zewnętrznego (cofnięcie
  deklaracji platformy, niepoliczalna powierzchnia wyjścia, wymóg sieci/uprawnień tam, gdzie
  deklarujemy ich brak, ryzyko regulaminowe, nieproporcjonalny dług) oraz **regułę pozytywną**:
  pomysł wolno przejąć zawsze, gdy licencja pozwala — przepisany po swojemu w Pythonie
  dziedziczy nasze bramki (egress, `UsageMeter`, audyt, RBAC) zamiast je omijać.
- Udokumentowane odrzucenie **OpenPipe ART** (Apache-2.0): `requires-python >= 3.12` wobec
  naszego `>= 3.11`; zależności bazowe z czterema klientami usług chmurowych; RULER woła
  sędziego przez `litellm`, omijając nasz `Router`, bramkę egress, `UsageMeter` i audyt.
  Do przejęcia jako pomysł: relatywne ocenianie grupowe i zasada, że twardy sygnał
  deterministyczny nigdy nie jest nadpisywany oceną sędziego LLM.
- Udokumentowane odrzucenie **OmniRoute** (MIT): to aplikacja Next.js, nie biblioteka (brak
  `main`/`exports`/`types`); 79 zależności runtime i Node ≥ 22; stan runtime wyłącznie
  w SQLite, więc konfiguracji nie da się wersjonować; `postinstall` wykonuje kod i pobiera
  binaria; MITM/TPROXY z instalatorem własnego CA. Rozstrzygające: komponenty do obchodzenia
  zabezpieczeń antybotowych konsumenckich interfejsów webowych. Żaden z podprojektów
  (`opencode-plugin`, `opencode-provider` — *deprecated*, `browser-pool`, `open-sse`,
  `electron`, `skills`) nie ma samodzielnej wartości dla Husarza.
- Trzy niezależne analizy wskazały ten sam brak — **warstwę pomiaru jakości**. To czyni ją
  najlepiej uzasadnionym kolejnym etapem.

### Dodane (BEZPIECZENSTWO — granice walidacji airgap dla endpointów modeli)
- Notatka utrwala **świadomą asymetrię** progów w walidacji krzyżowej profilu `airgap`:
  modele i embedder `rag` przechodzą przez `is_local_endpoint` (loopback + cały prywatny LAN
  + `.local`/`.internal`), a wtyczki MCP przez ostrzejsze `is_loopback_endpoint`. Szerszy próg
  dla modeli jest poprawny — airgap oznacza brak trasy do WAN, nie brak sieci lokalnej,
  a vLLM na osobnej maszynie z GPU to normalna topologia.
- Nazwane wprost **ryzyko rezydualne**, dotąd nigdzie niezapisane: walidator sprawdza ADRES,
  nie NATURĘ usługi, więc nie odróżni serwera modeli od bramki pośredniczącej. `airgap`
  egzekwuje, że *Husarz* nie kieruje ruchu do WAN — nie egzekwuje, bo nie może, że nie robi
  tego oprogramowanie, któremu operator świadomie powierzył ruch. Pełne wymuszenie należy do
  warstwy sieciowej (Etap 6). Zmian w kodzie nie wprowadzono — zachowanie jest zamierzone.

### Dodane (rozliczalność pętli narzędziowej — `detail` w widoku audytu, allowlista)
- **`GET /api/audit` zwraca teraz `detail`** — wąski, jawnie dozwolony podzbiór szczegółów
  wpisu. Dla `tool.call` są to `tool`, `action`, `ok`, czyli odpowiedź na podstawowe pytanie
  rozliczalności: **które** narzędzie zadziałało i czy się powiodło. Dotąd widok API nie
  wystawiał szczegółów wcale, więc konsola pokazywała wiersz `tool.call` bez nazwy narzędzia —
  ta sama klasa luki co brakujący `principal` przed Etapem 13c: funkcja istniała, ale była
  niewidoczna z zewnątrz. Konsola ma kolumnę **Szczegóły**.
- **Reguła ekspozycji: allowlista, deny-by-default** (`husarz.api.audit_view.public_detail`).
  Akcja spoza mapy nie ujawnia niczego, więc nowy typ wpisu audytu nie zacznie wyciekać
  payloadu przez przeoczenie. Dodatkowo: wyłącznie wartości skalarne (zagnieżdżona struktura
  mogłaby przemycić treść pod dozwoloną nazwą) i twardy limit długości.
- Na dysku **nic się nie zmienia** — `args`, `bytes` i `pinned_ip` nadal trafiają do
  niemodyfikowalnego dziennika. Rola `audit:read` odpowiada na pytanie o rozliczalność,
  nie daje wglądu w treść wywołania.
- **Pętla narzędziowa zweryfikowana end-to-end na realnym modelu** — pierwsze w historii
  projektu wywołanie `tool.call` w dzienniku (`tool=rag`, `action=search`, `ok=true`),
  łańcuch skrótów zweryfikowany. `tool_loop_enabled: false` pozostaje domyślne w dostarczonym
  configu (deny-by-default z ADR-0016) — weryfikacja szła na configu roboczym.
- Testy: +13 (`tests/security/test_audit_view_exposure.py`, marker `security`). Nośność
  potwierdzona: po tymczasowym wyłączeniu allowlisty 8 z 13 czerwienieje, w tym wszystkie
  o wycieku `args`. Docs: notatka weryfikacyjna w `BEZPIECZENSTWO.md`, `API.md`, zrzut ekranu.

### Poprawione (przegląd pobieranego launchera — licencja bazy, kolizja portu, znikające logi)
- **`ollama/README.md` rekomendował model o licencji BADAWCZEJ.** Jako obejście buga
  sterownika Blackwell dokument podawał `FROM qwen2.5-coder:3b`. Wariant 3B — w odróżnieniu
  od 1.5B/7B/14B/32B — jest wydany na `qwen-research`, ograniczającej użycie do badań
  i ewaluacji, więc nie nadaje się do wdrożenia produkcyjnego. Rekomendacja zmieniona na
  `1.5b` (Apache-2.0, ≈1 GB, również mieści się pod limitem alokacji), 3B wykluczony wprost.
  Tabela wymiany silnika ma teraz kolumnę **Licencja bazy** i ostrzeżenie, że licencje wag
  bywają NIEJEDNOLITE w obrębie jednej rodziny modeli.
- **Kolizja portu 8000 była niewidoczna.** Launcher domyślnie nasłuchuje na 8000, a
  dostarczony `config/models.yaml` ma endpoint vLLM na `http://localhost:8000/v1` — kto
  uruchomił jedno i drugie wg naszej własnej dokumentacji, tego żądanie do modelu wracało
  do API Husarza i kończyło się mylącym błędem. Nowy moduł `husarz.launcher.diagnostics`
  (czyste funkcje, bez sieci i I/O) wykrywa to przy starcie i wypisuje ostrzeżenie z nazwą
  modelu oraz podpowiedzią naprawy. **Ostrzega, nie blokuje** — Husarz w kontenerze legalnie
  nasłuchuje na `0.0.0.0:8000`, gdy vLLM działa na `:8000` hosta, i twarda bramka wywróciłaby
  to poprawne wdrożenie. Moduł jest przygotowany pod przyszłe `husarz doctor` (jedno źródło
  dla terminala i konsoli).
- **Komunikat startowy znikał przy przekierowaniu wyjścia.** `stdout` jest buforowany
  blokowo poza terminalem, a uvicorn loguje na `stderr` — więc pod `nohup`, w kontenerze
  czy pod menedżerem usług cała linia z adresem konsoli i ostrzeżeniami nie trafiała do
  logów. Dodane `flush=True`; zweryfikowane na przekierowanym wyjściu.
- **Dokumentacja obiecywała za dużo.** `packaging/README.md` i `docs/LAUNCHER.md` mówiły
  „działa out-of-the-box"; binarka faktycznie startuje bez konfiguracji, ale nie niesie ani
  silnika Ollamy, ani wag, więc czat bez lokalnego modelu kończy się `502`. Rozdzielono
  „startuje bez konfiguracji" od „czat od razu odpowiada", z podanym kosztem wag.
- Testy: +10 (`tests/unit/test_launcher_diagnostics.py`) — obie strony kontraktu: kolizja
  wykrywana ORAZ brak fałszywych alarmów (zdalny backend, model wyłączony, inny port,
  nasłuch nie-loopback, chory URL nie może wywrócić startu).

### Dodane (weryfikacja end-to-end na realnym modelu + zrzuty ekranu)
- **Skrypt `scripts/screenshots.py`** — odświeżanie zrzutów konsoli z REALNIE uruchomionej
  aplikacji (Playwright + systemowy Chrome; narzędzie operatora, nie zależność projektu ani
  część CI). Robi komplet: Czat (z odpowiedzią modelu), Agenci, Audyt, Monitor. Czeka na
  DOKŁADNE sygnały gotowości (zniknięcie `.typing`, podmiana placeholdera `…` w panelu),
  a nie na `networkidle` — inaczej zrzuty łapały „Husarz pisze…" i puste panele.
- Dokumentacja: 3 nowe zrzuty w `docs/index.md` (Agenci, Audyt, Monitor) z opisami; procedura
  odświeżania + ostrzeżenie o przeglądzie przed commitem w `CONTRIBUTING.md`.
- Zweryfikowano na żywo (Ollama, `qwen2.5-coder:7b` → model `husarz`): czat, pełna orkiestracja
  wieloagentowa, nadpisanie runtime, łańcuch skrótów audytu i liczniki `/api/usage`.
  Potwierdzono, że backend Ollamy **raportuje `usage`** — rozliczanie tokenów z Etapu 7b
  działa na realnym modelu, a nie tylko na atrapach z testów.

### Poprawione (weryfikacja end-to-end — panel pokazywał nieaktualny model agenta)
- **Zakładka Agenci ignorowała `routing.agent_models`.** `GET /api/agents` zwracał pole `model`
  z pliku agenta, podczas gdy router liczy model z pierwszeństwem tabeli routingu
  (udokumentowanym w `docs/AGENCI.md` i `docs/ROUTER.md`). Po zmianie tabeli — w pliku albo
  nadpisaniem runtime — panel pokazywał operatorowi model, którego agent w ogóle nie użyje.
  W dostarczonym szablonie obie wartości są zgodne, więc rozjazd był niewidoczny do chwili,
  gdy ktoś użyje tabeli routingu zgodnie z jej przeznaczeniem. Reguła pierwszeństwa wyjęta do
  `husarz.router.selection.resolve_agent_model` i używana przez router ORAZ panel — żeby nie
  istniała w dwóch kopiach, które mogą się rozjechać (tak właśnie powstał ten błąd).
- **README obiecywał orkiestrację „z pudełka" na Ollamie.** Sekcja „Lokalny czat i kodowanie"
  opisywała przełącznik *Orkiestracja* tak, jakby działał po samym `ollama create` — a
  dostarczony `config/routing.yaml` kieruje agentów na modele vLLM (`glm-main`, `hermes`),
  więc hetman zwracał `502 Backend modelu zawiódł`. Dopisany brakujący krok (przypisanie
  agentów do `husarz-local` w pliku lub nadpisaniem runtime), zweryfikowany end-to-end.
- Testy: +6 (`tests/unit/test_agents_effective_model.py`), w tym niezmiennik „router i panel
  liczą model tą samą regułą" oraz test widoczności nadpisania runtime w panelu.

### Dodane (Etap 13c — korelacja principal↔wywołanie w audycie)
- Dziennik odpowiadał na pytanie „kto WYKONAŁ" (`actor`: `kopijnik`, `puszkarz`, `api`), ale
  nie „na czyje ŻĄDANIE". Przy jednym operatorze bez znaczenia; przy wielu kontach audyt
  przestawał być śladem **rozliczalności** — nie dało się powiązać wywołania narzędzia
  z użytkownikiem, który je zlecił.
- `AuditEntry` niesie pole `principal`, przewleczone od API przez orkiestrator, pętlę
  narzędziową, `RoeRuntime`, `RoeGate` i `Puszkarza` — więc wpisy Z GŁĘBI orkiestracji też
  wiedzą, na czyje żądanie powstały.
- **Objęte łańcuchem skrótów**: dopisanie albo usunięcie `principal` w istniejącym wpisie
  unieważnia skrót. Nie da się „odpiąć" wywołania od użytkownika ani podpiąć pod kogo innego
  bez wykrycia przez `verify`.
- **Zgodność wstecz**: payload pomija `principal`, gdy jest pusty, więc dzienniki sprzed tej
  zmiany hashują się dokładnie tak jak wcześniej i nadal przechodzą `verify`. Bez tego
  aktualizacja Husarza sprawiłaby, że każdy istniejący dziennik wygląda na zmanipulowany.
- **Bez PII w niemodyfikowalnym logu**: referencja to ID konta (`user:<id>`), nie nazwa
  użytkownika (bywa e-mailem). Token maszynowy zapisujemy jako `token:<rola>`, żeby odróżnić
  wywołanie automatu od wywołania człowieka.
- Testy: +8 (m.in. wykrycie podmiany i usunięcia pola, weryfikacja starego formatu,
  end-to-end przez `/api/chat` i `/api/orchestrate`).

### Poprawione (Etap 3b — typowane ustawienia narzędzi; MARTWE klucze w dostarczonym configu)
- **`ToolConfig.config` było nietypowaną mapą**, walidowaną dopiero (i tylko częściowo) przy
  budowie narzędzia. Skutek okazał się gorszy niż literówki: **dostarczana konfiguracja
  zawierała 10 kluczy, których NIKT nie czyta** — w tym takie, które wyglądają jak kontrole
  bezpieczeństwa. `config/tools/shell.yaml` miał `network: false`, `cpu_limit`, `memory_limit`
  i `timeout_seconds`; operator mógł sądzić, że wyłączył narzędziu sieć albo ograniczył zasoby,
  a **jedynym realnym sterowaniem jest `security.sandbox`**. Analogicznie ignorowane były
  `file_edit.root`, `git.workdir`, `run_tests.workdir`/`timeout_seconds` i `web.method`.
- Nowe modele ustawień per `kind` (`FileEditSettings`, `ShellSettings`, `GitToolSettings`,
  `RunTestsSettings`, `WebToolSettings`, `PluginToolSettings`, `RagBackendConfig`) walidowane
  przy STARCIE z `extra="forbid"`. Nieznany klucz = czytelny błąd z nazwą narzędzia i rodzaju,
  zamiast cichego no-opu. Buildery czytają typowany obiekt (`settings_as`), nie surową mapę;
  helper `_int_setting` zniknął.
- `ShellSettings` jest świadomie **puste**: izolacja ma jedno źródło prawdy (`security.sandbox`).
  Duplikat per narzędzie dawałby dwa miejsca do rozjechania — przy kontroli bezpieczeństwa
  o jedno za dużo.
- Dostarczone `config/tools/*.yaml` wyczyszczone z martwych kluczy, z komentarzem wskazującym,
  gdzie leży realne sterowanie (żeby nikt nie skopiował ich z powrotem).
- **Loader**: pliki `._<nazwa>.yaml` (sidecary AppleDouble tworzone przez macOS na woluminach
  exFAT/NTFS) są pomijane, a plik nie-UTF-8 daje czytelny `ConfigError` zamiast surowego
  `UnicodeDecodeError` — start wywracał się wtedy wbrew zasadzie „błąd configu = czytelny
  komunikat, nigdy niekontrolowany crash".
- Testy: +22. **BREAKING**: konfiguracja z nieznanym kluczem w `config` narzędzia nie wystartuje.
  **Migracja:** usuń zgłoszony klucz — on i tak nic nie robił. Limity sandboxa ustaw
  w `security.sandbox`, katalog roboczy w `platform.workspace_dir`.

### Poprawione (Etap 7b — rozliczanie tokenów orkiestracji, dziura w limitach)
- **`/api/orchestrate` SPRAWDZAŁ limit tokenów, ale go NIE naliczał.** Rozliczenie
  (`_record_tokens`) istniało wyłącznie na ścieżce `/api/chat`, więc `tokens_used` konta
  nie rosło przy orkiestracji — a `check_quota` porównuje właśnie tę wartość z kwotą.
  Konto z ustawionym limitem mogło orkiestrować **bez końca**, i to na najdroższym
  endpoincie: jedno żądanie to plan + N delegacji + refleksja + synteza, czyli wiele
  wywołań modelu (a z pętlą narzędziową — wiele wywołań na KAŻDĄ delegację).
- Nowy `husarz.router.types.UsageMeter` — sumator zużycia dla jednej operacji, świeży per
  żądanie (jak `ToolCallBudget`, nigdy współdzielony między wątkami). Przewleczony przez
  wszystkie fazy: `AgentResult.usage` ← `BaseAgent.run`, pętla narzędziowa sumuje po
  iteracjach, orkiestrator dolicza plan/refleksję/syntezę i zwraca sumę w
  `OrchestratorResult.usage`; endpoint nalicza ją na koncie.
- Rozróżnienie „zero tokenów" od „backend nie raportuje": `snapshot()` zwraca `None`, gdy
  ŻADNE wywołanie nie zgłosiło zużycia — nie naliczamy zmyślonych wartości.
- Testy: +8, w tym regresja udowodniona empirycznie (po cofnięciu poprawki dwa testy padają).

### Dodane (Etap 4c — wpięcie ROE-gate w runtime orkiestratora)
- Domknięty ostatni krok Etapu 4. `RoeGate` był kompletny, ale **nieużywany**: orkiestrator
  twardo pomijał agentów `roe_required`, więc ani bramka, ani weryfikacja podpisu z 4b nie
  miały konsumenta. Teraz podpis jest **nośny** — decyduje o delegacji Puszkarza.
- Nowy `husarz.security.roe_runtime` (`RoeRuntime`, `build_roe_runtime`): bramka per zlecenie
  z weryfikatorem podpisu + bezwarunkowy przegląd odmowy ofensywy. Wpięty w `build_orchestrator`
  i przebudowywany przy `POST /api/config/runtime` (jak router i wtyczki).
- **Bez nowych zdolności ofensywnych**: Puszkarz nie ma narzędzi (pętla wyklucza `roe_required`
  na L0), więc pod ważnym zleceniem wytwarza wyłącznie analizę tekstową w dry-run. Zmiana to
  „nie działa nigdy" → „działa wyłącznie pod zweryfikowanym zleceniem, bez narzędzi, w dry-run".
- Bramka na poziomie DELEGACJI odpowiada „czy istnieje ważne zlecenie", a nie „czy wolno
  zaatakować cel X" — świadomie, bo zadanie kroku planu to wolny tekst od modelu, a wyłuskiwanie
  z niego celu byłoby autoryzacją sterowaną przez model. Autoryzacja na cel zostaje
  w `RoeGate.evaluate` (gotowa, pokryta testami, bez konsumenta do czasu nadania zdolności).
- Agentowi wstrzykiwana jest notatka kontekstowa o trybie dry-run i braku narzędzi — inaczej
  model mógłby raportować działania, których nie wykonał, a to trafiłoby do syntezy jako fakt.
- `RoeGate.engagement_decision` wydzielone z `evaluate` (jedna implementacja trzech bram:
  zgoda + podpis + okno). `Puszkarz` przyjmuje bramkę opcjonalną — odmowa ofensywy musi
  działać także bez zleceń. Testy: +11 (łącznie 859). Docs: BEZPIECZENSTWO.md, ADR-0021.

### Dodane (Etap 4b — kryptograficzny podpis ROE)
- Domknięta ostatnia otwarta pozycja rdzenia bezpieczeństwa (Etap 4). ROE to JEDYNY artefakt
  uprawniający Puszkarza do aktywnych działań wobec konkretnych celów, a jego ważność
  sprowadzała się do „pole `signature` jest niepuste" — dopisanie `signature: "abc"` czyniło
  zlecenie ważnym. Kto mógł edytować plik, mógł poszerzyć zakres; skutkiem byłby **atak na
  osobę trzecią z użyciem Husarza jako narzędzia**. Docs: ADR-0021.
- Nowy `husarz.security.roe_signature`: kanoniczny payload + `hmac-sha256` (stdlib) i
  `ed25519` (extra `husarz[roe]`, klucz PRYWATNY zostaje u zatwierdzającego — runtime widzi
  tylko publiczny). `build_roe_verifier` spina config → sekrety → `RoeGate`.
- **Podpisujemy TREŚĆ, nie plik**: payload liczony z EFEKTYWNEGO `RoeConfig`, więc poszerzenie
  zakresu przez `POST /api/config/runtime` (które nie zmienia ani jednego bajtu na dysku)
  również unieważnia podpis. Separacja domen prefiksem `husarz-roe-v1`.
- Fail-closed w każdym rozgałęzieniu: zły format/base64/algorytm/klucz → odmowa; downgrade-guard
  (algorytm z pliku musi zgadzać się z configiem); `verify_signature=true` bez `key_ref` →
  błąd STARTU; nierozwiązywalny klucz w runtime → wyjątek, nigdy „przepuść". Klucz rozwiązywany
  leniwie przy każdej weryfikacji (rotacja bez restartu).
- Config `security.roe` (`verify_signature`, `algorithm`, `key_ref` — wyłącznie REFERENCJA do
  sekretu). Profile `prod`/`airgap` z aktywnym zleceniem (`consent: true`) wymagają weryfikacji
  i klucza; sam szablon bez zgody nie wymusza niczego (zero friction dla wdrożeń bez zleceń).
- **Narzędzie operatora** `husarz roe sign|verify` — bez niego włączenie weryfikacji byłoby
  wyłącznie sposobem na unieruchomienie ROE (nie dałoby się wytworzyć poprawnego podpisu).
  `verify` zwraca kod 0 (ważny) / 2 (odrzucony) — nadaje się do CI.
- Testy: +40 (offline), w tym parametryczne wykrywanie manipulacji każdego pola, round-trip
  Ed25519 (PEM i base64), walidacja krzyżowa profili i pętla CLI. Łącznie 837.

### Poprawione (adwersaryjny przegląd Etapu 4b — 3 soczewki, 12 potwierdzonych findingów)
- **FAIL-OPEN w `out_of_scope` (major)**: niewyrównany CIDR (`192.0.2.5/29`), pusty wpis albo
  śmieć były CICHO ignorowane przez dopasowanie — wykluczenie po prostu znikało, czyli zakres
  się POSZERZAŁ. Gorzej: operator podpisywał kryptograficznie dokument, którego semantyka
  różniła się od tego, co czytał. Teraz walidacja odrzuca takie wpisy przy starcie.
- **Normalizacja wpisów zakresu (major)**: `_target_matches_entry` normalizował tylko CEL,
  a wpis brał dosłownie — więc `" db.example.local "`, `"db.example.local."` czy wpis
  z portem/schematem nie dopasowywał się. Dla `targets_*` to ciche zawężenie, ale dla
  `out_of_scope` — odsłonięcie chronionego hosta. Obie strony przechodzą teraz to samo.
- **Kotwica profilu (major)**: `platform.profile` dało się nadpisać przez
  `POST /api/config/runtime`, a to na nim opiera się CAŁA bazowa linia prod/airgap
  (sandbox, audyt, szyfrowanie, podpis ROE). Jedno żądanie `{"platform":{"profile":"dev"}}`
  wyłączało je wszystkie naraz. Endpoint odrzuca teraz takie nadpisanie.
- **`husarz up` degradował profil (major)**: `--profile` miał domyślną wartość `dev`
  wstrzykiwaną jako nadpisanie runtime, więc konfiguracja z `profile: prod` startowała jako
  dev. Bez jawnej flagi profil NIE jest już nadpisywany.
- **Kolizja nazw w ENV (major)**: `HUSARZ_SECURITY__ROE__VERIFY_SIGNATURE` nie działało —
  `roe` jest też nazwą kolekcji zleceń, a loader decydował o zachowaniu wielkości liter po
  SAMEJ nazwie segmentu, nie po ścieżce. Regresja wprowadzona przez nową sekcję `security.roe`.
- **CLI (minor)**: `roe sign --algorithm` niezgodny z configiem kończy się błędem (runtime
  odrzuciłby taki podpis przez downgrade-guard); `roe sign` ostrzega, gdy w środowisku są
  nadpisania `HUSARZ_ROE__*` (podpis obejmuje treść EFEKTYWNĄ, nie sam plik).
- **Widoczność (minor)**: `husarz validate` pokazuje stan weryfikacji podpisu ROE — wyłączona
  weryfikacja była dotąd niewidoczna poza lekturą YAML-a, mimo że degraduje jedyny prymityw
  autoryzacji. Przy wyłączeniu wypisywane są też nazwy zleceń ze zgodą.
- **Audyt (minor)**: błąd weryfikatora (np. zniknął sekret z `key_ref`) kończy się odmową
  Z WPISEM w audycie, a nie wyjątkiem uciekającym z `evaluate` — niezmiennikiem bramki jest,
  że KAŻDA decyzja zostawia ślad.
- **Dokumentacja (nit)**: README przedstawiał podpis ROE jako aktywną gwarancję runtime,
  choć `RoeGate` nie jest jeszcze wpięty — dopisane zastrzeżenie.
- Testy: +9 regresyjnych (łącznie 848).

### Zmienione (Etap 4b — zmiana zachowania, ścieżka aktualizacji)
- Dotychczasowe „podpisy" (dowolny tekst w polu `signature`) **przestają być ważne**. Dotyczy
  każdego zlecenia z `consent: true`. **Migracja:** ustaw `security.roe.key_ref`, wygeneruj
  podpis (`husarz roe sign --engagement <id>`) i wklej wynik do pliku ROE. To nie regresja —
  to koniec akceptowania czegoś, co nigdy nie było podpisem.
- Nowy opcjonalny extra `husarz[roe]` (`cryptography`) — potrzebny WYŁĄCZNIE dla `ed25519`;
  wariant `hmac-sha256` działa na samej stdlib.

### Dodane (Etap 15c — embedder pamięci i router modeli na wspólnej warstwie)
- Dwie ostatnie ścieżki wychodzące przeszły na `husarz.ssrf`. Po tym etapie **wszystkie pięć**
  dróg, którymi Husarz wychodzi na sieć (`web`, wtyczki MCP, Git, embedder RAG, router modeli),
  korzysta z JEDNEJ implementacji anty-SSRF — różnią się wyłącznie dwiema flagami polityki.
- Polaryzacja `allow_loopback=True, allow_lan=True` (endpointy modeli to z założenia własna
  infrastruktura operatora), ale metadane chmury, CGNAT, zakresy zarezerwowane i tunele
  osadzające IPv4 pozostają zablokowane — to tam wylądowałby **klucz API modelu** albo
  **wektor embeddingu** (odwracalny do PII), gdyby nazwa endpointu została przejęta.
- `HttpxEmbeddingTransport` i `HttpxTransport` (router): `trust_env=False` (proxy z ENV nie
  przekieruje przypiętego połączenia), `verify=True` jawnie, `follow_redirects=False`,
  chunkowany odczyt z twardym sufitem — parytet z pozostałymi trzema transportami.
- Protokoły `EmbeddingTransport` i `Transport` (router) przyjmują `PinnedTarget` zamiast URL;
  `resolve` przewleczony przez `build_rag_backend`/`build_embedder` (router: istniejącym
  szwem `client_factory`). Testy: +4 niezmienniki (łącznie 797).

### Poprawione (Etap 15c — wyciek w komunikacie błędu routera)
- `HttpxTransport` echował w błędzie pełny URL i wnętrzności httpx
  (`f"Błąd HTTP przy {url}: {exc}"`), a komunikat trafia przez `ModelBackendError` do
  odpowiedzi API oraz audytu. Teraz generyczny — parytet z pozostałymi transportami.

### Dodane (Etap 15b — `husarz.git` na wspólnej warstwie anty-SSRF)
- Integracje Git przeszły na `husarz.ssrf` — trzecia i ostatnia ścieżka wychodząca. Stawka
  jest tu najwyższa: połączenie niesie **token PAT z prawem zapisu do repozytoriów**,
  a poprzednia walidacja (`_is_internal_host`) sprawdzała wyłącznie LITERAŁY IP i **wcale
  nie rozwiązywała nazw** — nie było więc ani ochrony przed rebindingiem, ani pinu.
- Trzecia polaryzacja polityki: nowa oś `allow_lan` (obok `allow_loopback`). Git: loopback
  ZABRONIONY (nigdy nie jest usługą lokalną Husarza), ale prywatna sieć operatora
  DOZWOLONA — samodzielnie hostowany GitLab pod RFC 1918 to podstawowy scenariusz
  suwerenności. Luz jest WĄSKI i jawny (`_LAN_NETWORKS` = RFC 1918 + ULA); świadomie NIE
  realizowany przez `ipaddress.is_private`, bo ta właściwość obejmuje też loopback,
  link-local (metadane chmury) i zakresy testowe.
- `HttpxGitTransport` dostał parytet z pozostałymi transportami: `trust_env=False`,
  `follow_redirects=False`, odczyt strumieniowy chunkami z twardym sufitem (było:
  `response.json()` wciągało całą odpowiedź dostawcy bez limitu) + deadline wall-clock,
  oraz GENERYCZNY komunikat błędu — dotąd echował `f"Błąd HTTP {method} {url}: {exc}"`,
  czyli URL i wnętrzności httpx trafiały do audytu/API.
- `GitTransport` i providerzy przyjmują `PinnedTarget` zamiast łańcucha bazy API;
  `resolve` przewleczony przez `build_git_service → GitService → build_provider`.
- Testy: +8 niezmienników ścieżki Git. Docs: ADR-0020 (oś `allow_lan`), GIT.md, BEZPIECZENSTWO.md.

### Poprawione (adwersaryjny przegląd Etapu 15b — 3 soczewki, 8 potwierdzonych findingów)
- **Fail-open kill-switch dla Gita (major)**: `POST /api/config/runtime` przebudowywał router,
  orkiestrator, `plugin_service` i pętlę narzędziową, ale NIE `git_service` — zmiana polityki
  egress (łącznie z przełączeniem na profil `airgap`) nie obowiązywała aż do restartu, mimo że
  cała warstwa anty-SSRF opiera się na tej bramce, a to JEDYNA ścieżka wychodząca niosąca token
  z prawem zapisu do repozytoriów. Dodano `git_service_factory` (jak dla wtyczek); `_require_git`
  czyta świeży serwis ze stanu. Magazyn połączeń jest PRZEKAZYWANY do nowej instancji, więc
  przebudowa nie kasuje połączeń dodanych przez API (`build_git_service(..., store=...)`).
- **3xx dostawcy Git jako sukces (major)**: przy `follow_redirects=False` (anty-SSRF)
  przekierowanie dawało puste ciało, a `_raise_for_status` nie miało gałęzi dla 3xx —
  `list_repositories` zwracało `[]` („brak repozytoriów"), a `create_pull_request` obiekt
  z pustym URL, czyli operator widział „PR utworzony", choć żaden nie powstał. Teraz `GitError`
  (parytet z konektorem MCP).
- **Niedomknięta własna bramka `api_base` (minor)**: warunek sprawdzał WARTOŚCI
  `split.query`/`split.fragment`, a dla `https://host/api/v4?` i `.../v4#` `urlsplit` zwraca
  PUSTY łańcuch (falsy) — przypadek brzegowy przechodził, a gałąź literału IP niosła `api_base`
  verbatim, więc żądanie z tokenem szło pod korzeń API. Testujemy teraz OBECNOŚĆ separatora.
- **Brak `chunk_size` w transporcie MCP (minor)**: utwardzenie anty-OOM objęło `web` i Git, ale
  ominęło wtyczki — `iter_bytes()` bez `chunk_size` oddaje cały zdekompresowany blok naraz, więc
  odpowiedź gzip mogła przekroczyć `max_bytes` ~67× przed sprawdzeniem warunku. Wyrównane.
- **Martwe pole (nit)**: `HttpxGitTransport.__init__(timeout=...)` zapisywał `self._timeout`,
  którego `__call__` nigdy nie czytał — konstruktor obiecywał nastawę, której kod nie honorował.
  Parametr usunięty (limit czasu przychodzi z każdym wywołaniem).
- **Dokumentacja (minor)**: ADR-0011 twierdził, że `build_provider` woła `check_endpoint_allowed`
  — nieprawda od Etapu 14; dopisano notę korygującą z odsyłaczem do ADR-0020.
- Testy: +11 (m.in. integracyjna regresja kill-switcha z kontrolą, że przebudowa nie gubi
  połączeń, oraz kontrola chunkowanego odczytu we WSZYSTKICH trzech transportach).

### Zmienione (Etap 15b — zaostrzenie polityki adresów dla Gita, ścieżka aktualizacji)
- Git blokuje teraz adresy, które przed Etapem 15b **przechodziły**: CGNAT `100.64.0.0/10`
  (m.in. sieci Tailscale), TEST-NET, sieci benchmarkowe, klasa E, multicast, IPv6 site-local
  i tunele osadzające IPv4 — a także **nazwy** rozwiązujące się na te zakresy (dawna walidacja
  w ogóle nie rozwiązywała nazw). Dla typowych wdrożeń to bez zmian: `api.github.com`,
  `gitlab.com` i samodzielnie hostowany GitLab pod RFC 1918/ULA działają jak wcześniej.
  **Migracja:** jeśli Twój serwer Git jest dostępny przez CGNAT (Tailscale/CGNAT ISP), zaadresuj
  go nazwą lub adresem z zakresu RFC 1918/ULA albo publicznym — inaczej połączenie da `403`.

### Dodane (higiena testów — bezpiecznik offline)
- `tests/conftest.py`: autouse fixture blokujący `socket.getaddrinfo` dla CAŁEGO zestawu.
  Po wprowadzeniu pinowania testy z hostem `api.github.com`/`gitlab.com` po cichu wychodziły
  na sieć (działały lokalnie, padłyby w CI bez WAN i w profilu airgap) — bezpiecznik zamienia
  „zapomniałem wstrzyknąć resolver" w natychmiastowy, czytelny błąd. Wykrył i domknął
  5 takich miejsc (`test_git`, `test_git_api`, `test_plugins_security`).
- Odzyskany z niezmergowanej gałęzi test integracyjny `test_config_reload_lifecycle.py`
  (cykl życia trwałego magazynu RAG przy `POST /api/config/runtime`) — uzupełnia
  jednostkowy `test_tool_close.py` o dowód na poziomie API.

### Poprawione (adwersaryjny przegląd Etapu 15 — 3 soczewki, 18 potwierdzonych findingów)
- **Bypass klasyfikacji adresów (major)**: `is_blocked_address` opierał się wyłącznie na
  właściwościach `ipaddress`, a stdlib NIE uznaje za prywatne m.in. **CGNAT 100.64.0.0/10**
  (endpoint metadanych Alibaba Cloud, typowe pule węzłów k8s/EKS) ani **IPv6 site-local
  `fec0::/10``**. Domena z allowlisty rozwiązana na taki adres przechodziła przez bramkę:
  `web` zwracał metadane modelowi, a konektor MCP wysyłał tam `Authorization: Bearer`.
  Dodano jawną listę sieci deny (CGNAT, site-local, 6to4 `2002::/16`, Teredo `2001::/32`,
  NAT64 `64:ff9b::/96` — tunele osadzające IPv4, plus benchmark/TEST-NET/klasa E, których
  `ipaddress` nie zna na Pythonie 3.11.0–3.11.8 dopuszczonym przez `requires-python`).
- **`*.localhost` jako przepustka (major)**: sufiks był uznawany za loopback po samym
  łańcuchu znaków, więc `mcp.localhost` łączył się WPROST — z pominięciem pinu, wymogu
  `https` i allowlisty egress (RFC 6761 tylko ZALECA mapowanie na loopback; glibc bez
  systemd-resolved wysyła taką nazwę do zwykłego DNS). Teraz `*.localhost` jest
  rozwiązywane, a KAŻDY adres musi być loopbackiem — inaczej odmowa.
- **`trust_env=True` w klientach httpx (major)**: `HTTP(S)_PROXY`/`ALL_PROXY` ze środowiska
  przekierowałyby PRZYPIĘTE połączenie przez cudzy serwer (a `SSLKEYLOGFILE` zrzucił klucze
  sesji) — czyli obeszłyby całą warstwę pinowania i deny-all egress. Ustawione `trust_env=False`
  w `HttpxFetcher` i `HttpxPluginTransport`.
- **Fail-open na wyjątek w resolverze (major)**: `default_resolve` łapał tylko `OSError`,
  a `getaddrinfo` koduje nazwę kodekiem `idna` i dla etykiety >63 znaków rzuca
  `UnicodeEncodeError` (podklasa `ValueError`) — wyjątek uciekał poza bramkę i wywracał
  orkiestrację zamiast dać odmowę. Łapane `(OSError, UnicodeError)`.
- **Obejście allowlisty egress przez `.local`/`.internal` (major)**: `check_endpoint_allowed`
  przepuszcza „endpointy lokalne" po samej NAZWIE (poprawne dla routera modeli — lokalny
  vLLM/Ollama), więc nazwa `cokolwiek.internal` na allowliście narzędzia `web` omijała
  politykę egress, także w profilu `airgap`. `WebTool` egzekwuje teraz allowlistę bez tego skrótu.
- **Wyciek rozpoznania do modelu (minor)**: komunikat odmowy zawierał ROZWIĄZANY adres
  wewnętrzny (`…rozwiązuje się na (10.0.0.7)`) i wracał do modelu jako wynik narzędzia —
  czyli skaner sieci wewnętrznej przez komunikaty błędów. Adres usunięty z komunikatu.
- **Limit `max_bytes` przekraczalny o rzędy wielkości (minor)**: `iter_bytes()` bez
  `chunk_size` oddaje cały zdekompresowany blok naraz (domyślne `Accept-Encoding: gzip`),
  więc sprawdzenie limitu następowało PO doklejeniu. Odczyt chunkami po 64 KiB.
- **Ciche obcięcie przy deadlinie (minor)**: przekroczenie limitu czasu urywało treść
  i raportowało `ok=True`. Teraz `FetchError` (parytet z transportem MCP).
- **Schemat URL niewalidowany (minor)**: `web` przyjmował dowolny schemat (`ftp://`,
  `ws://`) — teraz wyłącznie `http(s)://`, odrzucane przed jakąkolwiek pracą.
- **3xx od serwera MCP jako „brak narzędzi" (minor)**: przy `follow_redirects=False`
  przekierowanie dawało puste ciało i cichą degradację; teraz czytelny `PluginError`.
- **`build_plugin_service` nie przewlekał `resolve` (minor)** — resolver dało się wstrzyknąć
  tylko przez konstruktor `PluginService`, niespójnie z `build_tools`/`build_tool_loop`.
- **Audyt**: `tool.call` zapisuje `pinned_ip` (z JAKIM adresem faktycznie się połączono) —
  przy pinowaniu sama nazwa hosta jest mniej informatywna. Docstring `is_loopback_endpoint`
  wskazywał na przemianowaną funkcję `_validate_mcp_endpoint`.
- Testy: +36 niezmienników dla powyższych (m.in. parametryczne adresy, których `ipaddress`
  nie uznaje za prywatne, oraz kontrola `trust_env`/`verify`/`follow_redirects`).

### Zmienione (Etap 15 — kontrakt wewnętrzny, BREAKING dla kodu first-party)
- Protokoły `Fetcher` (narzędzie `web`) i `PluginTransport` (konektor MCP) oraz `McpClient`
  przyjmują `PinnedTarget` zamiast gołego `str`/URL. Świadome: opcjonalny pin byłby fail-open
  (implementacja mogłaby go po cichu zignorować i rozwiązać nazwę po raz drugi).
- `build_tools` / `build_tool_loop` / `BuildContext` przyjmują `resolve` (resolver DNS) —
  ten sam szew wstrzykiwania, co `executor`/`fetcher`/`rag_backend`.
- `_validate_mcp_endpoint` → `_endpoint_target` (zwraca cel połączenia, nie `None`).
- `.gitignore`: `._*` — macOS na woluminach exFAT/NTFS tworzy sidecary AppleDouble,
  które zaśmiecały repo i wywracały `ruff` („stream did not contain valid UTF-8").

### Poprawione (follow-up 14b — zwalnianie zasobów przy rekonfiguracji)
- Domknięte udokumentowane ograniczenie z Etapu 14b: `SqliteVectorStore` trzymał połączenie do
  pliku przez cykl życia stacku i przy `POST /api/config/runtime` powstawało nowe bez zamknięcia
  starego (wyciek uchwytu). Dodano `close()` do protokołów `VectorStore`/`RagBackend` (oraz
  `RagTool`/`ToolDispatcher`/`ToolLoop`); `app._build_stack` zwraca pętlę, a `config_apply`
  zamyka STARĄ pętlę po atomowej podmianie (best-effort, tłumi błędy, idempotentne; RAM = no-op).
  Testy: +5 (łańcuch close, idempotencja, tłumienie błędów, regresja config_apply). Docs: ADR-0018.

### Dodane (Etap 13b — wywołanie narzędzi wtyczki MCP / tools/call)
- Nowy `kind: plugin` (narzędzie agenta) wiążący JEDEN konektor MCP przez `config.plugin`.
  Dwie akcje: `list` (odkrywanie `tools/list`, tylko `enabled`) i `call` (wywołanie `tools/call`,
  deny-by-default). `McpClient.call_tool` + `RemoteCallResult`; `PluginService.call` z bramami.
- Deny-by-default: `PluginConfig.allow_call` (master-switch, domyślnie false) + `call_allowlist`
  (jawna enumeracja; `allow_call=true` wymaga niepustej listy — fail-closed) + `max_call_bytes`
  (cap zserializowanych params PRZED egress). Odkrywanie ≠ wywołanie.
- Bezpieczeństwo: egress/SSRF re-walidowany PER wywołanie; airgap na starcie wymaga **loopbacku**
  (spójne z runtime); wynik NIEZAUFANY (bloki binarne/`resource` pomijane — bez SSRF-by-proxy,
  ogrodzony jako dane w pętli); token tylko w nagłówku; `arguments` VERBATIM (env: NIE rozwiązywane);
  audyt loguje `{bytes, sha256}` ładunku `arguments` (eksfiltracja wykrywalna). Docs: ADR-0019.
- Utwardzenia z adwersaryjnej krytyki projektu: **M1** audyt `arguments` (nie `<dict>`), **M2**
  airgap-loopback, **S4/S5** cap bajtowy wyniku (config-driven) i cap całych `params`.
- Przewleczenie `plugin_service` do pętli (`create_app → build_tool_loop → build_tools →
  BuildContext`) — ten sam serwis co `/api/plugins`. Config: `example-mcp.yaml` (+pola call),
  `example-plugin.yaml` (NOWY, `kind: plugin`). Testy: +30 (unit/security/integracja, offline).

### Poprawione (adwersaryjny przegląd Etapu 13b)
- **Fail-open kill-switch (major)**: `plugin_service` był budowany raz na starcie i NIE przebudowywany
  przy `POST /api/config/runtime`, więc zmiana polityki konektora (`enabled`/`allow_call`/
  `call_allowlist`/egress) nie obowiązywała aż do restartu. Dodano `plugin_service_factory`
  (jak `router_factory`) — serwis jest przebudowywany z nowego configu; `/api/plugins` i pętla
  czytają świeży serwis ze stanu.
- **Wyciek audytu (minor)**: `arguments` podane jako NIE-mapa wpadały do gałęzi generycznej
  `_arg_summary` (surowe 200 znaków). Teraz `arguments` ZAWSZE logowane jako `{bytes, sha256}`.
- **Cicha odmowa (minor)**: `call_allowlist` z białymi znakami nie dopasowywała się w runtime —
  wpisy są przycinane przy walidacji (`field_validator`).
- Docstringi „6 wbudowanych rodzajów" → „7" (dispatch, test). Testy: +3.

### Dodane (portal dokumentacji + PDF)
- Portal dokumentacji **MkDocs Material** (`mkdocs.yml`, extra `husarz[docs]`) generowany z
  `docs/` — jedno źródło prawdy dla strony HTML i **interaktywnego PDF** (plugin `print-site`,
  strona „Wersja do druku / PDF"). Nowa strona startowa `docs/index.md` (przegląd, szybki start,
  mapa dokumentacji) ze **zrzutem ekranu konsoli WWW** (`docs/assets/screenshots/console.png`).
- Nawigacja: architektura/rdzeń, bezpieczeństwo, operacje, ADR 0001–0018; tryb jasny/ciemny,
  wyszukiwarka, kopiowanie kodu.

### Zmienione (dokumentacja)
- Linki w `docs/*.md` wychodzące poza `docs/` (do plików repo) przepięte na absolutne URL-e
  GitHuba — działają zarówno w portalu HTML, jak i na GitHubie (brak martwych odnośników).
- `README.md`: sekcja budowy portalu i PDF. `.gitignore`: `/site/` (wyjście builda MkDocs).
- `ollama/README.md`: sekcja „Rozwiązywanie problemów" — GPU 50xx/Blackwell (`cudaMalloc failed`
  mimo wolnego VRAM: limit pojedynczej alokacji ~4 GB; obejścia: sterownik / baza ≤3B / CPU)
  oraz pułapka `ollama create -f` (FROM mylone ze ścieżką na Windows).

## [0.14.0] - 2026-08-14

### Zmienione (standard prowadzenia projektu)
- `CLAUDE.md`: doprecyzowany standard prowadzenia projektu (wymóg użytkownika) — aktualizacja
  na bieżąco README/CHANGELOG/ROADMAP/`docs/`/wiki/PDF w tym samym kroku co kod; wiki/PDF ze
  zrzutami ekranu (preferowany interaktywny PDF), zasoby w `docs/assets/`; higiena gita
  (commit/branch/merge/push-readiness, tagi SemVer spójne z CHANGELOG); skan plików publicznych
  pod kątem kluczy/danych prywatnych przed publikacją (w tym zrzutów ekranu); obowiązkowy opis
  kodu „niebezpiecznego" (po co, ryzyko, czy usuwalny/jak zabezpieczony).

### Dodane (Etap 14b — trwałość SQLite + szyfrowanie at-rest + przewleczenie sekretów)
- `SqliteVectorStore` (stdlib `sqlite3`, jeden plik `data_dir/memory/<collection>.db`) za
  NIEZMIENIONYM `Protocol VectorStore` — realna pamięć długoterminowa (przeżywa restart).
  Izolacja `namespace` (WHERE), dedup `(namespace,id)`, ewikcja FIFO po `max_items`,
  zapis atomowy pod `threading.Lock`. Wybór magazynu: `RagBackendConfig.store ∈ {in_memory, sqlite}`.
- Szyfrowanie at-rest CAŁEGO rekordu (tekst + metadane + **wektor**) — `AesGcmCipher`
  (AES-256-GCM, lazy import `cryptography`, opcjonalny extra `husarz[memory]`). Nonce per rekord,
  **`AAD = namespace`** (anti-swap: rekordu nie da się przenieść/odszyfrować jako innej kolekcji).
  `IdentityCipher` tylko dla dev (`encrypt_at_rest=false`). DEK = SHA-256 sekretu z referencji.
- **Przewleczenie `SecretsProvider` do produkcji** (domknięcie blockera z Etapu 14):
  `cli._cmd_up → create_app(secrets) → build_tool_loop(secrets, data_dir) → build_tools →
  BuildContext → _build_rag → build_rag_backend` — `encryption_key_ref` realnie się rozwiązuje.
- Config: `RagBackendConfig` rozszerzony o `store`, `path`, `encrypt_at_rest` (None → dziedziczy
  z `security.encryption.at_rest`), `encryption_key_ref` (walidowany: musi być referencją sekretu).
- Bramki fail-closed przy budowie: sqlite+at-rest bez klucza → błąd PL (nigdy cichy plaintext);
  globalny `at_rest=true` nie może być wyłączony lokalnie dla trwałego magazynu; brak praw
  zapisu do `data_dir` → czytelny `RagBackendError`.
- Testy: +13 (unit crypto/sqlite/bramki, security „brak jawnego tekstu na dysku", integracja
  szyfrowana pamięć przez pętlę), wszystko OFFLINE. Docs: ADR-0018.

### Poprawione (adwersaryjny przegląd Etapu 14b)
- Przegląd (3 wymiary, 13 potwierdzonych z 14) i utwardzenia:
- **Odcisk treści at-rest (major)**: jawna kolumna `id` = `sha256(text)` była membership-oracle
  / brute-force do PII. Teraz autorytatywny `item_id` żyje w zaszyfrowanym blobie, a kolumna to
  `Cipher.blind_id` = `HMAC-SHA256(DEK, namespace‖id)` — nieodwracalna bez klucza, namespace'owana
  (brak korelacji między kolekcjami), zachowuje dedup. Test at-rest idzie ścieżką produkcyjną.
- **Niekontrolowany crash (minor)**: `sqlite3.Error` w `upsert/search/count` opakowany w
  `RagBackendError` → dispatch degraduje do `ToolResult(ok=False)` zamiast HTTP 500.
- **Fail-closed przy budowie (minor)**: `build_cipher` przy at-rest sprawdza dostępność
  `cryptography` (czytelny błąd PL: zainstaluj `husarz[memory]`), nie odroczony `ImportError`.
- **Walidacja krzyżowa (minor)**: pola at-rest (`path`/`encrypt_at_rest`/`encryption_key_ref`)
  wymagają `store: sqlite`, a `store: sqlite` wymaga `backend: embedding` — koniec cichego
  ignorowania intencji szyfrowania.
- **Anty-korupcja wymiaru (nit)**: niezgodny wymiar wektora w trwałym magazynie (zmiana modelu
  embeddera) → `RagBackendError`, nie cicha `0.0`.
- Docs↔kod: zaktualizowane nieaktualne wzmianki „wchodzą w 14b" (`memory/__init__.py`,
  `memory/store.py`, `docs/NARZEDZIA.md`, `config/tools/rag.yaml`). Udokumentowane ograniczenia
  (ADR-0018): `vault:`/`sops:` przyjmowane przez schemat, rozwiązywane tylko przez wspierający
  `SecretsProvider`; cykl życia połączenia sqlite przy runtime-rekonfiguracji (follow-up).
- Testy: +4 (odcisk treści na ścieżce produkcyjnej, szyfrowany dedup + zaślepiony klucz,
  fail-closed wymiaru, walidacja krzyżowa configu).

### Dodane (Etap 14 — pamięć długoterminowa / RAG)
- Pakiet `husarz.memory`: produkcyjny `EmbeddingRagBackend` (wektorowa pamięć semantyczna)
  za NIEZMIENIONYM `Protocol RagBackend` — drop-in za `InMemoryRagBackend`, `RagTool` bez zmian.
  Kompozycja wstrzykiwalnych szwów: `Embedder` (tekst→wektor) + `VectorStore` (cosine).
- Embedder suwerennie: `FakeEmbedder` (deterministyczny, TYLKO dev/test) + `OllamaEmbedder`
  (lokalny `/api/embeddings`, transport wstrzykiwalny, bramka `check_endpoint_allowed` PRZED
  każdym wywołaniem, walidacja wymiaru fail-closed, klucz jako secret-ref).
- `InMemoryVectorStore` (cosine czysty Python, izolacja namespace, cap `max_items`+FIFO,
  dedup po `sha256(text)`). Zero nowych zależności rdzenia.
- Config: `RagBackendConfig`/`EmbedderConfig` (typowane, `extra=forbid`) parsowane z
  `config/tools/rag.yaml`; domyślny backend `memory` (słowny, zero regresji), wektorowy
  `embedding` opt-in. `_build_rag` buduje backend z configu (wstrzyknięty backend ma pierwszeństwo).
- Bezpieczeństwo: izolacja cross-agent przez rozłączne kolekcje (walidacja `_cross_validate`
  odrzuca kolizję namespace); airgap odrzuca nielokalny endpoint embeddera (embeddingi ~ PII);
  wynik `search` re-injektowany zawsze jako ogrodzone DANE (pętla, ADR-0016).
- Testy: +25 (unit + security izolacja/egress + integracja przez pętlę), wszystko OFFLINE.
  Docs: ADR-0017.
- ODŁOŻONE do Etapu 14b (świadomie): trwałość (`SqliteVectorStore`) + szyfrowanie at-rest
  (`AesGcmCipher`) RAZEM z przewleczeniem `SecretsProvider` do produkcji — bez tego
  szyfrowanie byłoby teatrem (klucz nierozwiązywalny). pgvector/mem0/graphiti jako przyszłe
  adaptery za `RagBackend`.

### Poprawione (adwersaryjny przegląd Etapu 14)
- Przegląd (3 wymiary, 3 potwierdzone findingi z 7) i utwardzenia:
- **Łagodna degradacja**: `ToolDispatcher.dispatch` łapie też `MemoryError_`/`EgressError`
  (awaria embeddera RAG, egress) → `ToolResult(ok=False)` zamiast crashu całej orkiestracji.
- **Spójne domyślne**: `embedder.dim` domyślnie 768 (pasuje do `nomic-embed-text`) — koniec
  fail-closed out-of-the-box na udokumentowanej ścieżce ollama (768 ≠ 1024).
- **Docs↔kod**: docstringi `rag.py` (pgvector→EmbeddingRagBackend jako produkcyjny wektorowy).
- Testy: +2 (degradacja dispatchu, spójność domyślnego dim).

### Dodane (Etap 13 — pętla narzędziowa / function-calling)
- **Pętla ReAct** (`husarz.agents.tool_loop`): PIERWSZY egzekutor narzędzi. Model emituje
  ogrodzony blok akcji `[[HUSARZ_ACTION]]{tool,action,args}[[/HUSARZ_ACTION]]`; pętla parsuje,
  autoryzuje, dispatchuje, oddaje wynik NIEZAUFANY z powrotem — aż do odpowiedzi końcowej
  lub limitu. Prompt-based (przenośne na każdy lokalny model), ZERO zmian w routerze.
- **Dispatch** (`husarz.tools.dispatch`): jawna tabela akcji per kind (bez `getattr`),
  walidacja args (zły kształt → `ToolResult(ok=False)`, nigdy wyjątek), `manual()` dla modelu.
- **Autoryzacja per-wywołanie (deny-by-default)**: L0 `roe_required` wykluczony + opt-in
  per agent (`AgentConfig.tool_loop_enabled`, domyślnie false); L1 allowlista agenta;
  L2 walidacja dispatchu; L3 bramki w narzędziach (bez zmian). Audyt każdego wywołania
  (arg_summary sanityzowany — bez surowej treści/sekretów).
- **Limity**: `AgentConfig.max_iterations` (per krok) + `security.tool_loop`
  (`max_result_bytes`, `max_total_calls` — globalny budżet per orkiestracja, `max_plan_steps`).
- **Ogrodzenie**: `husarz.fencing` (wydzielone z załączników) — `fence_untrusted` ogradza
  wyniki narzędzi (i kontekst) jako DANE; marker z wnętrza wyniku neutralizowany (prefiks linii).
- **Wpięcie**: `Orchestrator`/`build_orchestrator`/`create_app` z opcjonalną pętlą;
  `BaseAgent.run` niezmieniony (Pocztowy, plan/synteza, `/api/chat` bez zmian). Walidacja:
  `workspace_dir` rozłączny z `data_dir`/`artifacts_dir`.
- Wspólny helper `husarz.textjson.extract_json_object` (reużyty przez plan i ReAct).
- Testy: +35 (dispatch, protokół, pętla, security offline). Docs: ADR-0016.

### Poprawione (adwersaryjny przegląd Etapu 13)
- Przegląd (3 wymiary, 0 findingów bezpieczeństwa/poprawności, 2 spójności) i utwardzenia:
- **Zero-hardcode**: cap `rag.add` przeniesiony z modułowej stałej do
  `security.tool_loop.max_rag_add_bytes` (konfigurowalny jak pozostałe limity pętli).
- **Kontrakt „nigdy nie rzuca"**: `ToolDispatcher.dispatch` łapie `AttributeError` z
  niespójnego `kind_of` (instancja innego rodzaju niż deklarowany kind) → `ToolResult(ok=False)`.
- **Docs↔kod**: `ORKIESTRATOR.md`/`ROADMAP.md` — pętla oznaczona jako zrealizowana (Etap 13),
  koniec sprzeczności z sekcją ✅. Testy: +2.

### Dodane (Etap 12b — wtyczki / konektory MCP)
- Pakiet `husarz.plugins` (lustro `husarz.git`): konektor do zewnętrznego serwera
  narzędzi **MCP** przez HTTP JSON-RPC nad WSTRZYKIWALNYM transportem. MVP:
  **odkrywanie** narzędzi (`tools/list`); wywołanie wchodzi z pętlą function-calling.
- Nowa sekcja `config/plugins/*.yaml` (`PluginConfig`): `endpoint`, `token_ref`
  (referencja do sekretu, nie wartość), `timeout_seconds`, `max_output_bytes`.
  Nowy konektor = nowy plik, bez zmian w rdzeniu.
- Bezpieczeństwo: anty-SSRF `_validate_mcp_endpoint` (loopback dozwolony; adresy
  wewnętrzne/metadanych — także IPv4-mapped IPv6 — twardo blokowane; host publiczny
  wymaga https + `security.egress.allowlist`), token rozwiązywany leniwie i nigdy
  nielogowany, wynik NIEZAUFANY z limitem `max_output_bytes` (podczas odczytu),
  błędy transportu → generyczne 502, audyt `plugin.discover` przed wyjściem.
- API: `GET /api/plugins`, `GET /api/plugins/{name}/tools` (RBAC `plugin:read`);
  deny-by-default (brak włączonych wtyczek → 404). Konsola: zakładka **Wtyczki**.
- Launcher: `_build_plugins` (HttpxPluginTransport + `_SchemeSecrets`). Przykład:
  `config/plugins/example-mcp.yaml` (`enabled: false`).
- Testy: +37 (unit + security SSRF + API). Docs: `docs/WTYCZKI.md`, ADR-0015.

### Poprawione (adwersaryjny przegląd Etapu 12b)
- Przegląd (3 wymiary, 6 potwierdzonych findingów) i utwardzenia:
- **Anty-DNS-rebinding**: `_validate_mcp_endpoint` rozwiązuje nazwę domenową i sprawdza
  KAŻDY zwrócony adres wobec bloku wewnętrznego (nazwa wskazująca metadane/adres
  wewnętrzny blokowana mimo allowlisty); nierozwiązywalna nazwa → fail-closed. Resolver
  wstrzykiwalny (testy bez DNS). Pełne pinowanie IP nadal odłożone.
- **Anty-„slow-drip" DoS**: `HttpxPluginTransport` egzekwuje bezwzględny deadline
  wall-clock na pętli odczytu (serwer sączący bajty nie blokuje już wątku puli).
- **TLS `verify=True` jawnie** w wywołaniu `httpx.stream` (spójne z docstring/ADR).
- **Walidacja `security.egress.allowlist`**: odrzuca wpisy puste/whitespace i o
  kształcie URL (koniec częściowego wildcardu `host.endswith('.')`).
- **Diagnostyka**: nierozwiązywalny `token_ref` → `PluginSecretError` → HTTP **500**
  (lokalna konfiguracja), odróżnione od zdalnej odmowy serwera (`502`).
- Usunięto martwe pole `PluginConfig.protocol_version` (zwalidowane, nieużywane).
- Testy: +5 (rebinding, fail-closed, walidacja allowlisty, 500 vs 502).

### Zmienione (Etap 12a — rejestr providerów narzędzi)
- `tools/loader.build_tools` porzuca twardy `if/elif kind` na rzecz
  `ToolProviderRegistry` (`tools/registry.py`): rodzaj narzędzia = zarejestrowany
  builder `BuildContext -> Tool`. Nowy rodzaj = builder + jedna linia `register`
  w `default_registry()`, BEZ zmian w rdzeniu dispatchu („zero hardcode").
- `build_tools(..., registry=None)` — wstrzykiwalny rejestr (seam do testów i
  przyszłego konektora MCP); nieznany `kind` daje `ToolError` z zachowanym
  komunikatem (kontrakt niezmieniony, cały pakiet testów narzędzi zielony).
- Rejestr jest WYŁĄCZNIE first-party — świadomie bez `entry_points`/`importlib`
  (obcy kod = RCE/łańcuch dostaw). Testy: +6. Docs: ADR-0014.

### Dodane (Etap 11 — zdjęcia w czacie / modele wizyjne)
- Obrazy w `POST /api/chat` (`images: [{name, data}]`, `data` = base64) dla modeli
  **wizyjnych**. Typ rozpoznawany z **magic-bytes** (png/jpeg/gif/webp) — serwer NIE ufa
  deklarowanemu MIME; obraz przekazywany jako część multimodalna OpenAI-compat
  (`image_url` z data-URI) do backendu (Ollama llava/qwen2-vl).
- Router: `ChatMessage.images: list[ImagePart]` + `_message_payload` buduje treść
  multimodalną (`[{type:text}, {type:image_url}]`) tylko gdy są obrazy (inaczej `str`).
- Konfiguracja: `ModelSpec.vision: bool` (bramka), sekcja `chat.images`
  (`enabled`, `max_images`, `max_bytes_per_image`), model `husarz-vision` w rejestrze,
  `chat.max_request_bytes` podniesione do 12 MB (base64 ~+33%).
- Bezpieczeństwo: `sanitize_images` — limit liczby/rozmiaru, dekodowanie base64 z
  walidacją, sniff magic-bytes, re-enkodowanie znormalizowanej treści; model bez
  `vision` lub dane nie-obraz → `400`. Bez egressu (data-URI, brak pobierania z URL).
- Konsola: przycisk 📎 przyjmuje też obrazy (chip 🖼), wysyłane jako base64; czyszczone
  po wysłaniu / zmianie trybu / resecie.
- Testy: +13 (`tests/unit/test_images.py` — sniff, sanityzacja, payload multimodalny,
  bramka vision w API). Docs: `docs/API.md`, ADR-0013.

### Poprawione (adwersaryjny przegląd Etapu 11)
- Przegląd (3 wymiary, 5 potwierdzonych findingów, 3 odrębne przyczyny) i utwardzenia:
- **Bramka vision na łańcuchu fallbacków**: `ModelRouter.complete` pomija kandydatów
  z `vision:false`, gdy żądanie niesie obrazy — po awarii modelu wizyjnego obraz NIE
  trafia już do modelu tekstowego przez fallback (cichy błąd/halucynacja). Niezmiennik
  z ADR-0013 egzekwowany end-to-end, nie tylko na modelu wybranym w handlerze.
- **Limit ciała odporny na `Transfer-Encoding: chunked`**: `BodySizeLimitMiddleware`
  (czyste ASGI) buforuje ciało z twardym sufitem i zwraca czyste `413` — żądanie bez
  `Content-Length` nie omija już kontroli ani nie grozi OOM (pre-auth DoS) przed walidacją.
- **Obrazy wiązane z ostatnią wiadomością `user`** (nie ślepo z `messages[-1]`) — brak
  obrazu na wiadomości `assistant`/`system`; konwersacja bez `user` + obraz → `400`.
- Testy: +10 (`tests/unit/test_etap11_fixes.py`). Docs: ADR-0013, `docs/BEZPIECZENSTWO.md`.

### Dodane (Etap 10 — pobierany launcher)
- Launcher desktopowy `husarz-app` (`husarz.launcher.app`): bez argumentów startuje
  serwer na loopbacku i **otwiera konsolę w przeglądarce**; deleguje do `husarz up
  --open` (reużywa logiki i bramek bezpieczeństwa). Frozen (PyInstaller) → domyślne
  `config`/`prompts` z `sys._MEIPASS`.
- CLI: flaga `husarz up --open` + `_open_browser_async` (wątek daemon, opener
  wstrzykiwalny, błąd otwarcia nie wywraca serwera; tylko loopback).
- Pakowanie: `packaging/husarz.spec` (PyInstaller onefile: rdzeń + konsola + domyślne
  config/prompts), `packaging/husarz_app.py`, `packaging/README.md`; extra `[package]`.
- CI: `.github/workflows/release.yml` — buduje binarki Windows/Linux/macOS i publikuje
  jako artefakty (dla tagu `v*` dołącza do GitHub Release).
- Testy: +6 (otwieranie przeglądarki, flaga --open, delegacja husarz-app, parser).
  Docs: `docs/LAUNCHER.md`, ADR-0012.

### Poprawione (adwersaryjny przegląd Etapu 10)
- CI Release: unikalne nazwy binarek per-OS (`husarz-app-{windows.exe,linux,macos}`)
  + osobny sekwencyjny job `release` (jedno `gh-release`) — koniec kolizji nazw i
  wyścigu przy dołączaniu do jednego Release.
- Odporność: niezapisywalny audyt (np. read-only CWD binarki) → czytelne **503**
  (handler `AuditError`), nie surowe 500.
- Launcher: poprawny URL dla hosta IPv6 (`[::1]`), `--open` egzekwuje „tylko loopback".
- `.dockerignore`: wykluczone `packaging/` (spójnie z tests/docs/deploy).
- Testy: +6 (non-loopback --open, tłumienie błędu openera, IPv6, delegacja
  profile/prompts + brak config, audyt→503).

### Dodane (Etap 9 — integracje Git: GitHub/GitLab + tworzenie PR)
- Pakiet `husarz.git`: klienci `GitHubProvider`/`GitLabProvider` nad WSTRZYKIWALNYM
  transportem (testy bez sieci): lista repozytoriów + utworzenie PR/MR. Magazyn
  połączeń (InMemory/File JSON, zapis atomowy); `GitService` (rozwiązuje token z
  referencji przy operacji). **Token jako referencja do sekretu**, nigdy plaintext.
- **Bramka egress (deny-all)**: host dostawcy musi być na `security.egress.allowlist`
  — inaczej 403. Ta sama warstwa co router modeli (suwerenność).
- API: `GET/POST/DELETE /api/git/connections`, `GET …/{name}/repos`,
  `POST …/{name}/pull-request`. RBAC: `git:read`/`git:write`/`git:pr` (operator/admin).
- Sekcja konfiguracji `git` (`config/git.yaml`, opcjonalna): `enabled`,
  `connections_path`. ENV: `HUSARZ_GIT__…`. Launcher buduje usługę, gdy włączona.
- Konsola: zakładka **Połączenia** — lista/dodawanie/usuwanie połączeń (token jako
  referencja), podgląd repozytoriów, formularz utworzenia PR/MR.
- Testy: +21 (klienci GitHub/GitLab na mock transport, egress, magazyn, GitService,
  API — 404/409/403/RBAC). Docs: `docs/GIT.md`, ADR-0011.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 9)
- **Blocker SSRF**: dla Git NIE stosujemy „lokalne = zawsze dozwolone" — nowa walidacja
  `api_base` twardo **blokuje hosty wewnętrzne** (loopback/link-local/metadata
  `169.254.169.254`/localhost) i **wymaga jawnej allowlisty** egress.
- **api_base https-only, bez userinfo** (token nie leci plaintextem/na obcy host) —
  walidator schematu (422) + walidacja runtime (403/502).
- **token_ref jako referencja** — walidator odrzuca surowy token (422); sekret nie
  trafia na dysk (spójne z ADR-0011).
- **repo bez wstrzyknięć** — walidator postaci `owner/name` + URL-encode ścieżki w
  kliencie GitHub (koniec `?`,`#` w URL).
- Magazyn połączeń: zapis atomowy pod zamkiem (mutacja+persist), unikatowy temp,
  odporny `_load` (uszkodzony plik → czytelny `GitConnectionError`).
- Klienci: pomijanie elementów nie-`dict` w liście repo (koniec 500). Audyt próby
  PR **przed** budową dostawcy (blok egress też audytowany).
- Testy: +13 (SSRF/https/userinfo, encode repo, non-dict, corrupt-load, 422 dla
  token/api_base/repo, 502 auth, DELETE, RBAC write/PR, GitLab MR, audyt egress).

### Dodane (Etap 8 — załączniki do czatu)
- Moduł `husarz.attachments`: pliki/foldery jako kontekst czatu. Treść NIEZAUFANA —
  twarde limity (liczba, rozmiar per plik/łączny → DoS), czyszczenie nazw (basename),
  odrzucanie danych binarnych, **ogrodzony** blok oznaczony jako dane (anty-prompt-injection,
  neutralizacja prób domknięcia ogrodzenia z wnętrza treści).
- `POST /api/chat` przyjmuje `attachments: [{name, content}]`; kontekst doklejany do
  bieżącej wiadomości; przekroczenie limitu/binaria → `400`. Zużycie tokenów obejmuje kontekst.
- Nowa sekcja konfiguracji `chat` (`config/chat.yaml`, opcjonalna): `chat.attachments`
  (`enabled`, `max_files`, `max_bytes_per_file`, `max_total_bytes`). ENV: `HUSARZ_CHAT__…`.
- Konsola: przyciski 📎 (pliki) i 📁 (folder), odczyt po stronie klienta (FileReader),
  chipy załączników z usuwaniem; foldery przez `webkitdirectory`. Bez CDN.
- Testy: sanityzacja (limity, binaria, konfinacja nazw, ogrodzenie+defang) + integracja
  `/api/chat` (kontekst doklejony, odrzucenia 400). Docs: `docs/API.md`, ADR-0010.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 8)
- **Ogrodzenie odporniejsze**: prefiksowanie KAŻDEJ linii treści niezaufanej (żadna
  linia nie udaje znacznika) zamiast podmiany literałów; nazwy pozbawiane run `=`.
- **Czyszczenie treści**: usuwanie znaków sterujących/formatujących (Cc/Cf poza `\n\t`)
  — ANSI/bidi/zero-width (anty-obfuskacja), analogicznie do czyszczenia nazw.
- **Limit rozmiaru ciała** (`chat.max_request_bytes`, middleware Content-Length → 413)
  chroni pamięć przed OOM przed ingestią; sufity schematu na `content`, liczbę
  załączników (≤1000), `messages.content`, `orchestrate.task`.
- Konsola: załączniki wyłączone/czyszczone w trybie Orkiestracja; czyszczenie chipów
  dopiero po sukcesie (zachowane do ponowienia przy błędzie).
- Docs: sprostowany ADR-0010 (limity egzekwuje serwer) i wiersz `attachments?` w API.md.
- Testy: +7 (przycinanie wielobajtowe, czyszczenie znaków sterujących, neutralizacja
  znacznika w nazwie, limit rozmiaru ciała 413, sufit liczby załączników 422).

### Dodane (Etap 7 — konta, sesje i limity tokenów)
- Pakiet `husarz.accounts`: hashowanie haseł `scrypt` (biblioteka standardowa, bez
  zależności), magazyn kont wstrzykiwalny (`InMemory`/`File` JSON), `AccountService`
  (rejestracja gated, logowanie z sesją, TTL, `logout`, limit i zużycie tokenów).
- API kont: `POST /api/auth/register`, `/login`, `/logout`, `GET /api/auth/me`
  (rola, aktywny model czatu, `tokens_used`/`token_quota`/`tokens_remaining`).
- Uwierzytelnianie Bearer rozszerzone: akceptuje token **sesji użytkownika** oraz
  statyczny token maszynowy → `Principal(role, user_id, username)`; RBAC per użytkownik.
- Limit tokenów: `POST /api/chat` i `/api/orchestrate` zwracają **HTTP 402** po
  wyczerpaniu; zużycie doliczane z pola `usage` odpowiedzi modelu (czat).
- Konfiguracja `security.auth`: `allow_registration`, `default_user_role`,
  `default_token_quota`, `session_ttl_minutes`, `accounts_path`, seed-admin (hasło z
  referencji do sekretu). Launcher aktywuje konta i traktuje je jako uwierzytelnianie
  (nasłuch nie-loopback dozwolony z kontami).
- Konsola: modal logowania/rejestracji, pasek użytkownika (nazwa, rola, **model**,
  zużyte/limit tokenów), wylogowanie; token sesji jako Bearer (localStorage).
- `models.chat` prezentowany jako aktywny model czatu w `/api/auth/me`.
- Testy: hasła (scrypt), rejestracja/logowanie/sesje/wygasanie/limity, API kont
  (sesja jako Bearer, 402, RBAC viewer), seed-admin fail-closed.
- Dokumentacja: `docs/KONTA.md`, ADR-0009.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 7)
- **Najmniejsze uprawnienia**: nowe konta dostają rolę `user` (czat/orkiestracja),
  nie `operator` (bez `tool:*`, `roe:authorize`, `audit:read`). Dodano rolę `user`.
- **Anty-brute-force**: blokada konta po `login_max_attempts` nieudanych logowaniach
  na `login_lockout_minutes` (HTTP 429); nieudane logowania/blokady audytowane.
- **Walidacja ról**: `api_role`/`default_user_role` muszą należeć do `auth.roles`;
  pola seed-admina wymagane RAZEM (walidator schematu, czytelny błąd po polsku).
- **Hasła**: scrypt `n=2**16` (bliżej OWASP; jawny `maxmem`).
- **Trwały magazyn kont**: zapis atomowy (temp + `os.replace`) pod zamkiem — koniec
  ryzyka uszkodzenia pliku poświadczeń przy współbieżności.
- **Sesje**: sprzątanie wygasłych przy logowaniu + limit sesji na użytkownika.
- **Pusty token maszynowy** normalizowany do braku (koniec dopasowania „Bearer ").
- `check_quota` pod tym samym zamkiem co `record_usage` (limit udokumentowany jako miękki).
- **`husarz useradd`** — admin tworzy konta „dla wybranych" (hasło z ENV), gdy
  rejestracja wyłączona. Wymaga trwałego magazynu (`accounts_path`).
- Testy: +14 regresji (rola user, lockout+429, walidacja ról/seed, atomowy zapis,
  pusty Bearer, sweep sesji, most config→konta, fail-closed z kontami, useradd).

### Dodane (Czat lokalny + customowy model Ollama)
- **Customowy model Ollama** `husarz` (`ollama/Husarz.Modelfile`): persona hetmana
  (PL, czat + kodowanie) zaszyta w `SYSTEM`, baza wymienna przez `FROM` (domyślnie
  `qwen2.5-coder:7b`). Instrukcja: `ollama/README.md`.
- **Tryb bezpośredniego czatu** `POST /api/chat` — rozmowa z jednym modelem (szybka,
  konwersacyjna + kodowanie), obok ciężkiej orkiestracji wieloagentowej. Model z
  `models.chat` (nowe pole configu) lub `models.default`. Błędy routera mapowane na
  429/502/503; licznik `usage.chats`.
- **Model lokalny w rejestrze**: `config/models.yaml` → `husarz-local` (backend
  `ollama`, endpoint `http://localhost:11434/v1`), ustawiony jako `models.chat`.
- **Konsola — czat jak w nowoczesnym asystencie**: dymki (użytkownik/asystent),
  własny mini-renderer Markdown (nagłówki, listy, **pogrubienia**, `inline code`,
  bloki kodu ```lang``` z przyciskiem „kopiuj"), przełącznik Czat/Orkiestracja,
  historia rozmowy, Enter=wyślij. Bez zależności z CDN (airgap-safe), motyw husarski.
- `create_app`: router jest teraz przebudowywalny (`router_factory`) i dostępny dla
  `/api/chat`; przebudowa po nadpisaniu configu w runtime obejmuje router+orkiestrator.
- Testy: `/api/chat` (odpowiedź, licznik, walidacja pustych wiadomości, brak routera).
- Poprawki z przeglądu: porażka czatu audytowana jako `chat.error` (nie
  `orchestrate.error`); spójny snapshot (config, router) pod zamkiem w `/api/chat`
  i atomowa podmiana w `/api/config/runtime` (koniec przejściowego 503 przy
  równoległym przeładowaniu); testy mapowania błędów czatu (429/503/502) + RBAC
  (viewer bez `agent:run`); uwaga o stop-tokenach/parametrach w `ollama/`.

### Dodane (Etap 6 — deploy i profile)
- Obrazy: `Dockerfile` (`husarz-api`, wieloetapowy, non-root, healthcheck) oraz
  `docker/husarz-sandbox.Dockerfile` (obraz narzędzi); `.dockerignore` bez wag/sekretów.
- Docker Compose w profilach: `docker-compose.yaml` (dev, loopback) + nakładki
  `deploy/compose/{base,prod,airgap}.yml`. Prod = proxy Caddy z TLS dla
  `${HUSARZ_PUBLIC_HOST}` (domyślnie `husarzai.pl`), usługi danych w sieci wewnętrznej;
  airgap = brak WAN, dostęp tylko przez loopback.
- Manifesty Kubernetes (`deploy/k8s/`, Kustomize): NetworkPolicy **default-deny-all**
  + wąskie reguły (ingress z nginx, DNS, egress API→dane; brak `0.0.0.0/0`),
  Deployment hardened (runAsNonRoot, readOnlyRootFilesystem, drop ALL caps, seccomp),
  Service ClusterIP, Ingress TLS (cert-manager), ConfigMap (referencje) + szablon Secret.
- Launcher: flaga `--allow-insecure` (jawny opt-out fail-closed dla kontenerów).
- CI: dodane `pip-audit` (SCA) i `hadolint` + build obrazu w GitHub Actions; nowy
  `.gitlab-ci.yml` (lustro pipeline'u dla GitLaba).
- Testy bezpieczeństwa: `tests/security/test_deploy_invariants.py` — parsowanie
  compose/k8s i egzekwowanie niezmienników (deny-all, non-root, loopback, brak WAN).
- Dokumentacja: `docs/DEPLOY.md`, ADR-0008; aktualizacja README/ROADMAP/deploy.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 6)
- **Blocker: prod/airgap nie startowały** — base compose wstrzykiwał wartość tokenu,
  ale nie referencję; dodano `HUSARZ_SECURITY__AUTH__API_TOKEN_REF=env:HUSARZ_API_TOKEN`,
  więc launcher rozwiązuje token i nie odmawia nasłuchu `0.0.0.0`.
- **Hardening kontenera Compose** (dev+prod+airgap): `read_only`, `cap_drop: [ALL]`,
  `no-new-privileges`, `user 1000:1000`, `tmpfs /tmp` — lustro securityContext z k8s.
- **Redis z hasłem** (`--requirepass`, `HUSARZ_REDIS_PASSWORD`) — spójnie z Postgres/MinIO.
- **Obrazy przypięte** (koniec `:latest`): `vault:1.18`, `minio:RELEASE.*`, `husarz-api:0.1.0`;
  `pull_policy: never` na wszystkich usługach airgap.
- **CI naprawione**: GitLab `docker-build` dostał `DOCKER_HOST`/`DOCKER_TLS_CERTDIR`
  (dind); dodano `.hadolint.yaml` (świadome ignorowanie DL3008/DL3013).
- **Vault**: sprostowany komentarz — domyślny obraz startuje w `-dev`; prod wymaga
  własnego `command: [server]` + config + unseal.
- Testy: +9 regresji (hardening compose, e2e rozwiązanie tokenu prod, hasło Redis,
  pin obrazów, PSA `restricted`, sondy `/api/health`, brak `--allow-insecure` w k8s).

### Dodane (Etap 5 — API + launcher + konsola WWW)
- Pakiet `husarz.api`: `create_app(config, ...)` (FastAPI) z endpointami health,
  config/summary, agents, models, tools, audit (+`verify`), usage, orchestrate,
  config/validate+runtime. Router modeli i audyt są wstrzykiwalne (testy bez sieci).
- Konsola WWW: jednoplikowa (`api/static/console.html`, vanilla JS, theme-aware)
  serwowana pod `/` — czat, panel konfiguracji (walidacja nadpisań), agenci, audyt, monitor.
- Launcher `husarz up --profile dev --host --port` (uvicorn; importy FastAPI/uvicorn leniwe).
- Zależności: `fastapi`, `uvicorn`.
- Testy: smoke API przez `TestClient` (bez serwera/sieci), orkiestracja, walidacja
  configu, serwowanie konsoli.
- Dokumentacja: `docs/API.md`, ADR-0007; aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 5)
- **Uwierzytelnianie API (blocker):** token Bearer + RBAC na wszystkich endpointach
  poza `/api/health`. Token pochodzi z **sekretu** (`security.auth.api_token_ref`,
  `env:`/`file:`), nigdy z configu; rola z `security.auth.api_role`. Macierz:
  `config:read` (podgląd), `audit:read` (audyt), `agent:run` (orkiestracja),
  `config:write` (nadpisania runtime — tylko admin). Wstrzykiwalne do `create_app`.
- **XSS w konsoli (blocker):** wszystkie dane z API renderowane w tabelach (agenci,
  audyt) są escapowane HTML (`esc()`); dodano pole tokenu (nagłówek Bearer).
- **Fail-closed launchera:** `husarz up` odmawia nasłuchu poza loopbackiem bez tokenu
  (kod 2); `TrustedHostMiddleware` dla loopbacku (obrona przed DNS-rebindingiem).
- **Odporność `/api/orchestrate`:** błędy routera mapowane na `429`/`502`/`503`
  (nie gołe `500`); treść błędu nie wycieka.
- **Spójność liczników:** `usage.orchestrations` liczy próby (spójnie z audytem) +
  `failures`; inkrementy i `AuditLog.record` serializowane `Lock`-iem (endpointy biegną
  w puli wątków — koniec fałszywego alarmu `verify` przy współbieżności).
- **Przebudowa orkiestratora** po `POST /api/config/runtime` (koniec działania na
  starej konfiguracji); `GET /api/audit?limit` walidowany `0..10000` (`0` → pusto).
- Testy: +20 regresji (macierz RBAC, mapowanie błędów, liczniki, przebudowa,
  limit audytu, malformed body, fail-closed launchera, atomowość łańcucha pod wątkami).

### Dodane (Etap 4 — bezpieczeństwo/ROE)
- Pakiet `husarz.security`:
  - **Audit log** niemodyfikowalny z łańcuchem skrótów (`AuditLog`, `verify`,
    zapis append-only, zegar wstrzykiwalny); `build_audit_log(security)`.
  - **ROE-gate** (`RoeGate`): twarda bramka Puszkarza — aktywność ROE, okno czasowe,
    zakres (CIDR/domeny + `out_of_scope`), techniki, tryb; **dry-run domyślnie**,
    akcja aktywna wymaga `authorized=True`. Każda decyzja audytowana.
  - **Puszkarz** (`Puszkarz`): odmowa wytwarzania narzędzi ofensywnych (z propozycją
    działania defensywnego); akcje na celach wyłącznie przez ROE-gate.
  - **RBAC** (`Rbac`): role→uprawnienia z wildcardami `*` / `obszar:*`.
- Dostawcy sekretów: `FileSecretsProvider` (konfinacja), `SopsSecretsProvider`,
  `VaultSecretsProvider` (backendy wstrzykiwalne — testowalne bez sops/Vault).
- Testy (łącznie 274): łańcuch skrótów + wykrywanie manipulacji, ROE-gate (dry-run,
  `--authorized`, blok spoza zakresu/okna, techniki), odmowa ofensywy Puszkarza,
  RBAC, dostawcy sekretów + osobne niezmienniki bezpieczeństwa.
- Dokumentacja: ADR-0006; aktualizacja ARCHITEKTURA/BEZPIECZENSTWO/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 4)
- **ROE-gate:** techniki porównywane bez rozróżniania wielkości liter/spacji (koniec
  obejścia `SQLI` vs `sqli`); `RoeScope.targets_cidr` wymaga wyrównanego CIDR (strict —
  koniec cichego poszerzania zakresu); okno ROE i `now` normalizowane do UTC (koniec
  `TypeError` naive vs aware); wstrzykiwalny `signature_verifier`; `engagement_id/owner/
  authorized_by` wymagają niepustych wartości; host celu obsługuje `scheme://`.
- **Audyt:** zapis do pliku PRZED mutacją pamięci (brak rozjazdu przy błędzie I/O);
  deep-copy `detail` (niezmienność po zahashowaniu); nieserializowalny `detail` → `AuditError`;
  opcjonalny **HMAC** (`hmac_key`) dla odporności na zmotywowanego edytora; `AuditLog.load()`
  + `verify()` z pliku; `build_audit_log` odtwarza łańcuch po restarcie (ciągłość).
- **Puszkarz:** rozszerzone markery + **kontekst defensywny** (mniej fałszywych pozytywów,
  np. reguły YARA); audyt loguje **skrót** żądania, nie surową treść (ochrona PII/sekretów).
- **Sekrety:** SOPS/Vault fail-closed przy błędzie backendu (bez propagacji wyjątku
  mogącego nieść odszyfrowaną treść).
- +20 testów regresyjnych (razem 294).

### Dodane (Etap 3 — narzędzia + sandbox)
- Pakiet `husarz.tools`: `file_edit`, `shell`, `git`, `run_tests`, `web`, `rag`.
  - Konfinacja plików do workspace + deny-globi (`resolve_within_workspace`,
    matcher `**` zgodny z Py 3.11); allowlisty komend (shell), podkomend (git,
    `push` tylko przy `allow_push`), domen (web).
  - Sandbox: `SandboxSpec` + `build_docker_argv` (twarda izolacja: `--network none`,
    limity CPU/RAM, `--cap-drop ALL`, `no-new-privileges`, montaż tylko workspace,
    `--runtime runsc` dla gVisor); `SandboxExecutor` wstrzykiwalny.
  - web: dwuwarstwowy egress (allowlista domen narzędzia + globalny `security.egress`);
    `Fetcher` wstrzykiwalny. rag: `RagBackend` wstrzykiwalny (`InMemoryRagBackend`).
  - `build_tools(config, workspace, ...)` — ładowarka z `config/tools/*.yaml`.
- `SandboxConfig` rozszerzone o `image` i `runtime_class` (bez hardcode obrazu).
- Testy (łącznie 217): konfinacja/deny-globi, argv sandboxa, shell/git/run_tests
  na mockowym executorze, web (allowlista + egress) na mockowym fetcherze, rag
  in-memory, ładowarka, osobne testy bezpieczeństwa — wszystko bez Dockera/DB/sieci.
- Dokumentacja: `docs/NARZEDZIA.md`, ADR-0005; aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 3)
- **Hardening sandboxa:** `build_docker_argv` dodaje `--user` (non-root), `--read-only`
  (rootfs) + `--tmpfs /tmp`, `--pids-limit`; opcjonalny montaż workspace `:ro`; odrzuca
  obraz zaczynający się od `-`. Executor nazywa kontener i po timeout robi `docker rm -f`
  (koniec osieroconych kontenerów). Pola `SandboxConfig.run_as_user/pids_limit/read_only_rootfs`.
- **Ochrona SSRF w web:** narzędzie web odrzuca literalne adresy wewnętrzne/zarezerwowane
  (loopback/RFC1918/link-local — metadane chmury) niezależnie od allowlisty i egress.
- `file_edit`: limit `max_bytes` egzekwowany także przy odczycie; `metadata.bytes` liczy
  bajty UTF-8. Deny-globi są teraz case-insensitive (koniec obejścia `SECRET.ENV`).
- Loader: jawny `null` w `config` narzędzia nie wywraca ładowania (fallback do domyślnych).
- Testy: +27 (razem 240, 1 skip symlink na Windows) — hardening argv, SSRF, read deny-glob/
  traversal, propagacja limitów web, okablowanie loadera, extra_args, symlink escape.

### Dodane (Etap 2 — rdzeń agentów i orkiestrator „Husarz")
- Pakiet `husarz.agents`: `BaseAgent`, `Towarzysz`, `Pocztowy`, `AgentResult`,
  protokół `SupportsComplete` oraz `build_agents(config, prompts_dir)` — ładowarka
  agentów z `config/agents/*.yaml` + prompty z `prompts/*.md` (agent wyłączony
  pomijany, brak promptu = czytelny błąd).
- Pakiet `husarz.orchestrator`: hetman `Orchestrator` z pętlą plan → deleguj →
  obserwuj → refleksja → synteza; `build_orchestrator(config, router, prompts_dir)`;
  odporne parsowanie planu/refleksji (`parse_plan`/`parse_reflection`); znaczniki
  i instrukcje faz. Nieznany agent w planie jest pomijany z adnotacją.
- Testy (łącznie 151 zielone): agent + kontekst, ładowarka (repo + brak promptu +
  wyłączony), parsowanie planu/refleksji, e2e wieloagentowe na skryptowanym
  routerze, integracja `build_orchestrator` na realnej konfiguracji i promptach.
- Dokumentacja: `docs/ORKIESTRATOR.md`, ADR-0004; aktualizacja ARCHITEKTURA/AGENCI/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 2)
- **Blocker (bezpieczeństwo):** path traversal w ładowaniu promptu — `prompt_file`
  walidowany wzorcem `^[A-Za-z0-9._-]+\.md$` w schemacie + konfinacja ścieżki w loaderze.
- **Blocker (correctness):** obserwacje trafiają teraz jako kontekst do kroków z refleksji
  (wcześniej kanał `context` był martwy — kroki działały „na ślepo").
- **Izolacja treści niezaufanej:** obserwacje agentów są ogradzane i oznaczane jako dane
  (nie instrukcje) w promptach hetmana; kontekst agenta trafia do wiadomości user, a nie
  do system promptu (koniec inwersji zaufania). Flaga `security.prompt_injection_filters`
  jest teraz realnie egzekwowana (steruje izolacją).
- **ROE-gate na poziomie orkiestracji:** agent z `roe_required` nie jest delegowany bez
  aktywnego ROE (pełny ROE-gate runtime: Etap 4).
- **Parser planu/refleksji:** odporne wyłuskiwanie JSON (`raw_decode`, obce nawiasy/wiele
  obiektów), brak wyjątku na `RecursionError`, `done` odporne na string `"false"` i na brak
  klucza (domyślne wg obecności kroków), kroki tylko z niepustych pól tekstowych.
- **Router:** pole `model` z pliku agenta działa jako fallback po `routing.agent_models`.
- +29 testów regresyjnych (łącznie 180).

### Dodane (Etap 1 — router modeli)
- Pakiet `husarz.router` — warstwa OpenAI-compat (vLLM/Ollama/SGLang):
  - `select_candidates` — wybór modelu po tagach/agencie/jawnym modelu + rozwijanie
    łańcuchów fallback (odporne na cykle, tylko modele włączone).
  - `OpenAICompatClient` + wstrzykiwalny `Transport` (`HttpxTransport` w produkcji);
    `MockClient` dla backendu `mock` — testy bez sieci.
  - `ModelRouter.complete()` — selekcja → limity → wywołanie z fallbackiem przy błędzie.
  - Kontrola kosztów: clamp `max_tokens_per_request` + `RateLimiter` (token bucket,
    wstrzykiwalny zegar) dla `max_requests_per_minute`.
  - Klucz API z `ModelSpec.api_key_ref` rozwiązywany przez dostawcę sekretów.
- Zależność `httpx` (klient HTTP warstwy OpenAI-compat).
- Testy: selekcja, klient (mock transport), rate-limit, e2e fallback, integracja
  na realnej konfiguracji repo (łącznie 103 testy zielone).
- Dokumentacja: `docs/ROUTER.md`, ADR-0003 (router modeli); aktualizacja ARCHITEKTURA/ROADMAP.

### Poprawione / Bezpieczeństwo (adwersaryjny przegląd Etapu 1)
- **Bramka egress routera** (`husarz.router.egress`): deny-all na ścieżce wywołania
  modelu — zdalny host spoza `security.egress.allowlist` jest pomijany (endpointy
  lokalne/prywatne zawsze dozwolone). Wspólny helper `husarz.config.net`.
- Klient: kanoniczne `model`/`messages` nie mogą być nadpisane przez `params`/`extra`;
  `extra` nie obchodzi już clampa `max_tokens` (kontrola kosztów); `content=null`/nie-tekst
  → jasny `ModelBackendError`; `response.json()` (ValueError) opakowany w `TransportError`;
  brakujący sekret `api_key_ref` → fail-closed; klucz API `strip()`-owany.
- Selekcja: usunięto błąd obcięcia łańcucha fallback (ochrona przed cyklami przez
  zbiór odwiedzonych); reguła z pustym `match_tags` nie jest już łapaczem wszystkiego.
- Walidacja: `CostControls.*` i `ModelSpec.request_timeout_seconds` wymuszają `>= 1`
  (koniec cichego wyłączenia limitu przez 0); walidacja ról wiadomości.
- +29 testów regresyjnych (razem 132 zielone).

### Dodane (Etap 0 — szkielet + loader konfiguracji)
- Struktura repozytorium (src-layout: `src/husarz/{config,core,router,orchestrator,agents,tools,memory,security,api,launcher}`).
- System konfiguracji „zero hardcode":
  - Schematy Pydantic v2 dla wszystkich sekcji (`platform`, `models`, `routing`,
    `security`, `agents`, `tools`, `roe`) z surową walidacją (`extra="forbid"`).
  - Loader z hierarchią nadpisań: defaults → `config/*.yaml` → ENV (`HUSARZ_*`) → runtime.
  - Walidacja krzyżowa (referencje modeli/narzędzi, reguły profilu `airgap`).
  - Czytelne błędy po polsku zamiast crasha.
  - Interfejs dostawców sekretów (`none`/`env`; Vault/SOPS — zaślepki na Etap 4).
- Przykładowa konfiguracja działająca out-of-the-box (profil `dev`):
  3 modele (GLM-5.2, Bielik v3, Hermes), 7 agentów Chorągwi, 6 narzędzi, szablon ROE.
- Prompty systemowe agentów w `prompts/*.md`.
- Launcher CLI: `husarz validate` / `husarz version` (`up` — zaślepka na Etap 5).
- Narzędzia jakości: `pyproject.toml` (ruff, black, mypy `strict`, pytest),
  `.pre-commit-config.yaml` (gitleaks, ruff, black), `.gitleaks.toml`, `.gitignore`, `.env.example`.
- Testy: jednostkowe (loader, schemat, ENV, walidacja krzyżowa, sekrety, CLI)
  oraz bezpieczeństwa (niezmienniki domyślnej konfiguracji). Wszystkie zielone.
- Dokumentacja: README, SECURITY, CONTRIBUTING, docs/{ARCHITEKTURA,AGENCI,BEZPIECZENSTWO},
  ADR-0001 (układ repo), ADR-0002 (hierarchia konfiguracji), ROADMAP, CLAUDE.md.
- CI (GitHub Actions): lint + typy + testy + gitleaks.

### Poprawione (adwersaryjny przegląd wieloagentowy Etapu 0)
- Loader: walidacja narzędzi agenta działa też przy pustym rejestrze narzędzi
  (każde odwołanie = błąd), a nie jest wtedy pomijana.
- Loader: obca zmienna `HUSARZ_*` (np. `HUSARZ_HOME`) jest ignorowana zamiast
  wywracać start (przyjmowane tylko znane sekcje).
- Loader: nadpisania ENV zachowują wielkość liter w kluczach map (id modelu,
  nazwa agenta), więc identyfikatory z wielkimi literami da się nadpisać.
- Loader: wykrywanie duplikatów kluczy odporne na klucze liczbowe (normalizacja `str`).
- Loader: wymóg rejestru modeli sprawdzany po scaleniu warstw — modele mogą
  pochodzić z ENV/runtime zgodnie z zadeklarowaną hierarchią.
- Loader: docstring coercji ENV zgodny z faktycznym (bezpiecznym) zachowaniem.
- CLI: profile podkomendy `up` pochodzą z enuma `Profile` (koniec duplikacji listy).
- Docs: `README` (pełne wyjście `validate`) i `ARCHITEKTURA` (reguły `prefer`/`auto`)
  zsynchronizowane z kodem.

### Bezpieczeństwo
- Domyślne niezmienniki: deny-all egress, sandbox bez sieci, audit log
  niemodyfikowalny, szyfrowanie at-rest, zero telemetrii — pokryte testami.
- `models/`, `.env` i sekrety w `.gitignore`; `gitleaks` skonfigurowany.
- **Hardening po przeglądzie**: bazowa linia bezpieczeństwa dla profili `prod`
  i `airgap` (sandbox włączony, audyt włączony i niemodyfikowalny, szyfrowanie
  at-rest — nie można ich cicho wyłączyć); profil `airgap` wymusza lokalne
  endpointy modeli; `ROE.is_active_at()` egzekwuje okno czasowe, a `is_active`
  wymaga niepustej referencji podpisu; `ModelSpec.api_key_ref` (klucz API jako
  referencja do sekretu, nie w `params`); allowlista `gitleaks` zawężona
  (koniec ślepej plamy na `docs/` i `prompts/`).

[Unreleased]: https://github.com/Gh0s777tt/Husarz/compare/v0.14.0...main
[0.14.0]: https://github.com/Gh0s777tt/Husarz/releases/tag/v0.14.0
