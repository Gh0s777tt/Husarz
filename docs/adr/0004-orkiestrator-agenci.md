# ADR-0004: Rdzeń agentów i orkiestrator „Husarz"

- Status: przyjęty
- Data: 2026-08-13
- Etap: 2

## Kontekst

Potrzebujemy agentów Chorągwi i hetmana, który dekomponuje zadanie, deleguje je
do specjalistów, obserwuje wyniki, koryguje plan i syntetyzuje odpowiedź. Wymogi:
sterowanie konfiguracją (nowy agent bez zmian w rdzeniu), pełna testowalność bez
sieci, odporność na „szum" z modelu.

## Decyzja

### Agenci zależą od protokołu routera, nie od konkretnej klasy

`BaseAgent.run` i `Orchestrator` przyjmują `SupportsComplete` (protokół z metodą
`complete(...)`), który spełnia `ModelRouter`. Dzięki temu testy wstrzykują
skryptowany router i **nie wykonują połączeń sieciowych**.

### Klasy Towarzysz/Pocztowy wybierane przez konfigurację

Loader mapuje `agent_class` na klasę (`Towarzysz`/`Pocztowy`). W Etapie 2 obie
dzielą zachowanie `run` (pojedyncze wywołanie); Towarzysz zyska pętlę narzędziową
w Etapie 3. Prompty ładowane z `prompts/*.md` (edytowalne bez rekompilacji).

### Orkiestracja jako jawne fazy ze znacznikami

Pętla plan → deleguj → obserwuj → refleksja → synteza. Wiadomości do hetmana
zaczynają się znacznikiem fazy (`[FAZA:...]`), co czyni fazę jednoznaczną dla
modelu i umożliwia deterministyczne testy. Plan i refleksja to **JSON**, parsowany
odpornie (czysty lub osadzony w prozie; nieparsowalny → pusty plan / „zakończ").

### Robustność zamiast twardych błędów

Krok wskazujący nieznanego agenta jest pomijany z adnotacją; nieparsowalny plan
daje pustą listę kroków, a synteza i tak następuje. Orkiestrator ma działać mimo
niedoskonałych odpowiedzi modelu.

## Konsekwencje

- (+) Pełne testy wieloagentowe bez sieci; nowy agent = plik YAML + prompt.
- (+) Router pozostaje jedynym miejscem doboru modelu (spójność z Etapem 1).
- (+) Format JSON planu jest prosty do walidacji i rozszerzenia (Etap 3: narzędzia).
- (−) Poleganie na JSON od modelu wymaga dobrego promptu hetmana; parsowanie jest
  odporne, ale słaby model może dawać ubogie plany (mityguje refleksja).

## Alternatywy odrzucone

- **Twarde typowanie routera na `ModelRouter`**: utrudniłoby testy i związałoby
  rdzeń agentów z implementacją routera.
- **Sztywny plan w kodzie**: sprzeczne z „zero hardcode" i architekturą agentową.
- **Function-calling już teraz**: narzędzia i sandbox to Etap 3 — nie wyprzedzamy.
