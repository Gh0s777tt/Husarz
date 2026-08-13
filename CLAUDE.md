# CLAUDE.md — przewodnik pracy nad Husarzem

Ten plik jest wczytywany na starcie sesji Claude Code. Definiuje twarde zasady
pracy nad projektem **Husarz**. Trzymaj się ich rygorystycznie.

## Czym jest Husarz

Suwerenna, samodzielnie hostowana, wieloagentowa platforma AI (Chorągiew).
Zasada nadrzędna: **suwerenność danych** — modele i dane NIE opuszczają
infrastruktury użytkownika bez wyraźnej zgody. Domyślnie **deny-all egress**.

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
zielone, brak sekretów (gitleaks czysty), dokumentacja zaktualizowana.

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

## Dokumentacja kodu (wymóg użytkownika: „dokładna dokumentacja kodu")

- Każdy moduł, klasa publiczna i funkcja publiczna: docstring po polsku
  (cel, argumenty, zwracana wartość, wyjątki).
- Każdy komponent (pakiet) ma odpowiadającą sekcję w `docs/`.
- Nieoczywiste decyzje projektowe zapisuj jako ADR (`docs/adr/NNNN-*.md`).

## Weryfikacja bezpieczeństwa (wymóg użytkownika)

Przy każdej zmianie dotykającej bezpieczeństwa lub przed zamknięciem etapu:

1. **Testy bezpieczeństwa** (`tests/security/`, marker `security`) muszą
   przechodzić. Dodawaj nowe niezmienniki przy każdej nowej powierzchni ataku.
2. **Niezmienniki domyślne**: deny-all egress, sandbox bez sieci, audit log
   włączony i niemodyfikowalny, szyfrowanie at-rest, zero telemetrii.
3. **Sekrety**: nigdy w repo/obrazach/logach. `gitleaks` musi być czysty.
   Tylko referencje do Vault/SOPS w configu, nie same materiały.
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

## Repozytoria zdalne

- GitLab: https://gitlab.com/Gh0s777tt/husarz
- GitHub: https://github.com/Gh0s777tt/Husarz

Push wykonuje operator (wymaga uwierzytelnienia). Nie zapisuj poświadczeń w repo.

## Szybki start (dev)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
.venv/Scripts/python.exe -m husarz.launcher.cli validate --config ./config
```
