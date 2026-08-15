# ADR-0021: Kryptograficzny podpis ROE — autoryzacja, której nie da się dopisać

- Status: przyjęty
- Data: 2026-08-15
- Etap: 4b (wpięcie w runtime: 4c)
- Domyka ograniczenie z: [ADR-0006](0006-bezpieczenstwo-roe.md) (ROE-gate i Puszkarz)

## Kontekst

ROE (*Rules of Engagement*) to **jedyny** artefakt, który uprawnia Puszkarza do aktywnych
działań wobec konkretnych celów: zakres (CIDR/domeny), okno czasowe, dozwolone techniki.
Bramka `RoeGate` egzekwuje go rygorystycznie — ale ważność samego dokumentu sprowadzała się
do jednej linijki:

```python
return self.consent and bool(self.signature and self.signature.strip())
```

Czyli: **dowolny niepusty tekst w polu `signature` czynił zlecenie ważnym.** Kto mógł
edytować `config/roe/*.yaml`, mógł też dopisać `signature: "abc"` i poszerzyć zakres —
a wtedy Husarz skanowałby cele, na które nikt nie wyraził zgody. Konsekwencją nie jest
kompromitacja Husarza, tylko **atak na osobę trzecią z użyciem Husarza jako narzędzia**;
prawnie to różnica między testem penetracyjnym a przestępstwem.

Ryzyko było w chwili podejmowania tej decyzji **utajone**, nie żywe: orkiestrator twardo
pomijał agentów z `roe_required` (`SKIPPED_ROE`), więc bramka nie była jeszcze ścieżką
runtime. To właśnie dlatego prymityw autoryzacji domknęliśmy WTEDY — zanim bramka została
wpięta.

> **Aktualizacja (Etap 4c).** Bramka jest już wpięta: `husarz.security.roe_runtime.RoeRuntime`
> decyduje o delegacji agentów `roe_required` w orkiestratorze, więc opisany tu podpis jest
> teraz **nośny** — podrobiony blokuje delegację. Szczegóły w `docs/BEZPIECZENSTWO.md`
> (sekcja „Etap 4c").

## Decyzja

### 1. Podpisujemy TREŚĆ, nie plik

Payload to kanoniczna postać treści autoryzacyjnej: `engagement_id`, `owner`,
`authorized_by`, `scope`, `window` (znormalizowane do UTC), listy technik, `consent`
i `dry_run_default`. Samo pole `signature` jest wyłączone (inaczej nie dałoby się go policzyć).

Podpis bajtów pliku byłby **niewystarczający**, i to nie z wygody: Husarz scala konfigurację
z warstw `plik → ENV → nadpisania runtime (panel)`. Zakres poszerzony przez
`POST /api/config/runtime` nie zmienia ani jednego bajtu na dysku. Payload liczony z
**efektywnego** `RoeConfig` wykrywa również tę drogę.

Kanoniczność: klucze posortowane, separatory bez spacji, UTF-8. Kolejność elementów **list
jest zachowana** — lista celów to dane, nie zbiór, więc jej przestawienie świadomie
unieważnia podpis (operator podpisuje to, co widzi).

### 2. Separacja domen

Payload jest prefiksowany `husarz-roe-v1\n`. Ten sam klucz użyty w innym kontekście nie
da się przenieść do ROE, a zmiana formatu payloadu wymusza podniesienie wersji — stare
podpisy przestają wtedy pasować, co jest zachowaniem **pożądanym** (fail-closed przy
zmianie semantyki, a nie cicha akceptacja).

### 3. Dwa algorytmy, jedna bramka

| Algorytm | Zależności | Model zaufania |
|---|---|---|
| `hmac-sha256` | wyłącznie stdlib | symetryczny — klucz MUSI być na maszynie; chroni przed edycją pliku przez kogoś **bez** dostępu do sekretu |
| `ed25519` | `cryptography` (extra `husarz[roe]`) | asymetryczny — klucz **prywatny zostaje u zatwierdzającego**, Husarz trzyma tylko publiczny |

Domyślny jest `ed25519`: dla realnego zlecenia podpis ma pochodzić od osoby, która
autoryzowała zakres, a nie od maszyny, która go wykonuje. HMAC zostaje jako wariant bez
dodatkowych zależności (np. dla wdrożeń, które i tak trzymają sekret w Vaultcie).

**Downgrade-guard:** algorytm zadeklarowany w pliku (`<alg>:<base64>`) musi zgadzać się
z algorytmem z konfiguracji. Bez tego plik mógłby „zgadzać się sam ze sobą", wskazując
słabszy wariant.

### 4. Fail-closed w każdym rozgałęzieniu

| Sytuacja | Reakcja |
|---|---|
| brak podpisu / pusty / zły format / zły base64 | odmowa (`False`) |
| algorytm inny niż skonfigurowany | odmowa |
| zły klucz, zmieniona treść | odmowa |
| `verify_signature=true` bez `key_ref` | **błąd startu** (nie wstajemy) |
| `key_ref` nierozwiązywalny w runtime | `RoeSignatureError` — nigdy „przepuść" |

Rozdzielenie „nieważny dokument" (odmowa) od „zepsuta konfiguracja" (wyjątek) jest celowe:
pierwsze to normalna decyzja bramki, drugie ma być głośno widoczne dla operatora.
Porównanie HMAC jest stałoczasowe (`hmac.compare_digest`).

### 5. Domyślnie włączone, wymagane w `prod`/`airgap` — ale tylko przy aktywnym zleceniu

`verify_signature` domyślnie `true`. Walidacja krzyżowa wymaga go (wraz z `key_ref`)
w profilach `prod`/`airgap`, ale **tylko gdy istnieje zlecenie z `consent: true`**.
Szablon bez zgody jest nieszkodliwy, więc wdrożenia, które nie prowadzą testów, nie płacą
za tę bramkę żadnej ceny. Ten warunek działa też na scalonym configu, więc próba
podniesienia `consent` nadpisaniem runtime w profilu `prod` bez klucza jest odrzucana.

### 6. Narzędzie operatora jest częścią funkcji, nie dodatkiem

`husarz roe sign --engagement <id> [--private-key-file klucz.pem]` wypisuje gotową linię
do wklejenia, a `husarz roe verify --engagement <id>` diagnozuje istniejący podpis
(kod wyjścia: 0 = ważny, 2 = odrzucony). Bez tego włączenie weryfikacji byłoby wyłącznie
sposobem na unieruchomienie ROE — nie dałoby się wytworzyć poprawnego podpisu.

Klucz prywatny Ed25519 podaje operator plikiem; **runtime Husarza nigdy go nie widzi**.

## Konsekwencje

- (+) „Autoryzacja" przestaje być polem tekstowym. Poszerzenie zakresu, wydłużenie okna,
  usunięcie `out_of_scope`, dopisanie techniki i podniesienie `consent` — każde unieważnia
  podpis (pokryte testami parametrycznymi).
- (+) Wykrywana jest również eskalacja przez **nadpisania runtime**, nie tylko edycję pliku.
- (+) `ed25519` pozwala trzymać klucz podpisujący poza infrastrukturą wykonującą testy —
  rozdzielenie ról „kto autoryzuje" i „kto wykonuje".
- (−) **Zmiana zachowania**: dotychczasowe „podpisy" (dowolny tekst) przestają być ważne.
  Dotyczy każdego, kto ma `consent: true` — trzeba wygenerować podpis (`husarz roe sign`).
  Świadome: to nie regresja, tylko koniec akceptowania czegoś, co nigdy nie było podpisem.
- (−) `ed25519` wymaga extry `husarz[roe]` (`cryptography`). Wariant HMAC działa na samej
  stdlib, więc instalacja bazowa nie traci funkcjonalności.
- (−) Rotacja klucza unieważnia wszystkie podpisy (trzeba je odnowić). Świadome — to
  właściwość podpisów, nie usterka.

## Rozważone alternatywy

**Hash pliku (`sha256` bajtów YAML) w polu `signature`.** Odrzucone: nie jest podpisem
(każdy może przeliczyć hash po edycji) i nie wykrywa nadpisań runtime.

**Detached signature obok pliku (`zlecenie.yaml.sig`).** Odrzucone: dwa artefakty do
zsynchronizowania, a przy scalaniu warstw i tak trzeba by podpisywać treść efektywną.
Jedno pole w dokumencie jest prostsze i trudniejsze do rozjechania.

**GPG/`sops`.** Nie odrzucone jako idea — `key_ref` przyjmuje schemat `sops:`/`vault:`,
więc materiał klucza może pochodzić stamtąd. Odrzucone jako *mechanizm podpisu*: wciągałoby
zewnętrzny binarny toolchain w ścieżkę decyzyjną bramki bezpieczeństwa.

**Weryfikacja w `RoeConfig.is_active` (schemat).** Odrzucone: schemat nie ma (i nie powinien
mieć) dostępu do dostawcy sekretów, a walidacja configu nie jest miejscem na operacje
kryptograficzne. Bramka pozostaje jedynym punktem decyzji.

## Do zrobienia (świadomie poza zakresem)

- ~~Wpięcie `RoeGate` w runtime~~ — **zrealizowane** (Etap 4c): `RoeRuntime` bramkuje
  delegację agentów `roe_required` w orkiestratorze, więc podpis jest teraz nośny.
  Autoryzacja NA CEL (`RoeGate.evaluate`) czeka na nadanie Puszkarzowi zdolności wykonawczych.
- Klucze prywatne chronione hasłem (`load_pem_private_key(password=...)`).
- Rotacja i wersjonowanie kluczy (dziś: jeden `key_ref`).
