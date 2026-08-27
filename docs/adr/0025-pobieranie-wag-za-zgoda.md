# ADR-0025 · Pobieranie wag modeli — za zgodą, z rozmiarem podanym wcześniej

- **Status:** przyjęty
- **Data:** 2026-08-24
- **Kontekst dla:** `husarz.launcher.bootstrap`, `husarz bootstrap`, `config/bootstrap.yaml`

## Problem

Diagnoza ([ADR-0024](0024-sonda-gleboka-diagnozy.md)) kończy się ustaleniem w rodzaju
„Silnik odpowiada, ale NIE MA modelu 'bielik-11b-v3.0-instruct'". Operator wie już, co jest
nie tak — i musi to naprawić poleceniem spoza Husarza, z dokumentacji, na własną rękę.
Pętla „powiedz, co jest źle → napraw to" jest przerwana dokładnie w połowie.

Domknięcie jej znaczy jednak, że **Husarz z własnej inicjatywy sięga do sieci po treść** —
pierwszy raz w historii projektu, którego pierwszą zasadą jest suwerenność danych i
deny-all egress. Trzeba więc rozstrzygnąć nie „czy da się pobrać", tylko „na jakich warunkach
wolno".

## Decyzja

**1. Pobieramy WAGI, nie SILNIK.**

Wagi ściąga silnik operatora (`POST /api/pull` do Ollamy); Husarz jedynie o to prosi.
Odrzucone: pobieranie i instalowanie binarki silnika. Byłoby to ściąganie i uruchamianie
cudzego kodu wykonywalnego — z weryfikacją sum kontrolnych i podpisów, obsługą trzech
systemów, ścieżek instalacyjnych i czasem uprawnień administratora. To osobny obszar ryzyka,
a instalacja silnika jest właściwie zadaniem menedżera pakietów. Diagnoza i tak podaje
`ollama serve` z odnośnikiem do instrukcji.

**2. Rozmiar PRZED pobraniem, z manifestu — nie ze strumienia.**

„Ekran zgody podający liczbę GB" byłby fikcją, gdyby liczbę poznawać dopiero ze strumienia
pobierania: bajty już by leciały, a zgoda dotyczyłaby czegoś, co się zaczęło. Czytamy więc
manifest rejestru. Zmierzone: **857 bajtów** metadanych dla `qwen2.5-coder:1.5b`, z których
wynika dokładny rozmiar **0,986 GB** — zgodny z „≈1 GB" w dokumentacji Ollamy.

Konsekwencja, którą przyjmujemy świadomie: model, którego rozmiaru NIE DA SIĘ ustalić, jest
pokazywany operatorowi wraz z powodem, ale **nie jest pobierany**. Zgoda bez znajomości
rozmiaru nie byłaby zgodą.

**3. Dwie allowlisty, nie jedna.**

Zapytanie o manifest przechodzi przez `bootstrap.sources`, a NIE przez
`security.egress.allowlist`. Gdyby wystarczała ta druga, każda domena otwarta dla narzędzia
`web` stawałaby się źródłem, z którego Husarz gotów jest pobierać wagi — a to zupełnie inna
decyzja operatora. Zależność nie działa też w drugą stronę: `check_endpoint_allowed` (router,
`web`, wtyczki) czyta wyłącznie `security.egress`, więc wpis w `bootstrap.sources` nie
rozszczelnia deny-all.

Pobieranie idzie do silnika operatora, więc **ten** ruch podlega zwykłej bramce egress.
Zapytanie o manifest dodatkowo przechodzi pin IP (ADR-0020) z `allow_loopback=False`
i `allow_lan=False`: rejestr modeli jest w WAN, więc nazwa nie ma prawa rozwiązać się na
adres wewnętrzny ani na metadane chmury.

**4. Domyślnie wyłączone, a zgoda jest jawna.**

`bootstrap.enabled: false` w dostarczonej konfiguracji. Pytanie ma domyślną odpowiedź
**odmowną** — Enter naciśnięty odruchowo nie może uruchomić transferu gigabajtów. Brak
terminala (potok, usługa systemowa) również znaczy „nie": tam nie ma komu wyrazić zgody.
Flaga `--yes` istnieje dla skryptów i jej użycie **jest** zgodą; rozmiar i tak zostaje
wypisany, a pozycje o nieznanym rozmiarze nadal nie są pobierane.

**5. Profil airgap: twarda odmowa, sprawdzana PRZED włącznikiem.**

Kolejność kontroli jest częścią komunikatu. Operator w trybie airgap ma usłyszeć, że
zabrania **profil** — a nie że „wystarczy włączyć bootstrap", bo to sugerowałoby, że
politykę da się obejść ustawieniem.

## Konsekwencje

**Dobre.** Pętla diagnostyczna jest domknięta: `husarz doctor` mówi, czego brakuje,
`husarz bootstrap` proponuje to pobrać, `husarz doctor --probe` potwierdza skutek.
Ustalanie braków ma **jedno źródło prawdy** (`brakujace_modele` w module diagnozy), więc
bootstrap nie może zaproponować pobrania modelu, o którym diagnoza mówi „jest".

**Kosztowne.** Trzy pliki konfiguracji zamiast dwóch i jeszcze jedna allowlista do
zrozumienia. Uznajemy to za cenę rozdzielenia dwóch różnych decyzji operatora.

**Ograniczenia — wprost.** Modele powstające lokalnie z Modelfile (jak `husarz`) nie są
pobieralne: rejestr ich nie zna. Komenda mówi to wprost i odsyła do `ollama create`.
Nie weryfikujemy sum kontrolnych wag — robi to silnik przy pobieraniu (digesty warstw
w manifeście), a dublowanie tego wymagałoby pobierania wag przez Husarza, czego decyzja 1
zabrania.

## Powiązane

[ADR-0024](0024-sonda-gleboka-diagnozy.md) (diagnoza jako źródło braków),
[ADR-0020](0020-pinowanie-ip-anty-ssrf.md) (pin IP dla dróg wychodzących),
[ADR-0008](0008-deploy-profile-airgap.md) (profil airgap).
