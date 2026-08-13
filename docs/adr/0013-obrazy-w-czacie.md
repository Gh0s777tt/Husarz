# ADR-0013: Obrazy w czacie (modele wizyjne)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 11

## Kontekst

Wymóg użytkownika: „możliwość dodawania plików, zdjęć, folderów do chatu". Pliki
i foldery dostarczył Etap 8 (`attachments`). Ten etap dodaje **zdjęcia** jako wejście
dla modeli wizyjnych (multimodalnych), zachowując suwerenność (brak egressu, brak
cudzych API) i twarde traktowanie danych wejściowych jako NIEZAUFANYCH.

## Decyzja

### Obraz jako część multimodalna OpenAI-compat (bez pobierania z URL)

Klient routera buduje treść wiadomości jako listę części tylko wtedy, gdy są obrazy:
`[{type:"text", ...}, {type:"image_url", image_url:{url:"data:<mime>;base64,<...>"}}]`.
Obraz jest przekazywany **wyłącznie jako data-URI** (base64 w ciele), nigdy jako
zewnętrzny URL — brak powierzchni SSRF/egress po stronie routera. Dla wiadomości bez
obrazów treść pozostaje zwykłym `str` (kompatybilność wsteczna z każdym backendem).

### Zaufanie do bajtów, nie do deklaracji (magic-bytes sniffing)

Serwer NIE ufa deklarowanemu MIME ani rozszerzeniu nazwy. `_sniff_image_mime`
rozpoznaje typ z sygnatur (PNG `\x89PNG\r\n\x1a\n`, JPEG `\xff\xd8\xff`, GIF
`GIF87a/89a`, WEBP `RIFF….WEBP`). Cokolwiek nierozpoznane → odrzucone (`400`).
`sanitize_images` dekoduje base64 z walidacją, egzekwuje limit liczby i rozmiaru
(dekodowanego) per obraz, a następnie **re-enkoduje** znormalizowaną treść — do
backendu trafia dokładnie to, co serwer zweryfikował.

### Bramka „vision" na poziomie modelu — i na KAŻDYM kandydacie routera

`ModelSpec.vision: bool` (domyślnie `false`). `/api/chat` przyjmuje obrazy tylko gdy
wybrany model ma `vision: true` (np. `husarz-vision` → llava/qwen2-vl w Ollamie);
w przeciwnym razie `400` z czytelnym komunikatem po polsku. Zapobiega to wysłaniu
obrazów do modelu tekstowego (cichy błąd/halucynacja) i trzyma politykę w konfiguracji,
nie w kodzie („zero hardcode").

Sama bramka w handlerze nie wystarcza: `ModelRouter.complete` rozwija łańcuch
**fallbacków**, więc po awarii modelu wizyjnego to samo żądanie (z obrazami) trafiłoby
do modelu tekstowego. Dlatego bramka jest egzekwowana też **na poziomie kandydata**:
gdy żądanie niesie obrazy, router pomija każdego kandydata z `vision: false` (obraz
NIE trafia do modelu bez wizji — patrz `test_images_skip_nonvision_fallback`). Jeśli
żaden kandydat nie jest wizyjny, żądanie kończy się błędem zamiast halucynacji.
Obrazy wiąże się z ostatnią wiadomością o roli `user` (nie ślepo z `messages[-1]`).

### Limity i rozmiar żądania

Sekcja `chat.images` (`enabled`, `max_images`, `max_bytes_per_image`) — wszystko
konfigurowalne. `chat.max_request_bytes` podniesione do 12 MB (base64 zwiększa rozmiar
~+33%); nadmiar → `413`. Ponieważ obrazy istotnie zwiększają powierzchnię DoS,
`BodySizeLimitMiddleware` egzekwuje limit dwutorowo: szybko po `Content-Length` oraz
**buforując ciało z twardym sufitem** — dzięki temu żądanie `Transfer-Encoding: chunked`
(bez `Content-Length`) nie omija kontroli ani nie doprowadza do OOM przed walidacją
(patrz `test_chunked_body_over_limit_returns_413_end_to_end`). Obrazy liczą się do
limitu tokenów konta tak samo jak reszta kontekstu.

## Konsekwencje

- (+) Zdjęcia w czacie bez opuszczania infrastruktury (data-URI, lokalny model wizyjny).
- (+) Odporność na podszycie typu (magic-bytes), spójna z filozofią NIEZAUFANYCH danych.
- (+) Wymienność modelu przez config — nowy model wizyjny = wpis w `models.yaml`.
- (−) Faktyczna jakość opisu obrazu zależy od lokalnego modelu (llava/qwen2-vl) —
  poza zakresem rdzenia; operator dobiera wagi.
- (−) Brak (na razie) OCR/segmentacji ani kadrowania — surowy obraz do modelu.

## Alternatywy odrzucone

- **Przekazywanie URL obrazu do modelu**: otwiera SSRF/egress (model pobiera z sieci) —
  sprzeczne z deny-all. Wybrano wyłącznie data-URI (base64 w ciele).
- **Zaufanie zadeklarowanemu MIME/rozszerzeniu**: łatwe do podrobienia — sniff z bajtów.
- **Osobny endpoint `/api/vision`**: dublowałby logikę czatu (persona, limity, audyt) —
  wybrano jedną ścieżkę `/api/chat` z bramką `vision` na modelu.
- **Brak bramki (każdy model dostaje obraz)**: cichy błąd na modelu tekstowym — twardy
  `400` jest czytelniejszy.
