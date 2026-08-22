# ADR-0023 · Zapisywalny magazyn sekretów i kreator połączeń

- **Status:** przyjęty
- **Data:** 2026-08-22
- **Kontekst dla:** `husarz.security.secret_store`, `husarz.core.crypto`, `POST /api/git/connections/wizard`

## Problem

Dodanie połączenia z GitHubem albo GitLabem wymagało od operatora trzech kroków wykonanych
poza Husarzem: wygenerowania tokenu u dostawcy, umieszczenia go w zmiennej środowiskowej lub
w Vaulcie, i dopiero potem wpisania w konsoli *referencji* (`env:GITHUB_TOKEN`). Krok środkowy
jest tym, który ludzie robią źle albo wcale — token ląduje w pliku `.env` obok repozytorium,
w historii powłoki, czasem w samym pliku konfiguracji.

Źródło problemu jest strukturalne: **wszyscy dotychczasowi dostawcy sekretów są wyłącznie do
odczytu**. `EnvSecretsProvider`, `FileSecretsProvider`, `VaultSecretsProvider` i
`SopsSecretsProvider` potrafią rozwiązać referencję, ale żaden nie potrafi przyjąć materiału.
Husarz nie miał więc gdzie zapisać sekretu, który **dostaje** — wklejonego w konsoli, a w
przyszłości zwróconego przez OAuth.

Zasada „config nie zawiera materiału" jest przy tym nienaruszalna: konfiguracja i magazyn
połączeń są plikami, które operator kopiuje, wersjonuje i wysyła w zgłoszeniach błędów.

## Rozważane opcje

**1. Token w pliku konfiguracji.** Odrzucone bez dyskusji — łamie niezmiennik, który jest
powodem istnienia całej hierarchii referencji.

**2. Token tylko w pamięci procesu.** Nie wymaga niczego nowego, ale ginie przy restarcie.
Operator musiałby wklejać token po każdym `husarz up`, co w praktyce skończyłoby się
obejściem: wpisaniem go na stałe gdzieś indziej. Rozwiązanie, które ludzie obchodzą, nie jest
rozwiązaniem.

**3. Keychain systemu operacyjnego** (Keychain / DPAPI / Secret Service). Najmocniejsze
technicznie: klucz chroniony przez system, często sprzętowo. Odrzucone, bo wiąże się
z platformą i **wypada dokładnie tam, gdzie Husarz ma działać** — w kontenerze i na serwerze
w trybie airgap bez sesji graficznej. Wsparcie trzech backendów plus ścieżka awaryjna to
więcej kodu wrażliwego niż samo szyfrowanie pliku.

**4. Szyfrowany plik + klucz główny z istniejącego dostawcy** — wybrane.

## Decyzja

Wprowadzamy `husarz.security.secret_store.EncryptedFileSecretStore`: plik JSON z wpisami
zaszyfrowanymi AES-256-GCM, prawa `0600` w katalogu `0700`, zapis atomowy. Magazyn implementuje
protokół `SecretsProvider`, więc **wpina się w istniejący łańcuch jako kolejne źródło**, a nie
obok niego. Obsługuje nowy schemat referencji `husarz:<nazwa>`.

Klucz główny pochodzi z referencji rozwiązywanej przez dostawcę **zewnętrznego**
(`env:`/`file:`/`vault:`/`sops:`). Schemat `husarz:` jest dla `secret_store.key_ref` zabroniony
walidacją — magazyn odblokowywany własnym sekretem byłby zamkniętym kręgiem.

Kreator (`POST /api/git/connections/wizard`) przyjmuje token, zapisuje go w magazynie i tworzy
połączenie z **wygenerowaną referencją** `husarz:git/<nazwa>`. Magazyn połączeń dostaje to samo,
co dostawał wcześniej: referencję. Niezmiennik zostaje nienaruszony.

Magazyn jest domyślnie **wyłączony**. Instalacja, która go nie potrzebuje, nie ma powierzchni
zapisu sekretów.

## Konsekwencje

**Pozytywne.** Operator dodaje połączenie w jednym kroku, w konsoli, bez dotykania powłoki.
Sekret przeżywa restart. Wszystkie dotychczasowe drogi (`env:`, `vault:`, SOPS) działają bez
zmian i pozostają zalecane tam, gdzie operator już nimi zarządza. Prymityw kryptograficzny
wylądował w `husarz.core.crypto` (warstwa 0), więc pamięć długoterminowa i magazyn sekretów
korzystają z jednego, wspólnie testowanego kodu, zamiast z dwóch kopii.

**Negatywne i granice — świadomie przyjęte.**

Bezpieczeństwo magazynu **równa się bezpieczeństwu klucza głównego**. Klucz w Vaulcie daje
realną separację; klucz w zmiennej środowiskowej obok pliku magazynu chroni głównie kopie
zapasowe i wyniesiony dysk, a nie napastnika, który już jest na maszynie z uprawnieniami
operatora. To nie jest ukryta słabość — to model, który trzeba znać, wybierając miejsce na
klucz. Sekret jest z definicji odszyfrowywalny przez sam proces Husarza, więc żadna
konstrukcja nie obroni go przed kodem działającym na tym koncie.

Klucz jest wyprowadzany z materiału przez SHA-256, **nie przez KDF z solą i rozciąganiem**.
Jest to poprawne dopóty, dopóki materiał pochodzi z dostawcy sekretów, czyli jest losowym
kluczem, a nie hasłem wymyślonym przez człowieka. Gdyby kiedykolwiek dopuścić hasło operatora
jako źródło klucza, `derive_key` **musi** zostać zastąpione przez scrypt albo Argon2 —
inaczej atak słownikowy na plik magazynu stanie się opłacalny.

Dochodzi zależność `cryptography`, ale **nie do rdzenia**: pozostaje w extra `husarz[memory]`,
a jej brak daje czytelny komunikat po polsku zamiast błędu importu w losowym miejscu. Rdzeń
nadal ma pięć zależności runtime.

W procesie musi istnieć **dokładnie jedna** instancja magazynu. Dwie wskazujące ten sam plik
rozjechałyby się przy pierwszym zapisie: każda trzyma wczytane wpisy w pamięci, więc zapis
drugiej skasowałby wpis pierwszej. Dlatego launcher trzyma go w zmiennej modułowej korzenia
kompozycji, a nie buduje na żądanie.

## Czego ta decyzja NIE obejmuje

Device flow OAuth (logowanie „kliknij, żeby połączyć" bez ręcznego generowania tokenu) jest
osobnym zagadnieniem i osobnym ADR-em. Ten magazyn jest jego **warunkiem koniecznym** — token
zwrócony przez OAuth musi mieć gdzie wylądować — ale sam z siebie go nie wprowadza. Kreator
nadal wymaga tokenu wygenerowanego u dostawcy.

Nie obejmuje też rotacji ani wygasania wpisów: ponowny zapis pod tą samą nazwą zastępuje
wartość, ale nic nie przypomina operatorowi, że token wygasa.
