# Integracje Git (GitHub/GitLab) i tworzenie PR (Etap 9)

Husarz łączy się z **własnymi** repozytoriami użytkownika (GitHub/GitLab), listuje je
i tworzy **Pull Request / Merge Request**. Kod: `husarz.git`. To integracja *kodu*,
nie modeli — spójna z zasadą „nie używamy cudzych API modeli".

## Zasady bezpieczeństwa

- **Token jako referencja do sekretu** — połączenie przechowuje `token_ref`
  (`env:GITHUB_TOKEN`, `file:gh`, `vault:…`), NIGDY samej wartości. Token rozwiązywany
  jest dopiero przy operacji, przez dostawcę sekretów.
- **Bramka egress (deny-all)** — host dostawcy (`api.github.com`, `gitlab.com`) musi
  być na `security.egress.allowlist`, inaczej połączenie jest blokowane (`EgressError`
  → HTTP 403). Suwerenność: bez jawnej zgody nie łączymy się z WAN.
- **Transport HTTP wstrzykiwalny** — testy nie wykonują połączeń sieciowych.
- **RBAC** — `git:read` (lista/repozytoria), `git:write` (dodaj/usuń połączenie),
  `git:pr` (utwórz PR). Rola `operator`/`admin` je ma; `user`/`viewer` nie.

## Włączenie

```yaml
# config/git.yaml
enabled: true
connections_path: ./data/git-connections.json   # null = w pamięci

# config/security.yaml → egress.allowlist musi zawierać hosty dostawców:
# allowlist: ["api.github.com", "gitlab.com"]
```

Sekret z tokenem (PAT) dostarcz przez ENV/Vault/SOPS, np. `HUSARZ...`/`GITHUB_TOKEN`
i wskaż go w `token_ref` połączenia.

## Endpointy

| Metoda i ścieżka | Opis | Uprawnienie |
|------------------|------|-------------|
| `GET /api/git/connections` | lista połączeń (bez sekretu; tylko `token_ref`) | `git:read` |
| `POST /api/git/connections` | `{name, provider, api_base, token_ref, username?}` | `git:write` |
| `DELETE /api/git/connections/{name}` | usuń połączenie | `git:write` |
| `GET /api/git/connections/{name}/repos` | lista repozytoriów | `git:read` |
| `POST /api/git/connections/{name}/pull-request` | `{repo, title, head, base, body?}` → PR/MR | `git:pr` |

Błędy: nieznane połączenie → `404`; egress zablokowany → `403`; token/dostawca
odrzucił → `502`; kolizja nazwy połączenia → `409`.

## Konsola

Zakładka **Połączenia**: lista/dodawanie/usuwanie połączeń (token jako referencja),
podgląd repozytoriów i formularz utworzenia PR/MR dla wybranego połączenia.

## Ograniczenia

- Uwierzytelnianie: **PAT** (token) przez referencję do sekretu. Pełny **OAuth**
  (rejestracja aplikacji + callback) — kolejny krok (lepszy dla trybu hostowanego,
  wielu użytkowników z własnymi tokenami szyfrowanymi at-rest).
- PR opiera się na istniejących gałęziach po stronie dostawcy; automatyczny commit
  plików + push przez API dostawcy (agent „Kopijnik") — do rozbudowy.

Decyzje: [ADR-0011](adr/0011-integracje-git.md).
