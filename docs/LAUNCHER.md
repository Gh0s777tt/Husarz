# Pobierany launcher (Etap 10)

Husarz jako **jeden plik do pobrania**, który startuje serwer i otwiera konsolę w
przeglądarce — UX „aplikacji jak Claude", bez instalacji Pythona u użytkownika.

## Uruchomienie z pakietu (dev)

```bash
# Launcher desktopowy: serwer + automatyczne otwarcie konsoli
husarz-app                 # http://127.0.0.1:8000/ otwiera się w przeglądarce
husarz-app --no-open       # bez otwierania (np. zdalnie)
husarz-app --port 8080 --profile dev
```

Albo przez CLI: `husarz up --open`.

## Binarka do pobrania (PyInstaller)

Budowę binarki wykonuje **operator/CI** (nie środowisko dev):

```bash
pip install -e ".[package]"
pyinstaller packaging/husarz.spec --noconfirm
# → dist/husarz-app(.exe) — dwuklik startuje serwer i otwiera konsolę
```

Binarka zawiera rdzeń, konsolę oraz **domyślne** `config/`+`prompts/` — startuje bez
żadnej konfiguracji (nadpisz `--config`/`--prompts`). BEZ sekretów i wag. Szczegóły:
[../packaging/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/packaging/README.md).

!!! warning "„Bez konfiguracji" ≠ „czat od razu odpowiada""
    Binarka nie niesie ani silnika Ollamy, ani wag modelu. Serwer i konsola wstaną po
    dwukliku, ale `POST /api/chat` bez lokalnego modelu kończy się `502 Backend modelu
    zawiódł`. Model przygotowuje się **raz**, wg [`ollama/README.md`](https://github.com/Gh0s777tt/Husarz/blob/main/ollama/README.md);
    wagi (≈1 GB dla `1.5b`, ≈4,7 GB dla `7b`) zostają w `~/.ollama` i przeżywają
    aktualizacje launchera.

## CI — pobieralny artefakt

[`.github/workflows/release.yml`](https://github.com/Gh0s777tt/Husarz/blob/main/.github/workflows/release.yml) buduje binarki dla
**Windows/Linux/macOS** i publikuje jako artefakty przebiegu (zakładka Actions);
dla tagu `v*` dołącza je do GitHub Release. Uruchom ręcznie (`workflow_dispatch`)
lub przez push tagu wersji.

## Diagnoza: `husarz doctor`

Po pobraniu binarki konsola wstaje, a czat odpowiada `502 Backend modelu zawiódł` — bo nie ma
silnika ani wag. Wcześniej nie było przy tym ŻADNEJ podpowiedzi: w logu startowym cisza,
w odpowiedzi jedno zdanie. `husarz doctor` zamienia to w listę konkretnych ustaleń:

```bash
husarz doctor --config ./config
```

```
[!!] model-czatu-u-dostawcy: Silnik odpowiada, ale NIE MA modelu 'husarz'.
     Dostępne: qwen2.5-coder:7b.
     → Przygotuj model wg ollama/README.md (`ollama create ...`) albo zmień pole
       `model` w config/models.yaml na jeden z dostępnych.

Podsumowanie — problemów blokujących: 1.
```

Kod wyjścia **1** przy problemie blokującym, więc komenda nadaje się do skryptu startowego.

Ta sama diagnoza pojawia się przy `husarz up` — pokazywane są tylko ustalenia wymagające
uwagi, żeby normalny start nie tonął w komunikatach.

### Trzy stany, nie dwa

Kontrola kończy się jako `[ok]`, `[!!]` (problem) albo **`[??]` (nieznany)**. Ostatni jest
osobny celowo: projekt ma twardą zasadę, że pomiar NIE MOŻE zaokrąglać „nie dało się
sprawdzić" do „w porządku". Diagnoza, która przy niedziałającym silniku mówi „model OK",
jest gorsza niż brak diagnozy — operator przestaje szukać.

Podsumowanie wymienia stany nieznane osobno i **nigdy** nie kończy się zdaniem „wszystkie
kontrole przeszły", jeśli którakolwiek nie została wykonana.

### Co sprawdza

| Kontrola | Wykrywa |
|---|---|
| `model-<id>-wlaczony` | model z `enabled: false` — **schemat tego NIE łapie** |
| `model-<id>-u-dostawcy` | silnik nie odpowiada, nie zna modelu, albo egress zabrania pytać |
| `katalog-*` | `data_dir`/`artifacts_dir`/`workspace_dir` niezapisywalne (audyt jest fail-closed, więc objawia się to dopiero jako 503) |
| `kolizja-portu` | endpoint modelu celuje w port, na którym nasłuchuje sam Husarz |

Nazwy modeli porównujemy z kanonizacją etykiety: `husarz` i `husarz:latest` to ten sam model,
ale **`qwen2.5-coder:7b` i `qwen2.5-coder:1.5b` to NIE** — obcinanie etykiety po obu stronach
dawałoby fałszywe „OK".

### Sprawdzany jest CAŁY łańcuch, nie tylko czat

Diagnoza obejmuje wszystkie drogi, którymi router może pójść — każda może być martwa osobno:

- **tryb czatu** (`/api/chat`) — `models.chat`,
- **orkiestracja** (`/api/orchestrate`) — `models.default`,
- **poszczególni agenci** — `routing.agent_models` (wpis `auto` znaczy „wybierz sam"
  i nie jest identyfikatorem modelu),
- **modele preferowane przez reguły** — `routing.rules[].prefer`,
- **modele ZAPASOWE** — całe łańcuchy `fallback`, rekurencyjnie.

Na dostarczonej konfiguracji to nie jest teoria. Czat działa na lokalnej Ollamie, a orkiestracja
i wszyscy agenci wskazują na serwery vLLM, których na świeżej maszynie nikt nie uruchomił:

```
[!!] model-bielik-u-dostawcy: Silnik odpowiada, ale NIE MA modelu
     'bielik-11b-v3.0-instruct' (agent bielik).
[??] model-glm-main-u-dostawcy: Silnik pod http://localhost:8000/v1 nie odpowiedział
     (agent husarz, agent kanclerz, orkiestracja).
[??] model-hermes-u-dostawcy: Silnik pod http://localhost:8001/v1 nie odpowiedział
     (agent chorazy, agent kopijnik, agent puszkarz, agent zwiadowca).
[ok] model-husarz-local-u-dostawcy: Model 'husarz' (tryb czatu) jest dostępny.
```

Pierwsza wersja diagnozy sprawdzała wyłącznie model czatu i kończyła się na tej samej
konfiguracji zdaniem „ostrzeżeń: 1" — obraz prawdziwy co do litery, a mylący co do całości.

Ustalenia są grupowane **po modelu**, nie po roli: siedmiu agentów wskazujących ten sam model
daje jeden wpis z listą ról. Silnik jest pytany **raz na endpoint**, nawet gdy korzysta z niego
kilka modeli.

### Modele zapasowe: ostrzeżenie, nie blokada

Model zapasowy przejmuje ruch, gdy główny padnie — więc diagnoza, która o nim milczy, milczy
o ratunku. Łańcuch przechodzimy tak samo jak router: rekurencyjnie (zapas zapasu też jest
osiągalny), z ochroną przed cyklem, i tylko gdy `routing.fallbacks_enabled`.

```
[!!] model-zapasowy-u-dostawcy: Silnik odpowiada, ale NIE MA modelu 'nie-ma-takiego'
     (zapasowy dla 'glowny'). Dostępne: husarz:latest, qwen2.5-coder:7b.
[??] model-ostatnia-deska-u-dostawcy: Silnik pod http://localhost:9999/v1 nie odpowiedział
     — nie wiadomo, czy model 'cokolwiek' (zapasowy dla 'zapasowy') jest dostępny.
[ok] model-glowny-u-dostawcy: Model 'husarz' (orkiestracja, tryb czatu) jest dostępny.

Podsumowanie — ostrzeżeń: 1; kontroli NIE DAŁO SIĘ wykonać: 1.
```

Model pełniący **wyłącznie** rolę zapasową dostaje **ostrzeżenie**, nie problem blokujący —
jego awaria nie przerywa pracy dzisiaj. Ma to skutek praktyczny: `husarz doctor` zwraca kod 1
tylko przy problemie blokującym, a komenda nadaje się do skryptu startowego, więc zepsuty
zapas nie zatrzyma uruchomienia działającej instalacji. Model wskazany **i** jako zapasowy,
**i** w roli głównej pozostaje blokujący.

!!! note "Czego diagnoza NIE obejmuje"
    Modeli wybieranych wyłącznie przez **dopasowanie tagów** (punkt 4 w `select_candidates`).
    Ten warunek spełnia w praktyce dowolny włączony model z pasującym tagiem, więc diagnoza
    objęłaby cały rejestr — łącznie z modelami, które operator trzyma świadomie nieużywane.

!!! warning "Diagnoza NIE jest obejściem bramki egress"
    Sondowanie endpointu to połączenie wychodzące, więc przechodzi tę samą kontrolę co ruch
    routera. Endpoint spoza allowlisty **nie jest odpytywany** — kontrola kończy się stanem
    nieznanym z podaniem powodu i instrukcją dotyczącą allowlisty, a nie silnika. Bez tego
    `doctor` wystawiony w konsoli byłby skanerem portów.

### To samo w konsoli WWW — panel **Diagnoza**

Terminal jest właściwym miejscem dla skryptu startowego, ale operator, który pobrał binarkę
i klika w przeglądarce, nie ma powodu do niego wracać. Ta sama diagnoza jest w konsoli pod
zakładką **Diagnoza** (`GET /api/doctor`):

![Zakładka Diagnoza — te same ustalenia, co `husarz doctor`](assets/screenshots/console-diagnoza.png){ .shadow loading=lazy }

Panel **wyświetla** gotowe ustalenia i liczniki policzone po stronie API — sam niczego nie
ocenia. Gdyby liczył po swojemu, konsola i `husarz doctor` mogłyby ocenić tę samą instalację
inaczej, a operator nie wiedziałby, któremu nośnikowi wierzyć. Przy błędzie czatu lub
orkiestracji komunikat niesie odnośnik prowadzący wprost tutaj — bo `502 Backend modelu
zawiódł` jest dokładnie tym zdaniem, dla którego diagnoza powstała.

!!! warning "Diagnoza wymaga uprawnienia `diagnostics:read`, nie `config:read`"
    Odpowiedź niesie adresy silników i ścieżki katalogów operatora — dane, których
    `config:read` celowo nie wystawia — a wywołanie otwiera połączenia wychodzące.
    Rola `user` (zakładana samodzielną rejestracją) MA `config:read`, więc oparcie diagnozy
    na nim ujawniłoby to publicznie. Uprawnienie mają `admin` i `operator`; `viewer` nie,
    bo podgląd nie wysyła pakietów. Szczegóły: [API.md](API.md#uwierzytelnianie-i-autoryzacja-rbac).

    Każde wywołanie zostawia wpis w audycie (akcja `doctor`) z identyfikatorem wywołującego
    i samymi liczbami w szczególe — bez endpointów i ścieżek, bo dziennik jest niemodyfikowalny.

!!! note "Czego `doctor` NIE robi"
    Niczego nie pobiera i nie instaluje. Podaje polecenie do świadomego uruchomienia przez
    operatora. **Bez flagi `--probe` nie wysyła też żądania do modelu** (nie ładuje wag) —
    sprawdza katalog silnika, nie jego zdolność do odpowiedzi. Z flagą — patrz niżej.

### `--probe`: jedyna kontrola SKUTKU

Wszystko powyżej sprawdza **deklarację**: czy silnik wymienia model w swoim katalogu.
To za mało, i nie jest to teoria. Realny przypadek, odtworzony na działającej instalacji —
endpoint bez przyrostka `/v1`:

```
[ok] model-bez-v1-u-dostawcy: Model 'husarz' (orkiestracja, tryb czatu) jest dostępny
     pod http://localhost:11434.
```

Katalog się zgadza (`/api/tags` odpowiada), a czat zwraca 502, bo `POST /chat/completions`
daje 404. Kontrola katalogu **nie może** tego zobaczyć, bo pyta o co innego.

```bash
husarz doctor --config ./config --probe
```

```
Sonda głęboka włączona — każdy model potwierdzony w katalogu dostanie realne żądanie
(limit CO NAJMNIEJ 60 s; model z wyższym `request_timeout_seconds` dostanie tyle, ile ma
w konfiguracji). Pierwsze żądanie wczytuje wagi i może potrwać.
  … pytam model 'husarz-local'
[!!] model-bez-v1-odpowiada: Silnik wymienia model 'husarz' (...), ale NIE odpowiedział
     na żądanie (nie-znaleziono).
     → Endpoint odpowiedział 404 na `/chat/completions`. Najczęściej `endpoint` wskazuje
       na bazę BEZ `/v1` albo na serwer, który wystawia sam katalog modeli.
[ok] model-husarz-local-odpowiada: Model 'husarz' (tryb czatu) ODPOWIEDZIAŁ po 8.7 s:
     „Pong! Jak mogę Ci pomóc dzisiaj?".
```

**Dlaczego opt-in.** Pierwsze żądanie wczytuje wagi. Zmierzone na modelu 7B: **18,9 s przy
zimnym starcie, 0,9 s zaraz potem** — różnica dwudziestokrotna. Dlatego domyślny limit to
60 s, a `--probe-timeout` pozwala go podnieść (wartość musi być liczbą całkowitą ≥ 1).

**Czego sonda NIE zrobi:**

| Sytuacja | Zachowanie |
|---|---|
| model nieobecny w katalogu, silnik milczy, egress zabrania | nie jest pytany (żądanie skazane na porażkę) |
| model `enabled: false` | nie jest pytany — to byłoby sprzeczne z ustaleniem obok |
| `backend: mock` | **pomijany**; `MockClient` odpowiada z pamięci, więc „OK" nic by nie znaczyło |
| endpoint nieosiągalny przez API | sonda głęboka **nie jest wystawiona** przez `GET /api/doctor` |

**Odpowiedź szybka ≠ odpowiedź na czas.** Sonda jest cierpliwsza od routera, więc porównuje
zmierzony czas z limitem, którego użyje czat — a gdy `request_timeout_seconds` nie jest
ustawione, z **domyślnymi 60 s klienta**, nie z niczym. Model odpowiadający po 90 s dostaje
problem blokujący, nie „OK": w czacie jego żądanie zostanie przerwane.

!!! warning "Sonda głęboka wysyła prawdziwe żądania"
    Przechodzi tę samą drogę, co czat: ten sam klient, ten sam pin IP (ADR-0020), to samo
    rozwiązywanie `api_key_ref`, ta sama bramka egress. To celowe — sonda ma sprawdzać drogę,
    która realnie zawodzi, a nie „prostszą", której nikt nie używa. Kill-switch
    `security.secret_store.enabled` obowiązuje ją tak samo jak resztę systemu.

    Świadomie **nie** używa `ModelRouter`: router ma fallbacki, więc przy modelu, który nie
    odpowiada, dostalibyśmy odpowiedź z INNEGO modelu i uznali ją za dowód sprawności tego.

    Decyzje i uzasadnienia: [ADR-0024](adr/0024-sonda-gleboka-diagnozy.md).

## Pobranie brakujących wag: `husarz bootstrap`

Diagnoza mówi, czego brakuje. Ta komenda proponuje to pobrać — i domyka pętlę, która dotąd
kończyła się na „napraw to sam".

```bash
husarz bootstrap --config ./config
```

```
Brakuje 2 model(i). Sprawdzam rozmiary w rejestrze…
[  ] qwen2.5-coder:1.5b (orkiestracja, tryb czatu) — 0.99 GB → http://localhost:11434/v1
[--] husarz-nieistniejacy (reguła routingu [code]) — NIE DO POBRANIA
     powód: rejestr nie zna modelu 'husarz-nieistniejacy'. Jeśli powstaje on lokalnie
     z Modelfile (jak `husarz`), użyj `ollama create` — patrz ollama/README.md

RAZEM: 1 model(e) do pobrania, 0.99 GB. Pobiera SILNIK pod wskazanym adresem, nie Husarz.

Pobrać? [t/N]:
```

**Domyślnie wyłączone.** `bootstrap.enabled: false` w dostarczonej konfiguracji — Husarz nie
sięga do sieci, dopóki operator tego nie włączy. Bez włączenia komenda odmawia i mówi dlaczego.

**Rozmiar pochodzi z manifestu, nie ze strumienia.** Manifest to metadane: zmierzone
**857 bajtów** dla `qwen2.5-coder:1.5b`, z których wynika dokładny rozmiar 0,99 GB. Dopiero po
zgodzie prosimy silnik o wagi. Model, którego rozmiaru nie da się ustalić, jest **pokazany
z powodem, ale nie pobierany** — zgoda bez znajomości rozmiaru nie byłaby zgodą.

**Domyślna odpowiedź to odmowa.** Enter naciśnięty odruchowo nie uruchamia transferu
gigabajtów. Brak terminala (potok, usługa) też znaczy „nie". Do skryptów jest `--yes` i jej
użycie **jest** zgodą — rozmiar i tak zostaje wypisany.

!!! warning "Dwie allowlisty, nie jedna"
    Zapytanie o manifest przechodzi przez `bootstrap.sources`, a **nie** przez
    `security.egress.allowlist`. Gdyby wystarczała ta druga, każda domena otwarta dla
    narzędzia `web` stawałaby się źródłem, z którego Husarz gotów jest pobierać wagi.
    Zależność nie działa też w drugą stronę — wpis w `bootstrap.sources` nie otwiera domeny
    routerowi, wtyczkom ani agentom.

    Zapytanie o manifest przechodzi dodatkowo pin IP (ADR-0020) z zakazem loopbacku i LAN-u:
    rejestr modeli jest w WAN, więc jego nazwa nie ma prawa rozwiązać się na adres wewnętrzny.

!!! note "Czego `bootstrap` NIE robi"
    **Nie pobiera ani nie instaluje SILNIKA.** Wagi ściąga silnik operatora; Husarz o to
    prosi. Dzięki temu nigdy nie dotykamy cudzego kodu wykonywalnego ani ścieżek
    instalacyjnych per system — instalacja silnika należy do menedżera pakietów.
    W profilu `airgap` komenda odmawia twardo i podaje drogę ręczną; włączenie
    `bootstrap.enabled` tego **nie** zmienia.

    Decyzje i uzasadnienia: [ADR-0025](adr/0025-pobieranie-wag-za-zgoda.md).

## Zachowanie

- Domyślny nasłuch **loopback** (`127.0.0.1`) — bezpieczny, lokalny.
- Przeglądarka otwierana tylko dla loopbacku, po krótkiej zwłoce (serwer zdąży wstać);
  błąd otwarcia (headless/brak DISPLAY) nie wywraca serwera.
- Uwierzytelnianie/konta/Git działają jak w `husarz up` (ta sama logika i bramki).
- **Kontrola kolizji portu przy starcie.** Launcher i vLLM z dostarczonego
  `config/models.yaml` mają ten sam port domyślny **8000**. Gdy port nasłuchu pokrywa się
  z loopbackowym endpointem włączonego modelu, launcher wypisuje ostrzeżenie z nazwą modelu
  i podpowiedzią naprawy — żądania do takiego modelu wracałyby do własnego API Husarza.
  Kontrola **ostrzega, nie blokuje**: Husarz w kontenerze legalnie nasłuchuje na
  `0.0.0.0:8000`, gdy vLLM działa na `:8000` hosta. Ta sama kontrola działa w zakładce
  **Diagnoza** — launcher przekazuje do API REALNY adres nasłuchu (`--host`/`--port`),
  a nie nagłówek `Host` z żądania, który pochodzi od pytającego.

## Ograniczenia

- **Podpis kodu** (Windows Authenticode / macOS notarization) — po stronie operatora
  (certyfikaty/tożsamość); bez tego systemy mogą ostrzegać przy pobraniu.
- Realne odpowiedzi AI wymagają lokalnego modelu (Ollama) — [../ollama/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/ollama/README.md).
- Bogatszy desktop (Tauri, auto-update, tray, ikona) — przyszły krok.

Decyzje: [ADR-0012](adr/0012-pobierany-launcher.md).
