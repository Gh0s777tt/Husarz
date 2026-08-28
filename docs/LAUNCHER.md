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

### Trzy stany, nie dwa — i dwa stopnie problemu

| Znacznik | Znaczenie |
|---|---|
| `[ok]` | w porządku |
| `[!!]` | problem **blokujący** — bez naprawy funkcja nie zadziała, kod wyjścia 1 |
| `[! ]` | ostrzeżenie — działa, ale nie tak, jak operator zapewne oczekuje |
| `[??]` | **nie dało się sprawdzić** — to nie to samo co „OK" |

Rozróżnienie `[!!]` od `[! ]` nie jest kosmetyką. Wcześniej oba dostawały ten sam znacznik,
a od czasu diagnozowania modeli zapasowych (niedziałający zapas to ostrzeżenie, nie blokada)
mieszana lista zdarza się regularnie. Gdy wszystko krzyczy tak samo głośno, nic nie jest pilne.
Legenda pojawia się w wyjściu tylko wtedy, gdy na liście są OBA rodzaje — inaczej byłaby szumem.

Kontrola kończy się jako `[ok]`, `[!!]`/`[! ]` (problem) albo **`[??]` (nieznany)**. Ostatni jest
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
[! ] model-zapasowy-u-dostawcy: Silnik odpowiada, ale NIE MA modelu 'nie-ma-takiego'
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
    ale nie dlatego, że „podgląd nie wysyła pakietów" — ten argument przestał obowiązywać
    wraz z limitem tempa. Granica stoi na UJAWNIENIU topologii.
    Szczegóły: [API.md](API.md#uwierzytelnianie-i-autoryzacja-rbac).

    Każde wywołanie zostawia wpis w audycie (akcja `doctor`) z identyfikatorem wywołującego
    i samymi liczbami w szczególe — bez endpointów i ścieżek, bo dziennik jest niemodyfikowalny.

    **Tempo jest ograniczone** (`security.diagnostics.max_requests_per_minute`, domyślnie
    6/min — jedno wywołanie na dziesięć sekund, swobodnie wystarcza człowiekowi klikającemu
    „Sprawdź ponownie"). Każde wywołanie otwiera połączenia wychodzące, więc bez limitu
    uprawnienie byłoby dźwignią: żądanie tanie dla wywołującego, kosztowne dla instalacji.
    Limit sprawdzamy **przed** sondowaniem — chodzi o to, żeby nadmiarowe żądanie nie
    wygenerowało ruchu, a nie żeby dostało 429 po fakcie. Konsola pokazuje wtedy ostrzeżenie
    i **nie kasuje** poprzedniego wyniku, bo ten jest nadal prawdziwy.

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

## Integralność dziennika: `husarz audit verify`

Odpowiada na pytanie, które przy awarii jest jedynym istotnym: nie „czy coś jest nie tak",
lecz **gdzie**.

```bash
python -m husarz.launcher.cli audit verify --config ./config
```

Kody wyjścia nadają się do crona: **0** — łańcuch spójny, **1** — wykryta niezgodność,
**2** — błąd konfiguracji albo klucza.

Raport na dzienniku zdrowym:

```
Dziennik:  audit/audit.log (376 wpis(ów))
Kotwica:   ok
Klucz HMAC: pokolenie '2026-08', pokoleń łącznie: 2
Wynik:     ŁAŃCUCH SPÓJNY
```

I na tym, od którego zaczął się Etap 18c — prawdziwym dzienniku tego projektu:

```
Dziennik:  audit/archiwum/audit.log.rozgalezony-2026-08-28 (376 wpis(ów))
Kotwica:   brak
Klucz HMAC: brak (goły SHA-256 — każdy z prawem zapisu może przeliczyć łańcuch)
Wynik:     NIEZGODNOŚĆ (ogniwo)
           wpis nr 261 — orchestrate (wykonał: api, 2026-08-23T11:28:21.270160+00:00)
           Wpis wskazuje na inny skrót niż faktyczny skrót wpisu poprzedniego — łańcuch
           jest w tym miejscu ROZGAŁĘZIONY. Najczęstsza przyczyna to dwa procesy piszące
           do jednego pliku, a nie manipulacja.
```

Ostatnie zdanie jest tam celowo. Dziennik audytu, który melduje „manipulację" przy zwykłej
pomyłce konfiguracyjnej, uczy operatora ignorować alarmy — a to kosztuje więcej niż sam
błąd.

### Rodzaje niezgodności

| Rodzaj | Co znaczy |
|---|---|
| `plik` | plik skurczył się, zniknął albo przestał być czytelny |
| `kotwica` | wpisy zniknęły albo historia została przepisana (kontrola KOMPLETNOŚCI) |
| `ogniwo` | łańcuch rozgałęziony — zwykle dwa procesy na jednym pliku |
| `skrot` | wpis zmieniono po zapisaniu (albo policzono innym kluczem) |
| `pokolenie` | wpis starszego pokolenia po nowszym — patrz [ADR-0026](adr/0026-rotacja-klucza-hmac-audytu.md) |
| `nieznany_klucz` | etykieta pokolenia bez klucza w konfiguracji — nie ma czym sprawdzić |

### Dlaczego to osobna ścieżka, a nie `husarz up`

`husarz up` **odmawia startu** na dzienniku, który się nie weryfikuje — i słusznie, bo
buduje dziennik do PISANIA, a dopisywanie do zepsutego łańcucha pogłębia szkodę. Narzędzie
diagnostyczne potrzebuje czegoś dokładnie odwrotnego. Dziennik, którego nie da się obejrzeć
w jedynym momencie, który się liczy, byłby bezużyteczny.

`audit verify` otwiera dziennik **wyłącznie do odczytu** (bez ścieżki zapisu, więc nie da
się nim przypadkiem dopisać) i nie zawiera pola `detail` wpisów — może ono nieść ścieżki
i referencje kont, a raport bywa wklejany do zgłoszeń.

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

## Skąd ta wartość: `husarz config explain`

Hierarchia nadpisań to `defaults (kod) → config/*.yaml → ENV (HUSARZ_*) → sekrety →
runtime (panel)`. Na stanowisku deweloperskim odpowiedź „dlaczego ta wartość jest taka" jest
zwykle oczywista, bo warstwa jest jedna. We wdrożeniu kontenerowym już nie:
`deploy/k8s/configmap.yaml` nadpisuje konfigurację zmiennymi środowiskowymi, więc plik
w repozytorium mówi jedno, a działająca instancja robi drugie — a operator patrzy na plik.

```bash
python -m husarz.launcher.cli config explain security.audit.integrity --config ./config
```

```
Ścieżka:  security.audit.integrity
  defaults (kod)     (nie ustawia)                 [schemat Pydantic]
  config/*.yaml      'blocking'                    [security.yaml]
  ENV (HUSARZ_*)     'warn'                      <-- obowiązuje  [HUSARZ_SECURITY__AUDIT__INTEGRITY]
  runtime (panel)    (nie ustawia)
```

Wypisujemy **wszystkie** warstwy, także te, które nic nie wnoszą — z dwóch powodów. Po
pierwsze, sedno pytania leży w RÓŻNICY: gdyby raport pokazywał samą wartość obowiązującą,
nie rozwiązywałby problemu, dla którego polecenie powstało. Po drugie, warstwa pominięta
w wydruku jest dla operatora warstwą nieistniejącą, a to właśnie ona bywa miejscem, w którym
trzeba coś ustawić — stąd podpowiedź z nazwą zmiennej środowiskowej nawet wtedy, gdy ENV
dziś milczy.

### Trzy rozróżnienia, które łatwo zgubić

- **„Nikt tego nie ustawia" to nie „tego nie ma".** Gdy żadna warstwa się nie wypowiada,
  obowiązuje wartość domyślna ze schematu i raport mówi to wprost.
- **`null` to nie brak wpisu.** W YAML-u `null` bywa wartością znaczącą (np. wyłączeniem
  limitu tempa), więc jest odróżniane od nieustawienia.
- **Referencja do sekretu NIE jest rozwijana** — i to jest warunek, nie ograniczenie.
  Konfiguracja przechowuje wyłącznie referencje (`env:`/`file:`/`vault:`/`sops:`/`husarz:`),
  a narzędzie diagnostyczne, które by je rozwinęło, byłoby wygodnym sposobem odczytania
  sekretu przez kogoś z dostępem do powłoki, ale nie do magazynu. Raport pokazuje referencję
  dokładnie taką, jaka stoi w konfiguracji, i dopisuje ostrzeżenie.

Kody wyjścia: **0** — wypisano wyjaśnienie, **2** — błąd konfiguracji albo ścieżki.

## Aktualizacje: `husarz update check`

**Domyślnie wyłączone — i nie jest to ostrożność na wyrost.** Samo zapytanie o wersję jest
połączeniem **wychodzącym**: ujawnia serwerowi wydań, że ta instalacja istnieje, ma dany
adres IP i konkretną wersję. Husarz deklaruje zero telemetrii, więc mechanizm o takim skutku
nie może włączyć się sam.

```bash
python -m husarz.launcher.cli update check --config ./config
```

Włączenie: `config/update.yaml` → `enabled: true`. Wymagane są wtedy **oba** pozostałe pola
(repozytorium i allowlista hostów) — włączony mechanizm bez nich byłby atrapą wyglądającą na
działającą, więc start kończy się czytelnym błędem.

### Trzy stany, nie dwa

Ta sama zasada, co w diagnozie: **„nie udało się sprawdzić" NIGDY nie zaokrągla się do
„masz aktualną wersję"**. Instalacja, która przez tydzień nie dobiła do serwera wydań, ma
o tym powiedzieć — inaczej cisza znaczyłaby dwie zupełnie różne rzeczy naraz.

| Kod wyjścia | Znaczenie |
|---|---|
| 0 | masz najnowszą wersję **albo** mechanizm jest wyłączony |
| 1 | dostępna nowsza wersja |
| 2 | błąd konfiguracji |
| 3 | **nie dało się sprawdzić** — osobny kod, bo skrypt pilnujący wersji musi odróżnić to od „aktualna" |

Powiadomienie pojawia się także przy starcie `husarz up`, **po** diagnozie: diagnoza mówi
o tym, co nie działa teraz, a aktualizacja o tym, co można poprawić. Nieudane sprawdzenie
nie zatrzymuje startu — platforma ma wstać także wtedy, gdy serwer wydań milczy.

### Dwie allowlisty, nie jedna

Zapytanie przechodzi przez `update.sources`, a **nie** przez `security.egress.allowlist`.
Zgoda na pytanie o wersję nie może po cichu otwierać tej domeny narzędziu `web`, wtyczkom
MCP ani agentom. Droga jest przy tym objęta pinowaniem IP (ADR-0020) tak samo jak każda inna
wychodząca.

W profilu **airgap** mechanizm jest odrzucany przy starcie: instalacja odcięta od sieci nie
ma jak sprawdzić wersji, a pole, które „istnieje, ale nie działa", jest gorsze niż jego brak.

### Czego jeszcze NIE ma

Na tym etapie Husarz **wyłącznie powiadamia**. Pobierania nowej wersji, weryfikacji podpisu
i podmiany przy restarcie jeszcze nie ma — to osobny krok, opisany w ROADMAP. Powód
kolejności jest zasadniczy: aktualizator, który pobiera i **wykonuje kod**, jest powierzchnią
ataku na łańcuch dostaw, więc nie powstanie przed weryfikacją podpisu ed25519.
