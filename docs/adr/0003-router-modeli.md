# ADR-0003: Router modeli (warstwa OpenAI-compat)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 1

## Kontekst

Husarz musi kierować żądania do wielu backendów (vLLM/Ollama/SGLang), wybierać
model po tagach/capabilities, obsługiwać fallbacki i limity kosztów — sterowany
wyłącznie konfiguracją. Testy nie mogą wykonywać połączeń sieciowych (deny-all
egress), a klucze API muszą pochodzić z sekretów, nie z kodu.

## Decyzja

### Własny, cienki klient OpenAI-compat zamiast LiteLLM

Implementujemy własną, minimalną warstwę OpenAI-compat (`OpenAICompatClient`).
Wszystkie wspierane backendy eksponują `/v1/chat/completions`, więc jeden klient
wystarcza; różni je jedynie endpoint z `models.yaml`.

- (+) Brak ciężkiej zależności i jej domyślnych zachowań „phone home"/telemetrii.
- (+) Pełna kontrola nad payloadem, nagłówkami i parsowaniem.
- (−) Sami utrzymujemy kompatybilność (akceptowalne — kontrakt jest wąski).

Wzorzec pozostaje zgodny z LiteLLM/OmniRoute (routing po capabilities), ale bez
wiązania się z konkretną biblioteką.

### Transport wstrzykiwalny (testowalność, brak sieci)

`Transport` to protokół `(url, headers, payload, timeout) -> dict`. Produkcja
używa `HttpxTransport` (leniwy import `httpx`), a testy wstrzykują własny
transport. Dzięki temu cała logika klienta jest testowana **bez sieci**.

### Wybór modelu i fallbacki

`select_candidates` to czysta funkcja zwracająca uporządkowaną listę modeli
(preferencje → fallbacki), filtrowaną do włączonych i odporną na cykle
(limit głębokości). Model wyłączony jest pomijany, ale jego fallbacki działają.

### Kontrola kosztów

`max_tokens_per_request` przycina żądanie; `max_requests_per_minute` egzekwuje
`RateLimiter` (token bucket) z **wstrzykiwalnym zegarem** (deterministyczne testy).

### Sekrety

Klucz API pochodzi z `ModelSpec.api_key_ref` rozwiązywanego przez dostawcę
sekretów w runtime — nie z pliku ani z `params`.

## Konsekwencje

- (+) Router w pełni sterowany konfiguracją; nowy model/endpoint = edycja YAML.
- (+) 100% testów bez sieci; fallbacki i limity łatwo weryfikowalne.
- (+) Zgodność z zasadą „sekrety tylko jako referencje".
- (−) Brak zaawansowanych funkcji LiteLLM (retry z backoff, wiele providerów) —
  dołożymy w miarę potrzeb, świadomie i konfigurowalnie.

## Alternatywy odrzucone

- **LiteLLM jako zależność**: ciężka, własne domyślne zachowania i telemetria,
  trudniejsza kontrola egzekwowania deny-all egress.
- **Realne HTTP w testach**: łamie deny-all egress i czyni testy kruchymi.
