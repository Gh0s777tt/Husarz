# Konta, sesje i limity tokenów (Etap 7)

Warstwa kont dodaje **logowanie/rejestrację**, **sesje** (token jako Bearer),
**RBAC per użytkownik** oraz **licznik i limity tokenów**. Kod: `husarz.accounts`.
Wszystko suwerenne i testowalne bez sieci/DB: hashowanie `scrypt` (biblioteka
standardowa), magazyn wstrzykiwalny (pamięć / plik JSON / Postgres w przyszłości),
sesje w pamięci procesu.

## Dwa tryby

- **Suwerenny (lokalny)** — `token_quota=null` → brak limitu; licznik pokazuje tylko
  *zużycie*. Konta opcjonalne (dev bez logowania działa jak dotąd na loopbacku).
- **Hostowany (dla wybranych → subskrypcja)** — konta + limity per użytkownik +
  rejestracja gated. Fundament pod płatne API.

## Włączenie

Konta są aktywne, gdy w `security.auth` ustawisz choć jedno: `accounts_path`,
`seed_admin_username` lub `allow_registration`.

```yaml
# config/security.yaml → auth:
allow_registration: false        # false = konta zakłada admin („dla wybranych")
default_user_role: user          # najmniejsze uprawnienia (czat); admin podnosi rolę
default_token_quota: null        # null = bez limitu; liczba = limit tokenów per konto
session_ttl_minutes: 720
login_max_attempts: 5            # blokada konta po tylu nieudanych logowaniach…
login_lockout_minutes: 15        # …na tyle minut (anty-brute-force)
accounts_path: ./data/accounts.json   # trwałość (null = tylko w pamięci)
seed_admin_username: hetman
seed_admin_password_ref: env:HUSARZ_ADMIN_PASSWORD   # REFERENCJA (oba pola seed razem)
```

Konta zakładane administracyjnie (gdy rejestracja wyłączona): `husarz useradd`
(patrz niżej). Rola `user` może rozmawiać/orkiestrować, ale NIE ma `tool:*`,
`roe:authorize` ani `audit:read` — podniesienie do `operator`/`admin` to decyzja admina.

Gdy magazyn jest pusty, a skonfigurowano seed — przy starcie tworzone jest konto
administratora (hasło z sekretu; fail-closed, gdy nierozwiązywalne).

## Uwierzytelnianie API

Gdy konta są aktywne (lub ustawiono `api_token_ref`), endpointy `/api` wymagają
nagłówka `Authorization: Bearer <token>` — **poza** publicznymi: `/api/health`
(liveness), `/api/auth/register`, `/api/auth/login` oraz `/` (konsola). Akceptowany jest:

- **token sesji** użytkownika (z `/api/auth/login`), albo
- **statyczny token maszynowy** (`api_token_ref` — admin/CI).

## Endpointy kont

| Metoda i ścieżka | Opis | Auth |
|------------------|------|------|
| `POST /api/auth/register` | `{username, password}` → konto + token sesji (gdy `allow_registration`) | publiczny |
| `POST /api/auth/login` | `{username, password}` → token sesji | publiczny |
| `POST /api/auth/logout` | unieważnia sesję (idempotentny; czyta token z nagłówka) | Bearer (opc.) |
| `GET /api/auth/me` | bieżący użytkownik: rola, **aktywny model czatu**, `tokens_used`, `token_quota`, `tokens_remaining` | Bearer |

Limit tokenów: gdy konto ma `token_quota` i je wyczerpie, `POST /api/chat` i
`/api/orchestrate` zwracają **HTTP 402**. Zużycie doliczane jest z pola `usage`
odpowiedzi modelu — na OBU ścieżkach:

- **czat** — zużycie z pojedynczej odpowiedzi,
- **orkiestracja** — SUMA ze wszystkich wywołań modelu w jednym żądaniu: plan, każda
  delegacja (a przy pętli narzędziowej — każda jej iteracja), refleksja i synteza.
  Sumowaniem zajmuje się `UsageMeter`, tworzony świeżo na każde żądanie.

> **Uwaga (naprawione w Etapie 7b).** Wcześniej orkiestracja sprawdzała limit, ale go nie
> naliczała, więc konto z kwotą mogło korzystać z niej bez ograniczeń — i to na najdroższym
> endpoincie. Jeśli polegasz na limitach, ta poprawka realnie zmienia zachowanie: zużycie
> orkiestracji zaczyna być liczone.

Limit jest z natury **miękki**: rozliczenie następuje PO odpowiedzi, więc pojedyncze żądanie
może przekroczyć próg — blokowane są dopiero kolejne. Backend, który nie raportuje `usage`,
nie powoduje naliczania zmyślonych wartości (brak danych ≠ zero).

## Rozliczalność w audycie

Każdy wpis dziennika niesie DWIE informacje: `actor` — kto wykonał (agent albo `api`) —
oraz `principal` — na czyje żądanie (`user:<id_konta>` albo `token:<rola>`). Bez tej drugiej
przy wielu kontach nie dałoby się odpowiedzieć, kto zlecił konkretne wywołanie narzędzia.
Pole jest objęte łańcuchem skrótów, a do logu trafia **identyfikator konta, nie nazwa
użytkownika** (niemodyfikowalny dziennik nie powinien zawierać PII). Szczegóły:
[BEZPIECZENSTWO.md](BEZPIECZENSTWO.md) (sekcja „Etap 13c").

## Bezpieczeństwo

- Hasła: `scrypt` (memory-hard, n=2¹⁶), losowa sól per hasło, porównanie w stałym
  czasie; nigdy nie są przechowywane ani logowane w postaci jawnej.
- Enumeracja użytkowników utrudniona: weryfikacja hasha także przy braku konta.
- **Anty-brute-force**: blokada konta po `login_max_attempts` nieudanych próbach na
  `login_lockout_minutes` (HTTP 429); nieudane logowania i blokady są audytowane.
- Sesje: nieprzewidywalny token (`secrets.token_urlsafe`), TTL, unieważnianie,
  sprzątanie wygasłych przy logowaniu + limit sesji na użytkownika.
- **Najmniejsze uprawnienia**: nowe konta dostają rolę `user` (czat), nie `operator`.
- Trwały magazyn kont: zapis **atomowy** (temp + `os.replace`) pod zamkiem — brak
  ryzyka uszkodzenia pliku poświadczeń przy współbieżności.
- Seed-admin: hasło wyłącznie z referencji do sekretu (zero hardcode).

Decyzje: [ADR-0009](adr/0009-konta-tokeny.md). Model bezpieczeństwa: [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).
