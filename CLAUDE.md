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

Uruchamiaj z aktywnego venv (`.venv`):

```bash
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m black --check src tests
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m pytest -q
```

Definicja „ukończone": kod otypowany, testy (unit + integration + security)
zielone, brak sekretów (gitleaks czysty), **cała dokumentacja zaktualizowana**
(README/CHANGELOG/ROADMAP/`docs/`/wiki/PDF), repozytorium spójne i **gotowe do push**
(zacommitowane, właściwy branch, tag przy wydaniu), stan zaraportowany operatorowi.

## Dokumentacja — utrzymywana na bieżąco i WERYFIKOWANA (wymóg użytkownika)

Po KAŻDEJ istotnej zmianie kodu, w tym samym kroku:

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

## Rytm etapów

Realizuj Etapy 0→6 (patrz ROADMAP.md). Po KAŻDYM etapie:
**testy → wpis do docs/ (+ CHANGELOG/ROADMAP) → commit**. Nie zaczynaj kolejnego
etapu, dopóki bieżący nie jest „ukończony" wg definicji powyżej.

## Synchronizacja repozytoriów, wersjonowanie i push-readiness

- Zdalne (utrzymywane W SYNCHRONIZACJI — te same commity, tagi, wiki):
  - GitLab: https://gitlab.com/Gh0s777tt/husarz
  - GitHub: https://github.com/Gh0s777tt/Husarz
- **Push, mirroring oraz publikację wiki/PDF wykonuje OPERATOR** (wymaga uwierzytelnienia).
  Ty przygotowujesz WSZYSTKO do publikacji i jawnie raportujesz gotowość; nigdy nie zapisujesz
  poświadczeń w repo i nie próbujesz pushować samodzielnie.
- **Higiena gita na bieżąco** — po każdym etapie/istotnym kroku zweryfikuj i zaraportuj:
  - wszystko zacommitowane (czyste `git status`), sensowne komunikaty, żadnych zgubionych zmian;
  - właściwy branch; gałęzie tematyczne zmergowane do głównej po zakończeniu (bez wiszących/martwych);
  - historia gotowa do push na OBA zdalne; wskaż operatorowi dokładnie, co jest do wypchnięcia/zmergowania.
- **Wersjonowanie (SemVer + tagi)**: przy wydaniu nadaj/zaproponuj tag `vX.Y.Z` spójny z
  `CHANGELOG.md` (sekcja `[Unreleased]` → numer wersji). Tag to migawka, w której kod + docs +
  wiki + PDF są spójne. NIE taguj stanu z czerwonymi bramkami jakości.

## Szybki start (dev)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m husarz.launcher.cli validate --config ./config
```
