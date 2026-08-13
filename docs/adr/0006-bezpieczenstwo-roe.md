# ADR-0006: Bezpieczeństwo runtime — audit log, ROE-gate, Puszkarz, RBAC

- Status: przyjęty
- Data: 2026-08-13
- Etap: 4

## Kontekst

Etap 4 wprowadza egzekwowanie bezpieczeństwa w czasie działania: twardą bramkę
ROE dla agenta Puszkarz, niemodyfikowalny audit log, autoryzację (RBAC) i realnych
dostawców sekretów. Środowisko dev nie ma Vault/OIDC/mTLS-infra ani Dockera —
logika musi być testowalna bez nich.

## Decyzja

### Audit log z łańcuchem skrótów (tamper-evidence)

`AuditLog` dopisuje wpisy, każdy ze skrótem `sha256(prev_hash + payload)`. Zmiana
dowolnego wpisu unieważnia kolejne skróty — `verify()` wykrywa manipulację. Zapis
jest append-only; zegar wstrzykiwalny (testy deterministyczne). Wszystkie decyzje
ROE-gate i odmowy Puszkarza trafiają tu z odniesieniem do ROE.

### ROE-gate jako jedyna droga akcji Puszkarza

`RoeGate.evaluate` sprawdza: aktywność ROE (zgoda + niepusty podpis), okno czasowe
(`is_active_at`), przynależność celu do zakresu (CIDR/domeny, z `out_of_scope`),
techniki (allow/forbid) i tryb. **Domyślnie dry-run**; akcja aktywna wymaga
`authorized=True` (odpowiednik flagi `--authorized`). Cel spoza zakresu, poza oknem
lub bez aktywnego ROE = twardy blok — nawet z `authorized`.

### Puszkarz: defensywny z zasady

`Puszkarz.review_request` odrzuca żądania wytworzenia narzędzi ofensywnych
(malware/exploit/omijanie zabezpieczeń) i proponuje działanie defensywne
(audyt/hardening/detekcja). Akcje na celach idą przez ROE-gate. To egzekwuje
regułę „integruje narzędzia, NIE generuje exploitów" w kodzie, nie tylko w prompcie.

### RBAC jako czysta logika autoryzacji

`Rbac` mapuje rolę na uprawnienia `obszar:akcja` (z wildcardami `*` i `obszar:*`).
Uwierzytelnienie i przypisanie ról (OIDC/mTLS) wiąże się w Etapie 5 (API); tu jest
sama, testowalna warstwa autoryzacji.

### Dostawcy sekretów z wstrzykiwalnym backendem

`File`/`Sops`/`Vault` providerzy rozwiązują referencje (`file:`/`sops:`/`vault:`).
Dekryptor SOPS i odczyt Vault są wstrzykiwalne — logika testowalna bez `sops`/Vault;
domyślne implementacje wymagają tych narzędzi. Sekrety pozostają referencjami w configu.

## Konsekwencje

- (+) Twarde niezmienniki (dry-run, blok spoza ROE, tamper-evidence, odmowa ofensywy)
  egzekwowane i przetestowane bez zewnętrznej infrastruktury.
- (+) Audyt jest weryfikowalny kryptograficznie (łańcuch skrótów).
- (−) Uwierzytelnienie (OIDC), mTLS oraz runtime egress/sandbox enforcement to
  Etap 5/6 — tu dostarczamy warstwę decyzyjną, nie sieciową.

## Alternatywy odrzucone

- **Wymuszanie ROE tylko w prompcie Puszkarza**: prompt można obejść; bramka w
  kodzie (`RoeGate`) jest twarda i audytowalna.
- **Audyt bez łańcucha skrótów**: zwykły log daje się zmodyfikować bez śladu.
- **Twarde zależności Vault/SOPS**: uniemożliwiłyby testy i dev bez tych narzędzi.
