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
default_user_role: operator
default_token_quota: null        # null = bez limitu; liczba = limit tokenów per konto
session_ttl_minutes: 720
accounts_path: ./data/accounts.json   # trwałość (null = tylko w pamięci)
seed_admin_username: hetman
seed_admin_password_ref: env:HUSARZ_ADMIN_PASSWORD   # REFERENCJA do sekretu
```

Gdy magazyn jest pusty, a skonfigurowano seed — przy starcie tworzone jest konto
administratora (hasło z sekretu; fail-closed, gdy nierozwiązywalne).

## Uwierzytelnianie API

Gdy konta są aktywne (lub ustawiono `api_token_ref`), wszystkie endpointy poza
`/api/health` wymagają nagłówka `Authorization: Bearer <token>`. Akceptowany jest:

- **token sesji** użytkownika (z `/api/auth/login`), albo
- **statyczny token maszynowy** (`api_token_ref` — admin/CI).

## Endpointy kont

| Metoda i ścieżka | Opis | Auth |
|------------------|------|------|
| `POST /api/auth/register` | `{username, password}` → konto + token sesji (gdy `allow_registration`) | publiczny |
| `POST /api/auth/login` | `{username, password}` → token sesji | publiczny |
| `POST /api/auth/logout` | unieważnia sesję (nagłówek Bearer) | Bearer |
| `GET /api/auth/me` | bieżący użytkownik: rola, **aktywny model czatu**, `tokens_used`, `token_quota`, `tokens_remaining` | Bearer |

Limit tokenów: gdy konto ma `token_quota` i je wyczerpie, `POST /api/chat` i
`/api/orchestrate` zwracają **HTTP 402**. Zużycie doliczane jest z pola `usage`
odpowiedzi modelu (dla czatu; orkiestracja — po zsumowaniu zużycia w kolejnym kroku).

## Bezpieczeństwo

- Hasła: `scrypt` (memory-hard), losowa sól per hasło, porównanie w stałym czasie;
  nigdy nie są przechowywane ani logowane w postaci jawnej.
- Enumeracja użytkowników utrudniona: weryfikacja hasha także przy braku konta.
- Sesje: nieprzewidywalny token (`secrets.token_urlsafe`), TTL, unieważnianie.
- Seed-admin: hasło wyłącznie z referencji do sekretu (zero hardcode).

Decyzje: [ADR-0009](adr/0009-konta-tokeny.md). Model bezpieczeństwa: [BEZPIECZENSTWO.md](BEZPIECZENSTWO.md).
