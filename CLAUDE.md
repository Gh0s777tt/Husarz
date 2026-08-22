# CLAUDE.md — przewodnik pracy nad Husarzem

Ten plik jest wczytywany na starcie sesji Claude Code. Definiuje twarde zasady
pracy nad projektem **Husarz**. Trzymaj się ich rygorystycznie.

## Czym jest Husarz

Suwerenna, samodzielnie hostowana, wieloagentowa platforma AI (Chorągiew).
Zasada nadrzędna: **suwerenność danych** — modele i dane NIE opuszczają
infrastruktury użytkownika bez wyraźnej zgody. Domyślnie **deny-all egress**.

## Standard prowadzenia projektu (nadrzędny wymóg użytkownika)

Projekt prowadzimy **maksymalnie profesjonalnie**. Nadrzędna zasada operacyjna:
**żadna zmiana nie może pozostać nieudokumentowana ani niezsynchronizowana.** Każdy krok
zostawia repozytorium i dokumentację w stanie spójnym, kompletnym i gotowym do publikacji.

- **Na bieżąco, nie na koniec**: dokumentację, `CHANGELOG`, `ROADMAP`, wiki, PDF, tagi wersji
  i stan gita aktualizujemy W TYM SAMYM KROKU co zmianę kodu — nigdy „później".
- **Kompletność dla osoby z zewnątrz**: dokumentacja ma wystarczyć komuś spoza projektu, by
  zrozumieć CO i DLACZEGO robi każdy moduł/narzędzie/funkcja — bez czytania źródeł.
- **Porządek**: pliki, foldery i dokumentacja w czystej, logicznej strukturze; żadnych
  osieroconych ani nieaktualnych artefaktów.
- **Ślad audytowy**: po każdym istotnym kroku raportuj stan (co zmienione, testy, docs,
  gotowość do push), żeby żadna aktualizacja nie „zginęła".

## Zasada „zero hardcode" (bezwzględna)

- Żadnych kluczy, adresów, nazw modeli, ścieżek ani polityk w kodzie.
- Hierarchia: `defaults (kod) -> config/*.yaml -> ENV (HUSARZ_*) -> sekrety -> runtime (panel)`.
- Każdy config walidowany schematem Pydantic przy starcie. Błąd = czytelny
  komunikat po polsku, nigdy niekontrolowany crash.
- Nowy agent = nowy plik `config/agents/<nazwa>.yaml`, BEZ zmian w rdzeniu.

## Styl kodu i języka

- **Komentarze i dokumentacja: po polsku. Identyfikatory w kodzie: po angielsku.**
- Python 3.11+, typowanie obowiązkowe (mypy `strict`). Preferuj kompozycję i
  konfigurację nad dziedziczeniem i wartościami na sztywno.
- Pracuj małymi, weryfikowalnymi krokami. Po każdym kroku: uruchom testy.

## Bramki jakości (muszą być zielone przed commitem)

Uruchamiaj z aktywnego venv. Ścieżka zależy od systemu — `.venv/Scripts/python.exe` na
Windows, `.venv/bin/python` na Linuksie i macOS. **Zanim uruchomisz bramki, upewnij się, że
venv pasuje do TEGO systemu** — venv zbudowany gdzie indziej nie zadziała, a objawi się to
mylącymi błędami importu, nie czytelnym komunikatem.

Sprawdzenie zajmuje sekundę i oszczędza kwadrans zgadywania:

```bash
ls .venv                 # Windows: Scripts/ Lib/ ;  Linux/macOS: bin/ lib/
cat .venv/pyvenv.cfg     # pole `home` pokazuje, na jakim systemie powstał
```

**Repozytorium bywa współdzielone między systemami** (u operatora: dysk przenoszony między
Windowsem a macOS), więc `.venv` w repo może pochodzić z tego drugiego. Wtedy NIE nadpisuj go
w miejscu — zbuduj osobny venv POZA repo i używaj go jawnie:

```bash
python3 -m venv ~/.husarz-venv
~/.husarz-venv/bin/python -m pip install -e ".[dev]"
~/.husarz-venv/bin/python -m pytest        # i tak samo pozostałe bramki
```

Powód, dla którego venv stoi poza repo: nadpisanie `.venv` zepsułoby środowisko na drugim
systemie, a katalog i tak jest ignorowany przez gita, więc nic się nie „gubi".

```bash
python -m ruff check .
python -m black --check src tests scripts
python -m mypy
python -m pytest
python -m husarz.launcher.cli eval --config ./config --prompts ./prompts
python -m mkdocs build --strict
```

Dwie ostatnie pozycje są równoprawnymi bramkami, nie dodatkiem: `husarz eval` sprawdza
niezmienniki routingu i bramki narzędziowej bez modelu i sieci, a `mkdocs --strict`
wychwytuje martwe odnośniki, czyli rozjazd dokumentacji.

Osobno, przed commitem: `gitleaks protect --staged` musi być czysty.

Definicja „ukończone": kod otypowany, testy (unit + integration + security) zielone, bramka
ewaluacyjna zielona, dokumentacja buduje się w trybie `--strict`, brak sekretów, **cała
dokumentacja zaktualizowana** (README/CHANGELOG/ROADMAP/`docs/`/wiki/PDF), repozytorium
spójne, zmiany zacommitowane i **wypchnięte na oba zdalne**, stan zaraportowany operatorowi.

Zielone bramki to warunek KONIECZNY, nie wystarczający. Do „ukończone" należy jeszcze:
**nowy test na zmienione zachowanie**, **sprawdzona nośność tego testu** oraz **audyt
zmiany** — szczegóły w sekcji „Testowanie, audyt i dokumentacja KAŻDEJ zmiany". Zmiana
z zielonymi bramkami, ale bez własnego testu, jest NIEUKOŃCZONA — choćby cały zestaw
świecił się na zielono. Zielony zestaw mówi tylko, że nikt nie napisał asercji, która by
ją złapała.

## Testowanie, audyt i dokumentacja KAŻDEJ zmiany (wymóg użytkownika)

Nie „każdej istotnej" — **każdej**. Trzy rzeczy dzieją się w TYM SAMYM kroku co zmiana kodu,
a krok bez nich nie jest ukończony:

| Co | Znaczy konkretnie |
|---|---|
| **Test** | Nowa albo zmieniona zdolność ma test SKUTKU, o sprawdzonej nośności |
| **Audyt** | Zmiana zostaje przejrzana adwersaryjnie, a nie tylko „przeczytana jeszcze raz". Głębokość stopniowana — patrz tabela niżej |
| **Dokumentacja** | `docs/` + CHANGELOG + ROADMAP + docstringi — szczegóły w sekcjach niżej |

### Test — co to znaczy w tym projekcie

1. **Każda zmiana zachowania ma test.** Poprawka bez testu to poprawka, którą ktoś cofnie za
   trzy tygodnie, nie wiedząc, że coś psuje. Poprawka bezpieczeństwa bez testu jest gorsza:
   wygląda na domkniętą.
2. **Test sprawdza SKUTEK, nie deklarację** — patrz osobna sekcja niżej. To najdroższa lekcja
   tego projektu i nie ma od niej wyjątków dla warstwy bezpieczeństwa.
3. **Nośność sprawdzona zawsze** — patrz „Nośność testów". Test, którego nie próbowano złamać,
   jest hipotezą, nie zabezpieczeniem.
4. **Gdy testu SKUTKU napisać się nie da — powiedz to wprost.** Bywają własności (wzajemne
   wykluczanie w oknie dwóch instrukcji, zachowanie przy awarii zasilania), których nie da się
   odtworzyć bez wstrzyknięcia pauzy w kod produkcyjny, czyli bez testu zmieniającego to, co
   bada. Wtedy: zostaw kontrolę słabszą (np. strukturalną), **opisz w docstringu, że jest
   słabsza i czego NIE dowodzi**, i zapisz lukę w notatce weryfikacyjnej. Zapisana luka jest
   uczciwa; pozorne pokrycie jest szkodliwe.

### Audyt — przegląd adwersaryjny, nie ponowne przeczytanie

Audytowi podlega **każda** zmiana, ale jego GŁĘBOKOŚĆ jest stopniowana. Stopniowanie jest
jawne po to, żeby nie stało się furtką („to była drobna zmiana, więc pominąłem"):

| Rodzaj zmiany | Minimalny audyt |
|---|---|
| Refaktor bez zmiany zachowania, poprawka literówki, docstring | **Samokontrola z listy niżej** |
| Zmiana zachowania, nowy endpoint, nowa opcja konfiguracji | Samokontrola + **próba obalenia własnego testu** (mutacja) |
| Warstwa bezpieczeństwa, sekrety, egress, sandbox, ROE, audyt | **Pełny przegląd adwersaryjny**: kilka niezależnych perspektyw + próba OBALENIA każdego zgłoszenia przez uruchomienie kodu |

Uzasadnienie jest empiryczne, nie teoretyczne. W Etapie 17 trzy takie przeglądy dały bilans:
**z 19 zgłoszeń sprawdzonych osobno 18 okazało się realnych** — w tym trzy wady w kodzie, który
sam przed chwilą napisałem i uznałem za sprawdzony, oraz dwa moje twierdzenia w dokumentacji,
które były po prostu nieprawdziwe. Zielony zestaw testów tego nie wychwycił, bo testy sprawdzały
to, co autor pomyślał, że sprawdza.

Zasady, które z tego wynikają:

- **Zgłoszenie odcięte przez limit weryfikacji jest PRAWDOPODOBNE, nie hipotetyczne.** Przy
  bilansie 18/19 traktowanie reszty listy jak szumu byłoby zwykłym samooszukiwaniem. Zapisz je
  w ROADMAP i wracaj do nich.
- **Ale nie każde zgłoszenie jest trafne.** Jedno z dziewiętnastu się nie potwierdziło. Sprawdź
  je sam, uruchamiając kod — nie przyjmuj na wiarę ANI zgłoszenia, ANI jego obalenia.
- **Sceptyk bywa cenniejszy niż zgłaszający.** W tym projekcie agent mający obalić zgłoszenie
  poszerzył jego zakres, wykazując, że wada nie dotyczy jednego modułu, lecz całego endpointu.
  Czytaj uzasadnienia obu stron, nie tylko werdykt.
- **Wynik audytu zapisz** — także zgłoszenia obalone, żeby nie wracały.

**Samokontrola — pięć pytań, na które trzeba odpowiedzieć sobie wprost:**

1. Co ta zmiana psuje, jeśli jestem w błędzie? Kto jeszcze używa tego, co ruszyłem?
2. Czy mój test sprawdza SKUTEK, czy tylko to, że kod został wywołany?
3. Czy istnieje wejście, którego nie przewidziałem — puste, za długie, współbieżne, w innym
   porządku, po awarii w połowie?
4. Czy komunikat błędu nie odsyła tego, co dostałem na wejściu?
5. Czy dokumentacja, którą właśnie napisałem, jest PRAWDZIWA — czy tylko brzmi dobrze?

Pytanie piąte nie jest ozdobnikiem: dwa razy w tym projekcie zapisałem w dokumentacji
twierdzenie o bezpieczeństwie, które było nieprawdziwe, i wykrył je dopiero przegląd.

### Dwa wzorce, które w tym projekcie kosztowały najwięcej

**Poprawka wzorca wymaga przeszukania repozytorium.** Gdy wada nie jest pomyłką w jednym
miejscu, lecz WZORCEM (kolejność „zmutuj pamięć, potem zapisz plik"; brak `fsync`; echo wejścia
w komunikacie błędu) — po naprawie przeszukaj repozytorium pod kątem tego wzorca. U nas magazyn
połączeń miał dokładnie tę samą wadę co magazyn sekretów i przetrwał DWA przeglądy, bo uwaga
była skupiona na module, w którym wadę zgłoszono.

**Rozszerzenie warunku usuwania danych wymaga wypisania wszystkich użytkowników tych danych.**
Warunek zawężony jest bezpieczny; rozszerzając go, łatwo sprawdzić, że działa dla nowego
przypadku, i nie sprawdzić, komu jeszcze może zaszkodzić. U nas dodanie „połączenia nie ma, więc
sekret jest sierotą" zaczęło kasować token, którego używało INNE połączenie — i raportowało
sukces.

## Dokumentacja — utrzymywana na bieżąco i WERYFIKOWANA (wymóg użytkownika)

Po KAŻDEJ zmianie kodu, w tym samym kroku. „Istotna" nie jest tu kryterium: zmiana czysto
wewnętrzna (refaktor bez zmiany zachowania) nie potrzebuje wpisu w CHANGELOG-u, ale KAŻDA
zmiana zachowania potrzebuje go zawsze — choćby wydawała się drobna. Wątpliwość
rozstrzygaj na korzyść wpisu:

1. **README.md** — musi odzwierciedlać aktualny stan (instalacja, uruchomienie,
   przykłady). Jeśli zmiana wpływa na użycie — zaktualizuj.
2. **CHANGELOG.md** — dopisz wpis (format „Keep a Changelog", sekcja
   `[Unreleased]`). Każda zmiana funkcjonalna ma wpis.
3. **ROADMAP.md** — odhacz zrealizowane pozycje, dopisz nowe ustalenia.
4. **docs/** — sekcja komponentu (ARCHITEKTURA, AGENCI, BEZPIECZENSTWO)
   zsynchronizowana z kodem. Istotne decyzje → nowy ADR w `docs/adr/`.
5. **Weryfikacja dokumentacji**: po edycji sprawdź, że przykłady i polecenia
   z dokumentów faktycznie działają (uruchom je). Nazwy agentów, profili,
   ścieżek i pól configu w dokach MUSZĄ zgadzać się z kodem i schematem.
   Rozbieżność dokumentacja↔kod traktuj jak błąd do naprawy.
6. **Wiki + PDF (dla ludzi, nie tylko deweloperów)** — utrzymywane na bieżąco razem z kodem;
   źródłem prawdy jest `docs/`, z którego wiki/PDF są aktualizowane (bez rozjazdu treści):
   - **Zrzuty ekranu z aplikacji**: wiki i PDF NIE mogą być samym tekstem — dołączaj aktualne
     screeny UI (panel, czat, konsola), by czytelnik widział, o czym mowa. Rób je z realnie
     uruchomionej aplikacji (podgląd/przeglądarka). Gdy UI danej funkcji jeszcze nie istnieje,
     wstaw wyraźny placeholder + opis, czego brakuje — nic nie znika po cichu.
   - **Interaktywny PDF** preferowany, gdy wykonalny (spis treści z odnośnikami, linki, zakładki) —
     nowocześniej niż statyczny tekst; statyczny PDF to fallback.
   - Zrzuty i zasoby binarne trzymaj w `docs/assets/` (uporządkowane, opisowo nazwane).
7. **Porządek dokumentacji i plików**: logiczna struktura folderów, brak duplikatów i martwych
   plików; przy zmianie nazwy/lokalizacji zaktualizuj WSZYSTKIE odnośniki.

Publikację wiki/PDF na zdalne (GitLab/GitHub) wykonuje operator — Ty utrzymujesz źródła i zasoby
w repo aktualne i gotowe.

## Dokumentacja kodu (wymóg użytkownika: „dokładna dokumentacja kodu")

- Każdy moduł, klasa publiczna i funkcja publiczna: docstring po polsku
  (cel, argumenty, zwracana wartość, wyjątki). **Cel: osoba z zewnątrz rozumie działanie
  bez czytania implementacji.**
- Każdy komponent (pakiet) ma odpowiadającą sekcję w `docs/`.
- Nieoczywiste decyzje projektowe zapisuj jako ADR (`docs/adr/NNNN-*.md`).
- **Kod „niebezpieczny"/wrażliwy** (sieć, sekrety, deserializacja, wykonywanie poleceń, sandbox,
  kryptografia): OBOWIĄZKOWO opisany — po co istnieje, jakie ryzyko niesie, jakie bramki go chronią.
  Dla każdego takiego fragmentu odpowiedz jawnie: **czy da się go usunąć** lub zastąpić
  bezpieczniejszym; jeśli nie — udokumentuj, dlaczego jest konieczny i jak jest w pełni
  zabezpieczony (defense-in-depth). Wynik przeglądu → `docs/BEZPIECZENSTWO.md`.

## Weryfikacja bezpieczeństwa (wymóg użytkownika)

Przy każdej zmianie dotykającej bezpieczeństwa lub przed zamknięciem etapu:

1. **Testy bezpieczeństwa** (`tests/security/`, marker `security`) muszą
   przechodzić. Dodawaj nowe niezmienniki przy każdej nowej powierzchni ataku.
2. **Niezmienniki domyślne**: deny-all egress, sandbox bez sieci, audit log
   włączony i niemodyfikowalny, szyfrowanie at-rest, zero telemetrii.
3. **Sekrety i dane prywatne**: nigdy w repo/obrazach/logach. `gitleaks` musi być czysty.
   Tylko referencje do Vault/SOPS w configu, nie same materiały. **Przed każdym push/publikacją**
   (repo, wiki, PDF, zrzuty ekranu) zweryfikuj, że pliki PUBLICZNE nie zawierają kluczy, tokenów,
   haseł ani danych prywatnych (operatora/użytkowników) mogących narazić projekt lub ludzi —
   skanuj i przeglądaj ręcznie nowe/zmienione pliki. **Uwaga na zrzuty ekranu**: mogą zdradzić
   dane widoczne na UI (ścieżki, tokeny, e-maile) — sprawdzaj je przed dołączeniem.
4. **ROE-gate**: każda akcja Puszkarza przechodzi przez twardą bramkę ROE.
   Cele spoza zakresu = twardy blok. Domyślnie dry-run.
5. **Puszkarz**: NIE generuje malware ani exploitów. W takim żądaniu zwraca
   odmowę i proponuje działanie defensywne (audyt/hardening/detekcja).
6. Przed zamknięciem etapu z komponentem bezpieczeństwa: krótka notatka
   weryfikacyjna w `docs/BEZPIECZENSTWO.md` (co sprawdzono, jak, wynik).

## Weryfikacja: sprawdzaj SKUTEK, nie deklarację (zasada nadrzędna)

To najdroższa lekcja tego projektu. Zielony zestaw testów **nie znaczy, że rzecz działa** —
znaczy, że przechodzą asercje, które ktoś napisał. Sześć realnych wad przeszło przez komplet
zielonych testów, bo testy sprawdzały DEKLARACJĘ zamiast SKUTKU:

| Sprawdzano | Czego nie wykryto |
|---|---|
| `argv` przekazywane Dockerowi | czy silnik faktycznie egzekwuje `--network none` |
| manifesty k8s przez parsowanie YAML | czy selektory trafiają w pod po przekształceniu kustomize |
| compose przez asercje na YAML | że `internal: true` **cicho wyłącza** publikowanie portów |
| konfigurację przez walidację schematu | że `run_tests` naprawdę uruchamia kontener |
| stan panelu przez odczyt pliku agenta | że router wybiera model wg innej reguły |

Dlatego:

1. **Uruchom to, co opisujesz.** Każda funkcja opisana w dokumentacji ma być choć raz wykonana
   w realnym środowisku, a wynik zaraportowany. Polecenia z README i `docs/` muszą działać.
2. **Testuj obserwowalny skutek**, gdy tylko środowisko na to pozwala. Test na `argv` jest
   dobry jako uzupełnienie, nigdy jako jedyny dowód dla warstwy bezpieczeństwa.
3. **Test wymagający środowiska pomija się z czytelnym powodem** (brak Dockera, brak klastra) —
   NIGDY nie udaje sukcesu. Pomiar, który zaokrągla „nie dało się sprawdzić" do „w porządku",
   jest gorszy niż brak pomiaru.
4. **Ograniczenia zapisuj wprost.** Jeśli czegoś nie zweryfikowano (u nas: gVisor, klaster
   k8s, podpis kodu), ma to być napisane w notatce weryfikacyjnej, a nie przemilczane.

## Nośność testów (każdy nowy test)

Po napisaniu testu **cofnij poprawkę i sprawdź, że test czerwieni się**. Test, który przechodzi
bez poprawki, nie chroni niczego, a daje fałszywe poczucie bezpieczeństwa — zdarzyło się to
w tym projekcie: asercja sprawdzała tylko `kind` wyniku, podczas gdy funkcja łapała wyjątki
szeroko, więc awaria objawiała się jako „niezdany przypadek", a nie jako czerwony test.

W teście porównującym dwie wartości dopisz asercję, że **są różne** (`assert przed != po`) —
inaczej test przechodzi także wtedy, gdy mechanizm w ogóle nie zadziałał.

### Trzy pułapki samej kontroli nośności

Kontrola nośności to też narzędzie pomiarowe i też potrafi kłamać. W Etapie 17 wykryła cztery
wady w testach, ale przy okazji dwa razy skłamała sama:

1. **Sprawdź, że mutacja trafiła tam, gdzie chciałeś.** Podmiana „pierwszego wystąpienia"
   wzorca, który występuje dwa razy (dwa modele Pydantica, dwa bloki `except` tego samego typu),
   psuje NIE TEN mechanizm — test zostaje zielony i wygląda to jak brak nośności, choć jest
   błędem narzędzia. Po mutacji upewnij się, że zmiana dotknęła właściwego miejsca; przy
   wzorcach powtarzalnych podmieniaj WSZYSTKIE wystąpienia albo użyj wzorca jednoznacznego.
2. **Asercja „pole istnieje" to nie asercja „pole ma wartość".** Test sprawdzający
   `"principal" in wpis` przechodził po usunięciu poprawki, bo dziennik audytu serializuje to
   pole ZAWSZE — także puste. Sprawdzaj WARTOŚĆ, a jeśli wymaga to uwierzytelnienia albo
   innego kontekstu, zbuduj ten kontekst w teście.
3. **Uważaj na awaryjne przejścia w samym teście.** Test przebudowy serwisu przechodził bez
   poprawki, bo jego fabryka miała fallback na ten sam PLIK — dane wracały z dysku i maskowały
   utratę. Jeśli test ma wykryć utratę stanu, użyj stanu ULOTNEGO.

Reguła nadrzędna: **mutacja, która nie zaczerwieniła testu, jest sygnałem do zbadania — nie do
przyjęcia.** Może nie działać test, ale równie dobrze może nie działać mutacja.

## Sprostowania — obowiązek, nie uprzejmość

Gdy wcześniejsze twierdzenie okaże się nieprawdziwe (w commicie, CHANGELOG-u albo notatce),
**popraw je jawnie**, z wyjaśnieniem, co zweryfikowano i jak. Nie usuwaj po cichu i nie licz,
że nikt nie zauważy. Dokumentacja, której nie można ufać w jednym miejscu, przestaje być
wiarygodna w całości.

Dotyczy to również pomiarów: jeśli okaże się, że metoda pomiaru była wadliwa (u nas: błędne
przechwytywanie kodu wyjścia w pętli powłoki), powiedz to wprost i powtórz pomiar rzetelnie.

## Warstwy importów (niezmiennik architektoniczny)

Moduł NIŻSZEJ warstwy nie importuje z WYŻSZEJ — patrz tabela w
[docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md). Złamanie tej reguły tworzy cykl importów, który
**działa dopóty, dopóki ktoś importuje moduły w szczęśliwej kolejności**, i wywraca się przy
pierwszym module, który zrobi inaczej. Nowy pakiet MUSI dostać warstwę — pilnuje tego
`tests/unit/test_import_layering.py`.

## Rytm etapów

Realizuj Etapy 0→6 (patrz ROADMAP.md). Po KAŻDYM etapie:
**testy → wpis do docs/ (+ CHANGELOG/ROADMAP) → commit**. Nie zaczynaj kolejnego
etapu, dopóki bieżący nie jest „ukończony" wg definicji powyżej.

## Rytm „na bieżąco" — obowiązek ciągły (wymóg użytkownika)

Poniższe NIE są czynnościami „na koniec etapu". Wykonuje się je **w tym samym kroku**, co
zmianę, która ich wymaga. Jeżeli którakolwiek pozycja zostaje w tyle, krok nie jest ukończony.

| Obszar | Co robisz na bieżąco |
|---|---|
| Dokumentacja | `docs/` zsynchronizowane z kodem; nowy komponent = nowa sekcja; istotna decyzja = ADR |
| README | odzwierciedla aktualny sposób instalacji, uruchomienia i użycia |
| CHANGELOG | wpis do `[Unreleased]` przy KAŻDEJ zmianie funkcjonalnej; sprostowania też |
| ROADMAP | odhaczone zrealizowane, dopisane nowe ustalenia i ograniczenia |
| Commity | małe, opisowe, z uzasadnieniem DLACZEGO — nie tylko CO |
| Push | po zielonych bramkach wypychasz na **oba** zdalne (zasady niżej) |
| Wydania i tagi | tag `vX.Y.Z` spójny z CHANGELOG-iem; nigdy na czerwonych bramkach |
| Gałąź | świadomie wybrana i zaraportowana; brak wiszących gałęzi tematycznych |
| Dokumentacja kodu | docstring po polsku dla każdego modułu, klasy i funkcji publicznej |
| Testy | KAŻDA zmiana zachowania ma test skutku; nośność sprawdzona; luki w pokryciu zapisane wprost |
| Audyt zmiany | KAŻDA zmiana audytowana; głębokość stopniowana (samokontrola → mutacja → pełny przegląd adwersaryjny); wynik zapisany, także zgłoszenia obalone |
| Audyty i security checki | przy każdej zmianie dotykającej bezpieczeństwa |
| Spójność wersji | numer wersji = tag = CHANGELOG = obrazy w plikach wdrożeniowych |
| PR, CI, jakość | stan pipeline'ów sprawdzony i zaraportowany |
| Podpisy | commity i tagi podpisane, gdy operator skonfigurował klucz (zasady niżej) |
| Kod niebezpieczny | opisany: po co istnieje, jakie ryzyko niesie, co go chroni |
| Sekrety | zweryfikowane, że nic prywatnego nie trafia do publicznego repo |
| Porządek w repo | brak osieroconych plików, duplikatów i martwych artefaktów |

## Synchronizacja repozytoriów, push i wydania

- Zdalne (utrzymywane W SYNCHRONIZACJI — te same commity, tagi, wiki):
  - GitLab: https://gitlab.com/Gh0s777tt/husarz
  - GitHub: https://github.com/Gh0s777tt/Husarz
- **Push wykonujesz samodzielnie, na bieżąco**, gdy spełnione są WSZYSTKIE warunki:
  1. bramki jakości zielone (ruff, black, mypy, pytest, `husarz eval`),
  2. `gitleaks` czysty na zmianach wchodzących do commita,
  3. dokumentacja zaktualizowana w tym samym kroku,
  4. `git status` czysty, właściwa gałąź.
- **Czego NIE robisz samodzielnie, nawet przy rytmie „na bieżąco":**
  - `push --force` / `--force-with-lease` na gałąź główną — nadpisuje cudzą pracę;
    przygotuj polecenie i poproś operatora o decyzję,
  - usuwanie gałęzi zdalnych, tagów i wydań — operacje nieodwracalne,
  - publikowanie wiki i PDF na zdalne oraz tworzenie GitHub/GitLab Release — wymaga
    świadomej decyzji o tym, co staje się publiczne,
  - zapisywanie poświadczeń w repozytorium — nigdy, w żadnej formie.
- **Higiena gita** — po każdym istotnym kroku zweryfikuj i zaraportuj: wszystko zacommitowane,
  sensowne komunikaty, żadnych zgubionych zmian, właściwa gałąź, gałęzie tematyczne zmergowane.
- **Wersjonowanie (SemVer + tagi)**: tag `vX.Y.Z` spójny z `CHANGELOG.md` (sekcja
  `[Unreleased]` → numer wersji). Tag to migawka, w której kod + docs + wiki + PDF są spójne.
  NIE taguj stanu z czerwonymi bramkami. Przy każdym wydaniu sprawdź, że numer wersji zgadza
  się we WSZYSTKICH miejscach: `pyproject.toml`, `husarz.__version__`, CHANGELOG, tag oraz
  pliki wdrożeniowe (`deploy/compose/*`, `deploy/k8s/*`) — rozjazd tam jest niewidoczny
  w testach jednostkowych i ujawnia się dopiero na wdrożeniu.
- **Podpisy commitów i tagów**: jeżeli operator skonfigurował klucz (`user.signingkey`
  + `commit.gpgsign`/`tag.gpgsign`), podpisuj. Jeżeli NIE — **nie konfiguruj go za niego
  i nie podpisuj**: podpis to kryptograficzne oświadczenie konkretnej osoby, że firmuje tę
  zmianę, więc decyzja o jego włączeniu należy do operatora. Zawsze natomiast dodawaj
  `Co-Authored-By`, żeby współautorstwo było jawne.

## Porządek i struktura repozytorium (automatyczne pilnowanie)

Po każdym istotnym kroku sprawdź i napraw:

- **Brak osieroconych artefaktów**: plików, do których nic nie prowadzi; katalogów po
  usuniętych funkcjach; duplikatów dokumentacji mówiących różne rzeczy o tym samym.
- **Odnośniki żyją**: przy zmianie nazwy albo lokalizacji zaktualizuj WSZYSTKIE odwołania.
  `mkdocs build --strict` musi przechodzić — traktuj to jak bramkę jakości, nie ozdobę.
- **Nic wygenerowanego w gicie**: `site/`, `dist/`, `data/`, `audit/`, `artifacts/`,
  `workspace/` i wagi modeli są ignorowane. Przed commitem sprawdź `git status`.
- **Śmieci systemu plików**: na wolumenach bez natywnych atrybutów rozszerzonych (exFAT,
  NTFS, dyski sieciowe) macOS tworzy sidecary `._*`. Blokują `docker build` **jeszcze przed
  zastosowaniem `.dockerignore`** i wpadają w globy `*.md`/`*.py`. Sprzątaj je
  `scripts/clean_sidecars.py`; odrastają przy każdym zapisie, więc to krok do POWTARZANIA.
- **Każdy pakiet ma sekcję w `docs/`** — brak pokrycia traktuj jak brak testu.
- **Skrypty operatora w `scripts/`** z docstringiem mówiącym, po co istnieją i kiedy się je
  uruchamia. Nie są zależnością projektu ani częścią CI.

## Sekrety i dane prywatne — kontrola przed każdą publikacją

Repozytoria są **publiczne**. Przed każdym pushem, wydaniem i publikacją wiki/PDF:

1. `gitleaks` czysty na zmianach wchodzących do commita — bramka twarda.
2. Przejrzyj **ręcznie** nowe i zmienione pliki. Skaner nie wykryje wszystkiego: adresów
   wewnętrznych, nazw kont, ścieżek zdradzających strukturę katalogów operatora.
3. **Zrzuty ekranu są osobnym ryzykiem** — UI potrafi pokazać ścieżki, tokeny, e-maile
   i treść rozmów. Obejrzyj każdy przed dołączeniem; rób je wyłącznie na instancji z danymi
   demonstracyjnymi.
4. W configu wyłącznie **referencje** do sekretów (`env:` / `file:` / `vault:` / `sops:`),
   nigdy same materiały. Jeżeli funkcja wymaga ZAPISU sekretu (np. token z OAuth), zapisuj go
   przez dostawcę sekretów, a w konfiguracji zostaw referencję — model „config nie zawiera
   materiału" musi zostać nienaruszony.
5. Pliki pomiarowe i dzienniki (`data/runs/`, `audit/`) nie trafiają do dokumentacji, wiki
   ani zrzutów.

## Szybki start (dev)

```bash
python -m venv .venv
# Windows:            .venv\Scripts\python.exe -m pip install -e ".[dev]"
# Linux / macOS:      .venv/bin/python -m pip install -e ".[dev]"
python -m husarz.launcher.cli validate --config ./config
python -m husarz.launcher.cli eval --config ./config --prompts ./prompts
```

Uruchomienie z modelem lokalnym (Ollama) opisuje README, sekcja „Lokalny czat i kodowanie".
Pamiętaj o kroku, który README wskazuje osobno: dostarczony `config/routing.yaml` kieruje
agentów na modele vLLM, więc orkiestracja na samej Ollamie wymaga przypisania ich do modelu
lokalnego.
