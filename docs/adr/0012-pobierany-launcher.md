# ADR-0012: Pobierany launcher (serwer + konsola w przeglądarce)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 10

## Kontekst

Wymóg użytkownika: „można było pobrać gotowy launcher jak w Claude". Potrzebny jeden
plik, który u użytkownika (bez Pythona) startuje platformę i otwiera konsolę.

## Decyzja

### Launcher desktopowy `husarz-app` (przyjazny double-click)

Osobny punkt wejścia (`husarz.launcher.app:main`), który — inaczej niż `husarz`
(wymaga podkomendy) — bez argumentów startuje serwer na loopbacku i **otwiera konsolę
w przeglądarce**. Deleguje do `husarz up --open`, więc reużywa CAŁEJ logiki i bramek
bezpieczeństwa (konta, Git, fail-closed) — brak duplikacji.

### Otwieranie przeglądarki w tle, best-effort

`_open_browser_async` uruchamia `webbrowser.open` w wątku daemon po krótkiej zwłoce
(serwer zdąży nasłuchiwać). Wstrzykiwalny „opener" → testowalne bez realnej
przeglądarki. Błąd otwarcia (headless/brak DISPLAY) jest cicho ignorowany — NIE może
wywrócić serwera. Otwieranie tylko dla loopbacku.

### Pakowanie: PyInstaller onefile

`packaging/husarz.spec` buduje jeden plik: rdzeń + konsola (`collect_data_files`) +
domyślne `config/`/`prompts/`. Przy uruchomieniu jako binarka (frozen) domyślne
katalogi wskazują na zasoby dołączone (`sys._MEIPASS`), więc działa out-of-the-box;
operator nadpisuje `--config`/`--prompts`. Bez sekretów/wag.

### CI wypuszcza pobieralny artefakt

`release.yml` buduje binarki dla Windows/Linux/macOS (natywnie na każdym OS) i
publikuje jako artefakty przebiegu; dla tagu `v*` dołącza do GitHub Release.

## Konsekwencje

- (+) „Jeden plik do pobrania" spełnia życzenie użytkownika; UX aplikacji.
- (+) Zero duplikacji logiki/bezpieczeństwa (delegacja do `up`).
- (+) Rdzeń launchera w pełni testowalny (opener wstrzykiwalny, delegacja mockowana).
- (−) **Podpis kodu/notaryzacja** i zaufana dystrybucja — po stronie operatora
  (certyfikaty); bez nich systemy ostrzegają przy pobraniu.
- (−) Sama binarka budowana jest w CI/na maszynie operatora (PyInstaller), nie w
  środowisku dev — spec/CI są autorskim artefaktem, nie uruchamianym w testach unit.
- (−) Bogatszy desktop (Tauri, auto-update, tray) — odłożony.

## Alternatywy odrzucone

- **Zmiana `husarz` na domyślną akcję bez podkomendy**: łamałaby istniejący kontrakt
  CLI (walidacja podkomendy) — wybrano osobny `husarz-app`.
- **Electron/Tauri od razu**: cięższy toolchain (Node/Rust) — PyInstaller daje
  pobieralny plik najmniejszym kosztem; desktop-natywny to przyszłość.
- **Serwer bez auto-otwierania**: gorszy UX „launchera" — dodano `--open`.
