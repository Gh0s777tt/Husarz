# Router modeli (Etap 1)

Router dobiera model do żądania i wykonuje wywołanie przez warstwę OpenAI-compat
(vLLM/Ollama/SGLang), z fallbackami i kontrolą kosztów. Wszystko sterowane
konfiguracją (`models.yaml` + `routing.yaml`) — zero hardcode. Kod: `husarz.router`.

## Przepływ żądania

```
ChatRequest ─▶ ModelRouter.complete(agent|model|tags)
                 │
                 ├─ select_candidates()  ── lista modeli (preferencje + fallbacki)
                 ├─ RateLimiter.acquire() ── kontrola kosztów (opcjonalna)
                 ├─ _apply_cost_controls() ── clamp max_tokens
                 └─ dla kolejnych kandydatów:
                        bramka wizyjna    ── obrazy tylko do modelu vision:true
                        bramka budżetu    ── prompt + rezerwa ≤ context_length
                        bramka egress     ── host na allowliście
                        build_client(spec) ─▶ ModelClient.chat() ─▶ ChatResponse
                        (błąd backendu → następny kandydat; wszyscy zawiodą → AllModelsFailedError)
```

## Wybór modelu (`select_candidates`)

Kolejność preferencji (pierwszy wygrywa; reszta to fallbacki):

1. jawny `model=...` (jeśli podany),
2. model agenta z `routing.agent_models` (o ile nie `auto`),
3. modele preferowane przez reguły `routing.rules`, których `match_tags` ⊆ żądane `tags`,
4. dowolny model posiadający wszystkie żądane `tags`,
5. `models.default` — gdy nic nie wybrano.

Do każdego wybranego modelu dołączany jest jego łańcuch `fallback` (o ile
`routing.fallbacks_enabled`). Zwracane są wyłącznie modele **włączone**
(`enabled: true`); model wyłączony jest pomijany, ale jego fallbacki nadal
działają. Rozwijanie fallbacków jest odporne na cykle (każdy model odwiedzany raz).

> Uwaga: pole `routing.strategy` ma obecnie aktywną wyłącznie wartość `tags`
> (opisany wyżej wybór). Wartości `cost`/`latency` to placeholdery na kolejne etapy.

## Bramka egress (deny-all)

Przed połączeniem z modelem router sprawdza endpoint względem `security.egress`
([egress.py](https://github.com/Gh0s777tt/Husarz/blob/main/src/husarz/router/egress.py)):

- endpointy **lokalne/prywatne** (loopback, RFC 1918, `.local`) są zawsze dozwolone
  (lokalny vLLM/Ollama nie jest ruchem do WAN),
- host **zdalny** wymaga `default_policy: allow` albo obecności na `egress.allowlist`
  (dokładnie lub jako subdomena); w przeciwnym razie kandydat jest pomijany
  (`EgressError`) i router próbuje kolejnego (np. lokalnego fallbacku).

To kontrola na poziomie aplikacji (defense-in-depth). Pełne wymuszenie sieciowe
(NetworkPolicy, sandbox) należy do Etapu 4/6.

## Kontrola kosztów

- `routing.cost_controls.max_tokens_per_request` — `max_tokens` żądania jest
  przycinany do limitu (albo ustawiany, gdy nie podano).
- `routing.cost_controls.max_requests_per_minute` — `RateLimiter` (token bucket)
  z uzupełnianiem w tempie/min. Przekroczenie → `RateLimitExceededError`.

## Budżet okna kontekstu

Router sprawdza dla KAŻDEGO kandydata, czy prompt **razem z rezerwą na odpowiedź** zmieści się
w jego `context_length`. Bez tego backend zwracał błąd albo po cichu ucinał kontekst, a agent
w pętli narzędziowej wypalał limit iteracji, nie wiedząc dlaczego — problem zaobserwowany
przy modelu 7B, gdzie rozmowa rośnie o wyniki narzędzi.

**Niezmieszczenie się jest POMINIĘCIEM kandydata, nie błędem.** Prompt za duży dla modelu 7B
może wejść do fallbacku o większym oknie, więc router traktuje to jak każdą inną przyczynę
pominięcia i próbuje dalej. Dopiero gdy nie starczy okna u NIKOGO, leci `AllModelsFailedError`
z powodem zawierającym liczby.

Rezerwa na odpowiedź bierze się kolejno z: `max_tokens` żądania (już po kontroli kosztów),
`max_tokens` z rejestru modelu, a na końcu z wartości zapasowej 512. Bez tej rezerwy prompt
mógłby wypełnić okno co do tokena, zostawiając model bez miejsca na odpowiedź.

### Oszacowanie, nie pomiar — i dlaczego

Dokładne policzenie tokenów wymaga tokenizera KONKRETNEGO modelu, a rdzeń Husarza ma pięć
zależności runtime i żadna nim nie jest. Dokładanie `transformers` czy `tiktoken` wyłącznie po
to, by zmierzyć długość, byłoby złą wymianą. Szacujemy więc — ale **zachowawczo i na podstawie
pomiaru**, nie intuicji.

Kalibracja wykonana realnym tokenizerem (`qwen2.5-coder:7b` przez Ollamę, licznik
`prompt_eval_count`):

| Rodzaj treści | znaków na token |
|---|---|
| polski, proza | 2,19 |
| polski, techniczny | 2,08 |
| kod (Python) | 2,88 |
| angielski | 2,70 |
| **JSON / wpis dziennika** | **1,68** |

Dzielnik bierzemy z ostatniego wiersza (zaokrąglony do 1,6), bo to najgorszy przypadek dla NAS:
wyniki narzędzi w pętli agentowej są właśnie JSON-em. Doliczamy też zmierzony narzut szablonu
czatu — 32 tokeny stałe i 6 na wiadomość.

**Skutek: szacujemy Z GÓRY.** Dla typowej polskiej rozmowy wychodzi ~30% więcej, niż naliczy
tokenizer, więc odmówimy nieco wcześniej, niż trzeba. To świadomy wybór — fałszywa odmowa
z czytelnym komunikatem jest tańsza niż cicha awaria backendu w środku pętli narzędziowej.

!!! warning "Obrazy NIE są liczone"
    Modele wizyjne liczą obrazy osobno i zależnie od rozdzielczości, czego nie da się odtworzyć
    bez tokenizera modelu. Prompt z obrazami będzie więc **niedoszacowany** — bramka go
    przepuści, choć może się nie zmieścić. Ograniczenie znane i nieobejściowe bez zależności,
    której rdzeń świadomie nie ma.

## Klienci i transport

- `ModelClient` — protokół: `chat(ChatRequest) -> ChatResponse`.
- `OpenAICompatClient` — buduje payload `chat/completions`, scala parametry
  (`spec.params` + nadpisania z żądania), dodaje nagłówek `Authorization`
  z klucza API i parsuje odpowiedź.
- `MockClient` — backend `mock`: deterministyczna odpowiedź, **bez sieci**
  (używany w testach i dev bez wag).
- `Transport` — warstwa HTTP oddzielona od logiki. `HttpxTransport` (produkcja,
  leniwy import `httpx`) albo transport wstrzyknięty w testach. Dzięki temu testy
  **nie wykonują połączeń sieciowych** (zgodnie z deny-all egress).

Wszystkie backendy sieciowe (`vllm`, `ollama`, `sglang`, `openai_compat`)
używają jednego `OpenAICompatClient` — różni je tylko endpoint z `models.yaml`.

## Sekrety (klucz API)

Klucz API pochodzi z `ModelSpec.api_key_ref` (np. `env:GLM_API_KEY`,
`vault:...`) i jest rozwiązywany w runtime przez dostawcę sekretów
(`husarz.config.secrets`). **Nigdy** nie jest wpisywany w plik ani w `params`.

## Przykład użycia

```python
from husarz.config import load_config
from husarz.router import ModelRouter, ChatRequest, ChatMessage

config = load_config("./config")
router = ModelRouter(config)  # domyślna fabryka klientów (mock/OpenAI-compat)

resp = router.complete(
    ChatRequest(messages=[ChatMessage("user", "Napisz test w pytest.")]),
    tags=["code"],           # albo agent="kopijnik", albo model="glm-main"
)
print(resp.model, resp.content)
```

> Ten przykład wymaga **działającego endpointu** modelu (vLLM/Ollama/SGLang pod
> adresem z `models.yaml`). Do pracy bez sieci użyj modelu z `backend: mock`
> (wtedy `MockClient` zwraca deterministyczną odpowiedź) albo wstrzyknij własną
> `client_factory`.

## Testowanie

- Logika wyboru i fallbacków — czysta, testowana bez sieci.
- Klient — testowany z wstrzykniętym transportem (payload/parsing/błędy).
- `ModelRouter` — testowany z wstrzykniętą fabryką klientów (fallback, limity, koszty).
- Test integracyjny — selekcja na realnej konfiguracji repo.

Szczegóły decyzji: [ADR-0003](adr/0003-router-modeli.md).
