# ADR-0024 · Sonda głęboka diagnozy — pytanie do modelu jako świadomy opt-in

- **Status:** przyjęty
- **Data:** 2026-08-23
- **Kontekst dla:** `husarz.launcher.doctor`, `husarz doctor --probe`, `GET /api/doctor`

## Problem

Diagnoza instalacji (`husarz doctor`, ADR-0012) sprawdza, czy silnik **wymienia** model
w swoim katalogu. To deklaracja, nie skutek — a projekt ma twardą zasadę, że pomiar sprawdza
skutek. Luka jest realna i została odtworzona na działającej instalacji:

```
[ok] model-bez-v1-u-dostawcy: Model 'husarz' (orkiestracja, tryb czatu) jest dostępny
     pod http://localhost:11434.
```

Ten sam model w czacie zwraca 502. Przyczyna: endpoint nie ma przyrostka `/v1`, więc
`GET /api/tags` odpowiada (i katalog się zgadza), ale `POST /chat/completions` daje 404.
Kontrola katalogu **nie może** tego wykryć, bo pyta o co innego.

Ta sama klasa fałszywego „OK" obejmuje: model wymieniony, ale nie mieszczący się w pamięci;
endpoint OpenAI-compat wystawiający katalog modeli, których nie serwuje; odrzucony klucz API
(katalog bywa otwarty, `/chat/completions` już nie); niedopasowany szablon rozmowy.

Jedynym pomiarem, który to rozstrzyga, jest **zadanie modelowi prawdziwego pytania**. Ma ono
jednak skutki uboczne: wczytuje wagi do pamięci i trwa. Zmierzone na modelu 7B — 18,9 s przy
zimnym starcie, 0,9 s zaraz potem, czyli różnica dwudziestokrotna.

## Decyzja

**1. Sonda głęboka jest osobnym protokołem (`SondaGleboka`), a nie metodą w `Sonda`.**

Opt-in jest wtedy **strukturalny**: bez przekazanego obiektu diagnoza nie ma czym zapytać
modelu. Flaga logiczna dawałaby ten sam efekt tylko dopóty, dopóki nikt jej nie przeoczy.
Drugi powód jest praktyczny: dopisanie metody do `Sonda` unieważniłoby każdą istniejącą
implementację, w tym testowe — a te nie mają powodu umieć pytać modelu.

**2. Sonda używa `build_client` z routera, nie własnego wywołania HTTP.**

Sprawdzamy tę drogę, która realnie zawodzi w czacie: ten sam klient, ten sam pin IP
(ADR-0020), to samo rozwiązywanie `api_key_ref`, ten sam format żądania. Własny, „prostszy"
strzał sprawdzałby drogę, której nikt nie używa — czyli byłby dokładnie tą klasą pomiaru,
przed którą ostrzega ten projekt.

**3. Sonda NIE używa `ModelRouter`.**

Router ma fallbacki. Przy modelu, który nie odpowiada, dostalibyśmy odpowiedź z **innego**
modelu i uznali ją za dowód sprawności tego. Diagnoza musi zapytać dokładnie ten model,
którego dotyczy ustalenie.

**4. Przyczyna niepowodzenia jest zamieniana na KATEGORIĘ, nigdy przepisywana.**

Komunikat transportu jest celowo generyczny („Błąd HTTP przy wywołaniu modelu"), bo trafia
do odpowiedzi API i do audytu — nie może nieść URL-a ani wnętrzności biblioteki. Dla
operatora to jednak za mało: timeout, odrzucony klucz i brak pamięci wymagają trzech różnych
napraw. Sięgamy więc po pierwotną przyczynę przez `__cause__` i mapujemy na jedną z dziewięciu
kategorii z własnym opisem. Degradacja jest łagodna — nierozpoznany łańcuch daje kategorię
„inny", a nie zgadywanie.

**5. Sonda głęboka jest ŚWIADOMIE poza `GET /api/doctor`.**

Wystawienie przez HTTP operacji, która wczytuje wagi i potrafi trwać minuty, byłoby dźwignią
do wyczerpania zasobów — a odpowiedź i tak musiałaby na te wagi czekać. Diagnoza przez API
zostaje przy kontroli katalogu; głęboka jest operacją terminala. Niezmiennik ma test SKUTKU
(`test_endpoint_API_NIE_zadaje_pytania_modelowi`), a nie deklarację w dokumentacji.

## Konsekwencje

**Dobre.** Diagnoza wykrywa klasę usterek, których kontrola katalogu nie mogła zobaczyć,
i podaje przy każdej instrukcję zależną od przyczyny. `husarz up` i konsola WWW pozostają
szybkie, bo nie sondują.

**Kosztowne.** `husarz doctor --probe` trwa. Dla operatora z siedmioma agentami na trzech
różnych modelach to trzy wczytania wag. Komunikat startowy mówi o tym wprost, zanim pierwsze
żądanie poleci.

**Do rozstrzygnięcia później.** Czy dać sondę głęboką konsoli — wymagałoby limitu tempa
i osobnej zgody w konfiguracji, bo dziś nie ma ani jednego, ani drugiego (zapisane w ROADMAP).

## Powiązane

[ADR-0012](0012-pobierany-launcher.md) (pobierany launcher, geneza diagnozy),
[ADR-0020](0020-pinowanie-ip-anty-ssrf.md) (pin IP w kliencie modelu),
[ADR-0003](0003-router-modeli.md) (router i fallbacki).
