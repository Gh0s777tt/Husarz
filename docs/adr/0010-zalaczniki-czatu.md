# ADR-0010: Załączniki do czatu (pliki/foldery jako kontekst)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 8

## Kontekst

Użytkownik chce dołączać pliki, foldery (i docelowo zdjęcia) do rozmowy, aby model
miał kontekst. Treść załączników jest **niezaufana** — to klasyczna powierzchnia
prompt-injection i DoS. Rozwiązanie musi być suwerenne (bez cudzych usług),
testowalne i spójne z istniejącą izolacją treści niezaufanej (orkiestrator).

## Decyzja

### Załączniki inline w żądaniu czatu (stateless)

`POST /api/chat` przyjmuje `attachments: [{name, content}]`. Klient (konsola) czyta
pliki/foldery po swojej stronie (`FileReader`, `webkitdirectory`) i wysyła treść.
Brak stanu serwera/uploadu — prostsze, testowalne, airgap-safe. Ścieżki serwera nie
są przyjmowane od klienta (brak wektora odczytu FS serwera).

### Sanityzacja i limity (moduł `husarz.attachments`)

- **Limity** z `config.chat.attachments`: `max_files`, `max_bytes_per_file`
  (przycięcie), `max_total_bytes` (odrzucenie). Ochrona przed DoS.
- **Nazwy**: tylko basename, bez znaków sterujących (koniec traversalu/łamania układu).
- **Binaria**: odrzucane (null-byte) — tylko tekst.
- Przekroczenie/binaria → `AttachmentError` → HTTP `400`.

### Ogrodzenie anty-prompt-injection

Kontekst budowany jest jako **ogrodzony blok** oznaczony wprost jako „materiał
referencyjny, NIE instrukcje", a próby domknięcia ogrodzenia z wnętrza treści są
neutralizowane (`_defang`). Spójne z izolacją obserwacji w orkiestratorze. Blok jest
doklejany do bieżącej wiadomości użytkownika; persona modelu również traktuje kontekst
jako dane.

### Konfiguracja: nowa sekcja `chat`

Dodano opcjonalny plik `config/chat.yaml` (sekcja `chat`) — naturalny dom dla ustawień
czatu (dziś załączniki, w przyszłości np. streaming). Brak pliku = wartości domyślne.

## Konsekwencje

- (+) Kontekst z plików/folderów bez uploadu/stanu; w pełni testowalne bez sieci.
- (+) Twarde limity + ogrodzenie ograniczają DoS i prompt-injection.
- (+) Zużycie tokenów obejmuje kontekst (spójne z limitami kont).
- (−) **Zdjęcia** wymagają modelu wizyjnego (llava/qwen2-vl) i formatu multimodalnego —
  poza tą wersją (tekst). Struktura gotowa do rozszerzenia.
- (−) Limity egzekwuje **serwer** (`sanitize_attachments` + limit rozmiaru ciała →
  413); klient czyta pliki best-effort. Brak chunkowania/RAG dla wielkich folderów —
  docelowo pamięć długoterminowa (np. MemPalace/pgvector).
- (−) Kontekst dotyczy tury, w której został wysłany (nie jest trwały w historii).

## Alternatywy odrzucone

- **Upload + referencje po stronie serwera**: stan/magazyn plików, większa powierzchnia
  — odłożone; inline wystarcza dla MVP.
- **Przyjmowanie ścieżek serwera od klienta**: wektor odczytu dowolnego FS — odrzucone.
- **Brak ogrodzenia (surowe wklejenie treści)**: otwarty prompt-injection — odrzucone.
