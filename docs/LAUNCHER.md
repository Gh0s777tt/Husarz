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

Diagnoza obejmuje trzy niezależne drogi, bo każda może być martwa osobno:

- **tryb czatu** (`/api/chat`) — `models.chat`,
- **orkiestracja** (`/api/orchestrate`) — `models.default`,
- **poszczególni agenci** — `routing.agent_models`.

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

!!! warning "Diagnoza NIE jest obejściem bramki egress"
    Sondowanie endpointu to połączenie wychodzące, więc przechodzi tę samą kontrolę co ruch
    routera. Endpoint spoza allowlisty **nie jest odpytywany** — kontrola kończy się stanem
    nieznanym z podaniem powodu i instrukcją dotyczącą allowlisty, a nie silnika. Bez tego
    `doctor` wystawiony w konsoli byłby skanerem portów.

!!! note "Czego `doctor` NIE robi"
    Niczego nie pobiera i nie instaluje. Podaje polecenie do świadomego uruchomienia przez
    operatora. Nie wysyła też żądania do modelu (nie ładuje wag) — sprawdza katalog silnika,
    nie jego zdolność do odpowiedzi.

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
  `0.0.0.0:8000`, gdy vLLM działa na `:8000` hosta.

## Ograniczenia

- **Podpis kodu** (Windows Authenticode / macOS notarization) — po stronie operatora
  (certyfikaty/tożsamość); bez tego systemy mogą ostrzegać przy pobraniu.
- Realne odpowiedzi AI wymagają lokalnego modelu (Ollama) — [../ollama/README.md](https://github.com/Gh0s777tt/Husarz/blob/main/ollama/README.md).
- Bogatszy desktop (Tauri, auto-update, tray, ikona) — przyszły krok.

Decyzje: [ADR-0012](adr/0012-pobierany-launcher.md).
