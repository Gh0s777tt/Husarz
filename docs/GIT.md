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
  → HTTP 403). Suwerenność: bez jawnej zgody nie łączymy się z WAN. Git **świadomie nie
  korzysta** ze skrótu „endpoint lokalny jest zawsze wolny" (bezpiecznego dla lokalnego
  Ollamy, nie dla ścieżki niosącej token z prawem zapisu).
- **Anty-SSRF z pinowaniem IP** ([ADR-0020](adr/0020-pinowanie-ip-anty-ssrf.md)) — nazwa
  `api_base` jest rozwiązywana **dokładnie raz**, każdy zwrócony adres sprawdzany, jeden
  **przypinany**: transport łączy się z literałem IP, a nagłówek `Host` i weryfikacja
  certyfikatu TLS (SNI) idą po oryginalnej nazwie. Zamyka to okno DNS-rebindingu, w którym
  atakujący kontrolujący strefę allowlistowanej domeny przechwyciłby token PAT. Pin jest
  świeży przy KAŻDEJ operacji (klient budowany per wywołanie).
- **Polaryzacja adresów dla Git** — loopback (`127.0.0.1`, `localhost`, `*.localhost`)
  jest **twardo zablokowany**, a **prywatna sieć operatora (RFC 1918/ULA) dozwolona**:
  samodzielnie hostowany GitLab pod `https://git.firma.wewn/api/v4` to legalny scenariusz
  suwerenności. Luz dotyczy WYŁĄCZNIE zakresów prywatnych — link-local (metadane chmury
  `169.254.169.254`), CGNAT `100.64.0.0/10` i tunele osadzające IPv4 (6to4/Teredo/NAT64)
  pozostają zablokowane.
- **Transport HTTP wstrzykiwalny** — testy nie wykonują połączeń sieciowych (bezpiecznik
  w `tests/conftest.py` blokuje `socket.getaddrinfo` dla CAŁEGO zestawu testów).
  `trust_env=False` (zmienne `HTTP(S)_PROXY` nie przekierują przypiętego połączenia),
  `follow_redirects=False`, twardy sufit rozmiaru odpowiedzi (anty-OOM) i deadline
  wall-clock; komunikaty błędów transportu są generyczne (bez URL-a i wnętrzności httpx).
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

!!! danger "W profilu `airgap` integracja Git nie działa WCALE"
    Także z GitLabem w Twojej sieci lokalnej. Klient sprawdza allowlistę egress dla
    **każdego** hosta — świadomie nie stosuje skrótu „adres lokalny = zawsze dozwolony" —
    a walidacja profilu `airgap` wymusza **pustą** allowlistę (`husarz.config.schema`,
    walidacja krzyżowa). Żaden host nie przejdzie, więc integracja jest w tym profilu
    wyłączona całkowicie, nie tylko wobec internetu.

    To jest zamierzone (airgap = brak wyjścia), ale bywa zaskoczeniem: operator z własnym
    GitLabem w LAN-ie spodziewa się, że „lokalne" zadziała. Nie zadziała.

!!! warning "Własne CA nie zadziała — blokuje samodzielnie hostowanego GitLaba"
    `HttpxGitTransport` ustawia `verify=True` i `trust_env=False` **na sztywno**
    (`husarz/git/client.py`). `trust_env=False` jest celowe — zmienne `HTTP(S)_PROXY`
    nie mogą przekierować przypiętego połączenia wraz z tokenem przez cudzy serwer — ale
    powoduje też, że `SSL_CERT_FILE` i `REQUESTS_CA_BUNDLE` są **ignorowane**, a pola
    konfiguracyjnego na własny bundle CA **nie ma**.

    Instancja GitLaba z certyfikatem podpisanym przez prywatne CA jest więc nieosiągalna.
    Wymóg `https` w `api_base` operator sobie spełni; prywatnego CA nie wstrzyknie.

    **Uwaga na fałszywy trop:** `security.mtls.ca_cert_ref` wygląda na rozwiązanie, ale
    dotyczy mTLS **między usługami** i — jak cała sekcja `security.mtls` — nie jest dziś
    przez żaden kod odczytywane (mTLS to Etap 6). Ustawienie go nie zmieni niczego.

!!! note "Zakresy nie są równoważne między dostawcami"
    Utworzenie merge requesta na GitLabie wymaga zakresu `api`, czyli **pełnego odczytu
    i zapisu całego API użytkownika** — wszystkich grup, projektów, rejestru kontenerów
    i pakietów. GitHub ma węższy odpowiednik (`pull_requests:write`); GitLabowy
    `write_repository` dotyczy wyłącznie Git-over-HTTP i do MR-ów nie wystarcza.
    Do samego listowania projektów wystarcza `read_api` — nadawaj `api` dopiero wtedy,
    gdy faktycznie tworzysz MR-y.

- Uwierzytelnianie: **PAT** (token) przez referencję do sekretu. Pełny **OAuth**
  (rejestracja aplikacji + callback) — kolejny krok (lepszy dla trybu hostowanego,
  wielu użytkowników z własnymi tokenami szyfrowanymi at-rest). Uwaga: na GitHubie
  przepływ „kod autoryzacyjny + PKCE" **nie zwalnia z `client_secret`** (dokumentacja
  GitHuba oznacza go jako wymagany także z PKCE), więc jedyną drogą bez sekretu jest
  **device flow** — z widocznym kodem do przepisania, a nie „jednym kliknięciem".
- PR opiera się na istniejących gałęziach po stronie dostawcy; automatyczny commit
  plików + push przez API dostawcy (agent „Kopijnik") — do rozbudowy.

Decyzje: [ADR-0011](adr/0011-integracje-git.md).
