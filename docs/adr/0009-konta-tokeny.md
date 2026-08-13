# ADR-0009: Konta użytkowników, sesje i limity tokenów

- Status: przyjęty
- Data: 2026-08-13
- Etap: 7

## Kontekst

Produkt ma dwa tryby: suwerenny (lokalny, bez limitów) i hostowany (dostęp „dla
wybranych" → subskrypcja, z limitami tokenów). Potrzebne są konta (logowanie/
rejestracja), sesje, RBAC per użytkownik oraz licznik i limity tokenów — wszystko
zgodne z zasadami: suwerenność, zero cudzych API, testowalność bez sieci/DB, sekrety
poza repo.

## Decyzja

### Hashowanie haseł: `scrypt` z biblioteki standardowej

Świadomie BEZ zależności (argon2/bcrypt): `hashlib.scrypt` jest memory-hard i
wbudowany. Format `scrypt$n$r$p$salt$hash`, losowa sól per hasło, weryfikacja w
stałym czasie (`hmac.compare_digest`). Mniej zależności = mniejsza powierzchnia i
łatwiejszy airgap.

### Magazyn kont wstrzykiwalny

`AccountStore` (Protocol) z implementacjami `InMemory` (testy/dev) i `File` (JSON,
konfinowana ścieżka). Produkcyjnie można podstawić Postgres bez zmian w rdzeniu —
ten sam wzorzec co audyt/sekrety/sandbox.

### `AccountService` spina logikę

Rejestracja (gated `allow_registration`), logowanie z sesją, `resolve_session`
(z TTL), `logout`, `check_quota`/`record_usage`. Zegar wstrzykiwalny → testy
deterministyczne (wygasanie sesji). Sesje w pamięci procesu (MVP; wielo-proces →
backend współdzielony, np. Redis).

### Uwierzytelnianie API: sesja LUB token maszynowy

`create_app` przyjmuje wstrzykiwaną usługę kont. Nagłówek Bearer jest rozwiązywany
kolejno jako: statyczny token maszynowy (admin/CI) albo token sesji użytkownika →
`Principal(role, user_id, username)`. RBAC działa na `principal.role`. Endpointy
`register`/`login` są publiczne (inaczej nie dałoby się zalogować); `me`/`logout`
wymagają Bearer. Rozliczanie tokenów i limit (HTTP 402) w `/api/chat` (zużycie z
pola `usage`); w `/api/orchestrate` egzekwowany jest limit (sumowanie zużycia —
kolejny krok).

### Seed administratora z sekretu

Przy pustym magazynie i skonfigurowanym `seed_admin_username` +
`seed_admin_password_ref` tworzone jest konto admina; hasło WYŁĄCZNIE z referencji
do sekretu (fail-closed, gdy nierozwiązywalne). Zero hardcode.

## Konsekwencje

- (+) Fundament pod „ile tokenów zostało", płatne API i subskrypcje.
- (+) Bez zależności zewnętrznych do haseł; w pełni testowalne bez sieci/DB.
- (+) Spójne z istniejącym Bearer + RBAC (sesja to po prostu token Bearer).
- (−) Sesje w pamięci procesu — restart = ponowne logowanie; skalowanie poziome
  wymaga wspólnego magazynu sesji (Redis) — odłożone.
- (−) Rozliczanie tokenów orkiestracji wymaga przewleczenia `usage` przez
  orkiestrator (obecnie liczony jest tylko czat) — odłożone.
- (−) Pełny OIDC nadal odłożony; token sesji + RBAC to pomost.

## Alternatywy odrzucone

- **argon2/bcrypt**: dodatkowa zależność natywna; `scrypt` stdlib wystarcza i lepiej
  pasuje do suwerenności/airgap.
- **JWT bez stanu**: unieważnianie (logout) wymaga listy odwołań i tak → wybrano
  proste, unieważnialne tokeny sesji.
- **Rejestracja domyślnie otwarta**: sprzeczne z „dostęp dla wybranych" — domyślnie
  wyłączona.
