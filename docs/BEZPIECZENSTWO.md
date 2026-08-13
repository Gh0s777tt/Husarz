# Bezpieczeństwo — model i weryfikacja

Dokument techniczny modelu bezpieczeństwa Husarza oraz **notatek weryfikacyjnych**
(co sprawdzono, jak, z jakim wynikiem). Uzupełnienie [SECURITY.md](../SECURITY.md).

## Zasady (niezmienniki)

1. **Deny-all egress** domyślnie; `airgap` = brak WAN.
2. **Sekrety** wyłącznie w Vault/SOPS/age; w repo tylko referencje.
3. **Sandbox** narzędzi: bez sieci, limity CPU/RAM/czasu, tylko workspace, allowlisty.
4. **Szyfrowanie at-rest** + **mTLS** + **OIDC/RBAC**.
5. **Niemodyfikowalny audit log** (łańcuch skrótów).
6. **Zero telemetrii**; filtry anty-prompt-injection; izolacja treści niezaufanych.
7. **Wagi lokalnie** (`models/` gitignored).

## Dwuwarstwowy egress

Ruch wychodzący dopuszczają dopiero **obie** warstwy naraz:

- Globalna: `security.yaml -> egress.default_policy` (domyślnie `deny`) + `allowlist`.
- Narzędzia: `tools/*.yaml -> requires_egress` + własna `allowlist` domen.

Deklaracja narzędzia sama nie otwiera sieci — operator musi dodać domenę do
globalnej allowlisty. W profilu `airgap` globalna allowlista musi być pusta
(wymuszane walidacją).

## ROE-gate (Puszkarz)

- Puszkarz ma `roe_required: true`. Bez **aktywnego** ROE (zgoda + podpis) nie
  wykona akcji ofensywnej.
- ROE (`config/roe/*.yaml`): właściciel, `authorized_by`, zakres (CIDR/domeny +
  `out_of_scope`), okno czasowe, dozwolone/zabronione techniki, `consent`,
  `signature`, `dry_run_default`.
- `RoeConfig.is_active == (consent AND niepusty signature)`. Przykładowe ROE w
  repo jest **nieaktywne** (szablon). Kryptograficzną weryfikację podpisu wykonuje
  ROE-gate przez **wstrzykiwalny weryfikator** (`RoeGate(..., signature_verifier=...)`).
- Twarda bramka runtime (`RoeGate`) przepuszcza WSZYSTKIE narzędzia Puszkarza tylko
  po sprawdzeniu: aktywność ROE, (opcjonalnie) weryfikacja podpisu, okno czasowe,
  cel w zakresie (CIDR wyrównany, strict), technika (bez rozróżniania wielkości liter),
  tryb (dry-run/authorized). Każda decyzja → wpis w audit logu z odniesieniem do ROE.
- Puszkarz **nie generuje** malware/exploitów — zwraca odmowę i propozycję
  działań defensywnych.

## Zarządzanie sekretami

- Dostawcy: `none` (domyślny, nic nie zwraca), `env` (`env:NAZWA`),
  `file` (`file:nazwa` z konfinowanego katalogu), `sops` (`sops:plik#klucz`) i
  `vault` (`vault:mount/ścieżka#klucz`). `sops`/`vault` mają wstrzykiwalny backend
  (dekryptor/odczyt) i wymagają jawnej konstrukcji z parametrami — `get_secrets_provider`
  dla nich rzuca `ValueError` (nie `NotImplementedError`). Błąd backendu = fail-closed
  (zwraca `None`, nie propaguje — wyjątek mógłby nieść odszyfrowaną treść).
- Konfiguracja przechowuje wyłącznie referencje; wartości (z env/file/SOPS/Vault)
  rozwiązywane w runtime — w repo pozostają tylko referencje.
- `.gitignore` wyklucza `.env`, `*.key`, `*.pem`, `secrets/`, `models/`, `*.age`.
- `gitleaks` (pre-commit + CI) blokuje wyciek sekretów; `.gitleaks.toml`
  dopuszcza jedynie jawne placeholdery (`CHANGE_ME`, `PLACEHOLDER`, referencje `vault:`/`sops:`).

## Notatki weryfikacyjne

### Etap 0 — konfiguracja i niezmienniki (data: 2026-08-13)

**Zakres:** walidacja domyślnej konfiguracji i schematu; brak komponentów runtime.

**Co sprawdzono (testy `tests/security/`, marker `security`):**

| Niezmiennik                                   | Test | Wynik |
|-----------------------------------------------|------|-------|
| Domyślny egress = `deny`                       | `test_default_egress_is_deny` | ✅ |
| Zero telemetrii (i twarde odrzucenie `true`)   | `test_no_telemetry`, `test_telemetry_is_forbidden` | ✅ |
| Sandbox bez sieci, tylko workspace             | `test_sandbox_has_no_network_by_default` | ✅ |
| Audit log włączony i niemodyfikowalny          | `test_audit_log_enabled_and_immutable` | ✅ |
| Szyfrowanie at-rest włączone                   | `test_encryption_at_rest_enabled` | ✅ |
| Puszkarz wymaga ROE                            | `test_puszkarz_requires_roe` | ✅ |
| Przykładowe ROE nieaktywne                     | `test_example_roe_is_inactive` | ✅ |
| Profil `airgap` wymusza brak egress/sieci      | `test_airgap_*` (crossref) | ✅ |
| Narzędzie z egress ma niepustą allowlistę      | `test_web_tool_declares_egress_with_allowlist` | ✅ |

**Weryfikacja sekretów:** `gitleaks` skonfigurowany (pre-commit + CI);
`.gitignore` wyklucza sekrety i wagi. Brak sekretów w repo.

**Wynik:** wszystkie niezmienniki Etapu 0 spełnione. Komponenty runtime
(sandbox, mTLS, OIDC, ROE-gate, hash-chain) — do weryfikacji w Etapach 3–4.

### Etap 0 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

**Kontekst:** wieloagentowy, adwersaryjny przegląd Etapu 0 (5 wymiarów, każdy
finding weryfikowany osobno względem plików). Wdrożone utwardzenia warstwy
konfiguracji (jedynego obecnie strażnika, dopóki runtime to zaślepki):

| Nowy niezmiennik / poprawka                                   | Test |
|---------------------------------------------------------------|------|
| Bazowa linia bezpieczeństwa `prod`/`airgap`: sandbox włączony, audyt włączony+niemodyfikowalny, szyfrowanie at-rest | `test_prod_baseline_forbids_disabling_protections` |
| `airgap`: modele muszą mieć lokalne endpointy (loopback/prywatne/`.local`) | `test_airgap_rejects_remote_model_endpoint`, `test_airgap_allows_local_model_endpoint` |
| `airgap`: egress `allow` odrzucony (osobna gałąź)             | `test_airgap_forbids_egress_allow_policy` |
| ROE: `is_active_at(now)` egzekwuje okno czasowe; `is_active` wymaga niepustego podpisu | `test_roe_is_active_at_respects_window`, `test_roe_empty_signature_is_inactive` |
| Walidacja narzędzi agenta działa przy pustym rejestrze        | `test_agent_tool_validated_even_with_empty_registry` |
| `gitleaks`: zawężona allowlista (bez ślepej plamy na `docs/`, `prompts/`) | — (skan CI) |
| `ModelSpec.api_key_ref`: klucz API jako referencja do sekretu, nie w `params` | — (schemat) |

**Ograniczenia (świadome, do domknięcia w kolejnych etapach):**
- Egzekwowanie egress/sandbox/ROE to nadal *konfiguracja i walidacja*, nie
  wymuszenie w czasie działania — warstwa runtime powstaje w Etapach 3–4.
- Podpis ROE jest sprawdzany jako obecność niepustej referencji; kryptograficzna
  weryfikacja przez dostawcę sekretów — Etap 4.
- Dwuwarstwowy egress (allowlisty narzędzi ⊆ globalna allowlista) i audyt
  `runtime_override` sekcji `security` — Etap 4.
- Pola bezpieczeństwa w opaque `config`/`params` narzędzi (`network`, `allow_push`)
  do wypromowania na typowane pola — Etap 3.

### Etap 2 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

**Kontekst:** przegląd rdzenia agentów i orkiestratora (5 wymiarów, 20 findingów).
Wdrożone poprawki bezpieczeństwa:

| Niezmiennik / poprawka                                        | Test |
|---------------------------------------------------------------|------|
| Brak path traversal w `prompt_file` (wzorzec + konfinacja ścieżki) | `test_prompt_file_pattern_rejects_paths`, `test_read_prompt_confines_to_prompts_dir` |
| Obserwacje ogradzane i oznaczane jako dane w promptach hetmana | `test_observations_are_fenced_when_isolation_on` |
| Kontekst agenta poza system promptem (koniec inwersji zaufania) | `test_run_puts_context_in_fenced_user_message_not_system` |
| `security.prompt_injection_filters` realnie steruje izolacją    | `test_observations_not_fenced_when_isolation_off`, `test_build_orchestrator_custom_name_and_rounds` |
| ROE-gate na poziomie orkiestracji (agent `roe_required` blokowany) | `test_roe_required_agent_is_not_delegated` |
| Parser odporny na złośliwe wejście (RecursionError, obce nawiasy) | `test_parser_never_raises_on_recursion`, `test_extract_skips_stray_braces_in_prose` |

**Ograniczenie:** izolacja treści niezaufanej to ogrodzenie + instrukcja w prompcie
(mityguje indirect prompt injection). Twarde filtry I/O i pełny ROE-gate runtime — Etap 4.

### Etap 3 — hardening narzędzi i sandboxa (data: 2026-08-13)

**Kontekst:** przegląd warstwy narzędzi (4 wymiary, 21 findingów). Wdrożone utwardzenia:

| Niezmiennik / poprawka                                        | Test |
|---------------------------------------------------------------|------|
| Sandbox: non-root, rootfs read-only, `--pids-limit`, `--tmpfs`, montaż `:ro` | `test_argv_has_hardening_flags`, `test_argv_workspace_readonly_mount` |
| Sandbox: odrzucenie obrazu `-...`; kill kontenera po timeout    | `test_argv_rejects_dash_image` (kill: `pragma no cover`, wymaga Dockera) |
| web: blok literalnych adresów wewnętrznych/metadanych (SSRF)    | `test_web_blocks_internal_ip_literals` |
| file_edit: `max_bytes` przy odczycie; deny-glob case-insensitive | `test_read_enforces_max_bytes`, `test_glob_match_is_case_insensitive` |
| Konfinacja: escape przez symlink odrzucony                     | `test_symlink_escape_blocked` (skip bez uprawnień do symlinków) |

**Ograniczenia (świadome):**
- `shell` sprawdza tylko `argv[0]` — argumenty są dowolne; realną granicą jest sandbox,
  dlatego sekrety/wagi muszą być POZA montowanym workspace (deny-globi to warstwa dodatkowa).
- SSRF przez DNS rebinding (allowlistowana domena → adres wewnętrzny) wymaga pinowania IP — Etap 4/6.
- Realne wykonanie sandboxa (Docker+gVisor) weryfikowane w środowisku z Dockerem — Etap 6.

### Etap 4 — runtime bezpieczeństwa (data: 2026-08-13)

**Zakres:** `husarz.security` — audit log, ROE-gate, Puszkarz, RBAC + dostawcy sekretów.

| Niezmiennik                                                   | Test |
|---------------------------------------------------------------|------|
| Audit log tamper-evident (łańcuch skrótów; manipulacja wykryta) | `test_tampering_breaks_verification` |
| ROE-gate: akcja domyślnie dry-run                             | `test_action_defaults_to_dry_run` |
| ROE-gate: bez aktywnego ROE — blok nawet z `--authorized`     | `test_inactive_roe_hard_blocks` |
| ROE-gate: cel spoza zakresu/out_of_scope — twardy blok        | `test_out_of_scope_hard_blocks`, `test_denies_target_outside_scope` |
| ROE-gate: poza oknem czasowym / zabroniona technika — blok    | `test_denies_outside_time_window`, `test_denies_forbidden_and_unlisted_techniques` |
| Puszkarz: odmowa generowania ofensywy                         | `test_puszkarz_refuses_offensive_generation` |
| Każda decyzja audytowana z odniesieniem do ROE                | `test_every_decision_is_audited_and_tamper_evident` |
| RBAC: role→uprawnienia (wildcardy), odmowa domyślna           | `test_operator_has_tool_wildcard_but_not_config_write` |
| Sekrety: File konfinowany, SOPS/Vault po kluczu               | `test_file_secrets_provider_is_confined`, `test_sops_provider_navigates_key` |

### Etap 4 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

| Poprawka                                                      | Test |
|---------------------------------------------------------------|------|
| Techniki bez rozróżniania wielkości liter (koniec obejścia `SQLI`) | `test_forbidden_technique_case_insensitive` |
| CIDR wyrównany (strict) — koniec cichego poszerzania zakresu   | `test_cidr_with_host_bits_rejected` |
| Okno ROE i `now` normalizowane do UTC (koniec TypeError naive/aware) | `test_naive_now_does_not_crash` |
| Wstrzykiwalny weryfikator podpisu ROE; puste pola rozliczalności odrzucane | `test_signature_verifier_can_block`, `test_empty_accountability_fields_rejected` |
| Audyt: zapis-przed-pamięcią, deep-copy detail, opcjonalny HMAC, `load()`+`verify()` z pliku | `test_record_persist_first_no_divergence`, `test_load_from_file_and_detect_tampering`, `test_hmac_key_changes_hash_and_verifies` |
| Puszkarz: mniej fałszywych pozytywów (kontekst defensywny), audyt loguje skrót nie treść | `test_puszkarz_allows_defensive_yara_rule`, `test_puszkarz_refuses_infinitive_verb_exploit` |
| Sekrety SOPS/Vault: błąd backendu = fail-closed (bez wycieku)  | `test_vault_provider_handles_backend_error` |

**Ograniczenia (Etap 5/6):** pełna kryptograficzna weryfikacja podpisu ROE (domyślnie
tylko obecność; weryfikator jest wstrzykiwalny); audyt bez `hmac_key` jest tamper-evident
wobec przypadkowej korekty, z kluczem — wobec zmotywowanego edytora (zalecane w prod);
uwierzytelnienie (OIDC), mTLS oraz runtime egress/sandbox enforcement — API (Etap 5) i deploy (Etap 6).

### Etap 5 — uwierzytelnianie API i hardening (przegląd adwersaryjny, data: 2026-08-13)

**Zakres:** `husarz.api` (REST API + konsola) i launcher `husarz up`.

| Niezmiennik                                                   | Test |
|---------------------------------------------------------------|------|
| API wymaga tokenu Bearer, gdy skonfigurowany (poza `/api/health`) | `test_auth_required_when_token_set` |
| RBAC: operator bez `config:write`; viewer bez `agent:run`; admin pełny | `test_rbac_operator_cannot_write_config`, `test_rbac_viewer_cannot_orchestrate`, `test_rbac_admin_can_write_config` |
| Błędy routera → kody HTTP (429/502/503), nie gołe 500          | `test_orchestrate_maps_router_errors` |
| Liczniki usage/audyt spójne (próby + failures)                | `test_usage_counts_attempts_and_failures` |
| Łańcuch audytu atomowy pod współbieżnością (Lock)             | `test_audit_chain_atomic_under_threads` |
| `config/runtime` przebudowuje orkiestrator (koniec starej konfiguracji) | `test_config_runtime_rebuilds_orchestrator` |
| Launcher fail-closed: nasłuch poza loopbackiem bez tokenu = odmowa | `test_up_refuses_non_loopback_without_token` |
| Token API rozwiązywany z sekretu (env/file), fail-closed przy braku | `test_resolve_api_token_from_env`, `test_resolve_api_token_fails_closed_when_unresolvable` |
| Konsola: dane z API escapowane HTML (anty-XSS)                | `test_console_escapes_interpolated_data` |

### Etap 6 — niezmienniki wdrożeń (data: 2026-08-13)

**Zakres:** obrazy, Docker Compose (dev/prod/airgap), manifesty k8s. Weryfikacja
**statyczna** (parsowanie YAML) — bez uruchamiania klastra/Dockera.

| Niezmiennik                                                   | Test |
|---------------------------------------------------------------|------|
| API publikowane tylko na loopbacku (dev/airgap)               | `test_dev_compose_publishes_api_only_on_loopback`, `test_airgap_api_loopback_only_and_no_wan` |
| Prod: token wymagany, bez `--allow-insecure`; WAN tylko dla proxy | `test_prod_api_requires_token_no_insecure` |
| Airgap: brak proxy/sieci brzegowej (bez WAN)                  | `test_airgap_api_loopback_only_and_no_wan` |
| k8s: NetworkPolicy default-deny (ingress+egress)              | `test_k8s_default_deny_all_present` |
| k8s: reguły zezwalające nie otwierają `0.0.0.0/0`             | `test_k8s_allow_policies_do_not_open_wan` |
| k8s: Deployment non-root, read-only rootfs, drop ALL caps     | `test_k8s_deployment_hardened_non_root_readonly` |
| k8s: Ingress wymusza TLS                                       | `test_k8s_ingress_uses_tls` |
| Szablon Secret zawiera wyłącznie placeholdery                 | `test_secret_example_has_only_placeholders` |

**Weryfikacja manualna (wykonano):** cała suita zielona (ruff/black/mypy `--strict`,
pytest), gitleaks czysty (skan sekretów). Realne uruchomienie na klastrze z CNI
egzekwującym NetworkPolicy, gVisor dla sandboxa oraz Vault (unseal) — środowisko
docelowe (poza zakresem testów jednostkowych).

**Przegląd adwersaryjny Etapu 6 (wykonano):** wieloagentowy przegląd deploy (16
findingów, 12 potwierdzonych) — naprawiono m.in. blocker startu prod/airgap (brak
referencji tokenu w compose), hardening kontenera Compose (rootfs RO, drop caps,
no-new-privileges), hasło Redisa, przypięcie obrazów, `pull_policy: never` w airgapie,
poprawki CI (dind, hadolint). Nowe testy niezmienników: hardening compose, e2e
rozwiązanie tokenu prod, PSA `restricted`, sondy `/api/health`, brak `--allow-insecure`
w k8s. Odrzucone (fałszywe): pętla Vault, brak Secreta (celowy), zapis artifacts/workspace.

### Etap 7 — konta, sesje i limity tokenów (data: 2026-08-13)

**Zakres:** `husarz.accounts` (hasła, sesje, limity) + uwierzytelnianie API kont.

| Niezmiennik | Test |
|---|---|
| Hasła: hash `scrypt` (nie plaintext), losowa sól, weryfikacja w stałym czasie | `test_password_hash_is_not_plaintext_and_verifies`, `test_password_hash_uses_random_salt` |
| Błędny format hasha → fail-closed (False) | `test_verify_rejects_malformed_encoding` |
| Rejestracja domyślnie wyłączona („dla wybranych") | `test_registration_disabled_by_default`, `test_registration_disabled_returns_403` |
| Złe poświadczenia → `AuthenticationError`/401 (bez rozróżnienia user/hasło) | `test_authenticate_wrong_credentials`, `test_login_wrong_password_401` |
| Sesje: wygasanie (TTL) i unieważnianie (logout) | `test_session_expires`, `test_logout_invalidates_session` |
| Token sesji działa jako Bearer; zły token → 401 | `test_session_token_works_as_bearer` |
| Konta włączone ⇒ uwierzytelnianie ON (poza `/api/health`) | `test_auth_required_when_accounts_enabled` |
| Limit tokenów → HTTP 402; zużycie doliczane | `test_quota_blocks_with_402`, `test_chat_records_token_usage` |
| RBAC per użytkownik (viewer bez `agent:run`) | `test_viewer_account_cannot_chat` |
| Seed-admin fail-closed przy nierozwiązywalnym sekrecie | `test_seed_admin_fails_closed_when_secret_missing` |

**Weryfikacja na żywo (wykonano):** uruchomiony serwer z kontami — rejestracja →
token, `/api/auth/me` (rola, model `husarz-local`, liczniki), sesja jako Bearer (200),
wylogowanie → 401. Konsola serwuje modal logowania i pasek użytkownika.

### Etap 7 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

Przegląd (3 wymiary, 15 findingów, 12 potwierdzonych — brak osiągalnego obejścia
auth w dostarczanej ścieżce) i wdrożone utwardzenia:

| Poprawka | Test |
|---|---|
| Najmniejsze uprawnienia: nowe konta = rola `user` (nie `operator`) | `test_default_registration_role_is_user` |
| Anty-brute-force: blokada po N próbach (HTTP 429), audyt nieudanych | `test_login_lockout_after_max_attempts`, `test_api_login_lockout_returns_429`, `test_lockout_window_expires` |
| Walidacja `api_role`/`default_user_role` ∈ `roles`; seed parowany | `test_config_rejects_unknown_api_role`, `test_config_rejects_partial_seed` |
| Hasła scrypt `n=2**16` (jawny `maxmem`) | (przez pełną suitę haseł) |
| Trwały magazyn: zapis atomowy (`os.replace`) pod zamkiem | `test_file_store_atomic_leaves_no_tmp` |
| Sesje: sprzątanie wygasłych + limit per użytkownik | `test_session_sweep_bounds_growth` |
| Pusty token maszynowy normalizowany do braku | `test_empty_bearer_rejected_when_token_set` |
| Most config→konta (ENV/seed) i fail-closed z kontami | `test_accounts_enabled_and_built_from_env`, `test_up_with_accounts_allows_non_loopback` |
| `husarz useradd` (konta „dla wybranych") | `test_useradd_creates_persistent_account` |

**Ograniczenia (świadome):** limit tokenów jest miękki (rozliczanie po odpowiedzi);
throttling logowania jest per-konto/in-proces (per-IP i współdzielony magazyn sesji —
przy skalowaniu poziomym); rozliczanie tokenów orkiestracji — po zsumowaniu `usage`.

### Etap 8 — załączniki do czatu (data: 2026-08-13)

**Zakres:** `husarz.attachments` — treść załączników jest NIEZAUFANA.

| Niezmiennik | Test |
|---|---|
| Limit liczby plików (DoS) | `test_reject_too_many_files` |
| Przycięcie per plik + odrzucenie łącznego rozmiaru | `test_truncate_per_file`, `test_reject_total_too_large` |
| Odrzucenie danych binarnych (tylko tekst) | `test_reject_binary_content` |
| Konfinacja nazwy (tylko basename, koniec traversalu) | `test_clean_name_strips_path_traversal` |
| Ogrodzenie jako dane + neutralizacja domknięcia z treści (anti-injection) | `test_context_block_fences_and_defangs` |
| Wyłączenie przez config | `test_reject_when_disabled` |
| API: kontekst doklejony; binaria/limit → 400 | `test_chat_prepends_attachment_context`, `test_chat_rejects_binary_attachment`, `test_chat_rejects_too_many_attachments` |

**Ograniczenia:** zdjęcia wymagają modelu wizyjnego (poza wersją); brak chunkowania/RAG
dużych folderów (limity twarde); ścieżki serwera nie są przyjmowane od klienta.

### Etap 8 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

Przegląd (3 wymiary, 11 findingów, 10 potwierdzonych — brak osiągalnej iniekcji;
zasadniczą obroną pozostaje ramka w j. naturalnym + interpretacja modelu) i utwardzenia:

| Poprawka | Test |
|---|---|
| Ogrodzenie: prefiksowanie każdej linii treści (koniec udawania znaczników) | `test_context_block_neutralizes_marker_forgery` |
| Neutralizacja znacznika także w NAZWIE (redukcja run `=`) | `test_context_block_neutralizes_marker_in_name` |
| Czyszczenie treści ze znaków sterujących/formatujących (ANSI/bidi/zero-width) | `test_strip_control_chars_from_content` |
| Bezpieczne przycinanie wielobajtowe (poprawny UTF-8) | `test_truncate_multibyte_safe` |
| Limit rozmiaru ciała (Content-Length → 413) — ochrona OOM przed ingestią | `test_body_size_limit_returns_413` |
| Sufit liczby załączników na poziomie schematu (422) | `test_schema_caps_attachment_count` |
| Pusta/sterująca nazwa → wartość domyślna | `test_clean_name_empty_becomes_default` |

**Ograniczenia:** wolnotekstowa perswazja w treści nie jest (i nie może być)
w pełni wyeliminowana strukturalnie — ogrodzenie + etykieta „dane, NIE instrukcje"
to obrona miękka; limit tokenów pozostaje miękki (rozliczany po odpowiedzi).

### Etap 9 — integracje Git (data: 2026-08-13)

**Zakres:** `husarz.git` — połączenia z GitHub/GitLab, tworzenie PR/MR.

| Niezmiennik | Test |
|---|---|
| Token jako REFERENCJA do sekretu (nie plaintext); brak tokenu → GitAuthError | `test_service_provider_for_missing_token_raises_auth`, `test_add_and_list_connection_hides_no_secret` |
| Bramka egress (deny-all): host dostawcy spoza allowlisty → EgressError/403 | `test_build_provider_egress_denied_by_default`, `test_egress_blocked_returns_403` |
| Host na allowliście → połączenie dozwolone | `test_build_provider_egress_allowed_when_allowlisted` |
| Błędny token dostawcy (401/403) → GitAuthError; 4xx → GitError | `test_github_auth_error`, `test_github_error_status` |
| RBAC: `user` bez `git:read` → 403 | `test_rbac_user_cannot_access_git` |
| Magazyn połączeń: zapis atomowy, bez sekretu | `test_file_connection_store_persists` |
| Transport wstrzykiwalny — testy bez sieci | całość `test_git.py`/`test_git_api.py` |

**Ograniczenia:** uwierzytelnianie to PAT (referencja) — pełny OAuth + tokeny
szyfrowane at-rest (tryb hostowany) odłożone; egzekwowanie egress na warstwie
aplikacji (pełne wymuszenie sieciowe: NetworkPolicy/sandbox, Etap 6).

### Etap 9 — hardening po przeglądzie (data: 2026-08-13)

Przegląd (3 wymiary, 11 findingów, 11 potwierdzonych — w tym blocker SSRF) i utwardzenia:

| Poprawka | Test |
|---|---|
| SSRF: twardy blok hostów wewnętrznych (loopback/link-local/metadata) dla Git | `test_build_provider_blocks_internal_host_ssrf`, `test_build_provider_egress_denied_by_default` |
| api_base https-only, bez userinfo | `test_build_provider_rejects_non_https_and_userinfo`, `test_add_connection_rejects_http_422` |
| token_ref musi być referencją (surowy token → 422) | `test_add_connection_rejects_raw_token_422` |
| repo bez wstrzyknięć (walidator + URL-encode) | `test_pull_request_rejects_bad_repo_422`, `test_github_create_pr_encodes_repo_path` |
| Magazyn: zapis atomowy pod zamkiem; odporny `_load` | `test_file_connection_store_persists`, `test_file_store_load_corrupt_raises_clean` |
| Klient: pomijanie elementów nie-dict; audyt próby PR przed budową dostawcy | `test_list_repositories_skips_non_dict_items`, `test_pull_request_egress_block_is_audited` |
| RBAC: user bez git:write/git:pr; 502 z odmowy dostawcy | `test_rbac_user_cannot_write_or_pr`, `test_provider_auth_error_maps_502` |

**Ograniczenia:** ochrona przed DNS-rebinding (walidacja po rozwiązaniu nazwy) —
do rozważenia; egzekwowanie egress to warstwa aplikacji (pełne wymuszenie: NetworkPolicy).

### Etap 11 — zdjęcia w czacie / modele wizyjne (data: 2026-08-13)

**Zakres:** obrazy jako wejście `POST /api/chat` dla modeli wizyjnych. Wejście
NIEZAUFANE — weryfikowane z bajtów, bez egressu.

| Niezmiennik | Test |
|---|---|
| Typ z magic-bytes (png/jpeg/gif/webp), nie z deklarowanego MIME/rozszerzenia | `test_sniff_recognizes_formats`, `test_chat_rejects_non_image_bytes` |
| Dane nie-obraz (poprawny base64, ale nie obraz) → odrzucone `400` | `test_reject_non_image`, `test_chat_rejects_non_image_bytes` |
| Błędny base64 → odrzucony (bez wyjątku niekontrolowanego) | `test_reject_bad_base64` |
| Limit liczby / rozmiaru per obraz egzekwowany | `test_reject_too_many_images`, `test_reject_too_large_image` |
| Wyłączone obrazy w configu → odrzucone | `test_reject_when_disabled` |
| Bramka `vision`: model bez `vision:true` → obraz odrzucony `400` | `test_chat_image_rejected_on_non_vision_model` |
| Brak egressu: obraz jako data-URI (base64), nie zewnętrzny URL (brak SSRF) | `test_message_payload_with_images_is_multimodal`, `test_client_sends_multimodal_payload` |
| Treść tekstowa bez obrazów pozostaje `str` (brak zmian dla modeli tekstowych) | `test_message_payload_plain_text` |

**Ograniczenia:** brak głębszej inspekcji zawartości obrazu (np. polyglot PNG/HTML,
zip-bomby graficzne) — chroni limit rozmiaru i re-enkodowanie; jakość i bezpieczeństwo
interpretacji obrazu zależą od lokalnego modelu wizyjnego (poza rdzeniem).

### Etap 11 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

Przegląd (3 wymiary, 5 potwierdzonych findingów → 3 odrębne przyczyny) i utwardzenia:

| Poprawka | Test |
|---|---|
| Bramka vision egzekwowana na KAŻDYM kandydacie routera — po awarii modelu wizyjnego obraz nie trafia do modelu tekstowego przez fallback (ADR-0013 end-to-end) | `test_images_skip_nonvision_fallback`, `test_images_use_vision_fallback` |
| Brak obrazów → łańcuch fallbacków działa normalnie (bramka nie zawęża) | `test_no_images_still_falls_back_to_text` |
| Limit ciała odporny na `Transfer-Encoding: chunked` (bez `Content-Length`) — bufor z twardym sufitem, czyste `413`, brak pre-auth OOM | `test_chunked_body_over_limit_returns_413_end_to_end`, `test_body_limit_blocks_chunked_over_limit`, `test_body_limit_single_huge_chunk_not_buffered` |
| Pojedynczy nadmiarowy chunk nie wchodzi do pamięci (sprawdzenie przed doklejeniem) | `test_body_limit_single_huge_chunk_not_buffered` |
| Obrazy wiązane z ostatnią wiadomością `user`; brak `user` + obraz → `400` | `test_image_binds_to_last_user_not_assistant`, `test_image_rejected_without_user_message` |

**Ograniczenia:** `BodySizeLimitMiddleware` buforuje ciało (wszystkie endpointy czytają
JSON w całości — brak strat); dla realnie strumieniowych uploadów wymagałby trybu
przepływowego. Sufit pamięci ≈ `max_request_bytes` + jeden bufor odczytu serwera.

### Etap 12 — system wtyczek (konektory MCP) (data: 2026-08-13)

**Zakres:** rejestr providerów narzędzi (12a) + konektor MCP z odkrywaniem narzędzi
(12b). Powierzchnia ataku: zewnętrzny serwer narzędzi (mniej zaufany niż host operatora).
Projekt z panelu 3 architektur + adwersaryjnej krytyki bezpieczeństwa (zakres zawężony
do discover-only; domknięte: bypass IPv4-mapped, nietypowany `config`/`allowlist`,
kolizja ROE, DoS wynikiem, wyciek transportu do audytu).

| Niezmiennik | Test |
|---|---|
| Rejestr narzędzi: nieznany `kind` → `ToolError` (komunikat zachowany); rozszerzenie bez zmian w rdzeniu | `test_empty_registry_reports_kind_as_unknown`, `test_injected_registry_extends_without_core_change` |
| Anty-SSRF: loopback dozwolony, adresy wewnętrzne/metadanych (w tym `::ffff:169.254.169.254`) TWARDY BLOK | `test_loopback_allowed`, `test_internal_and_metadata_hard_blocked` |
| Host publiczny wymaga https + allowlisty egress; http poza loopbackiem odrzucone; userinfo odrzucone | `test_public_http_rejected_requires_https`, `test_public_https_without_allowlist_denied`, `test_userinfo_rejected` |
| Odmowa egress/SSRF NIE wychodzi na sieć (transport nietknięty) | `test_blocked_endpoint_never_hits_transport`, `test_discover_blocked_endpoint_returns_403` |
| Token WYŁĄCZNIE jako referencja; surowy token → walidacja odrzuca | `test_config_rejects_raw_token_reference` |
| Token rozwiązywany leniwie, NIE wycieka do audytu/API/odpowiedzi; brak tokenu gdy wymagany → fail-closed | `test_discover_audited_without_token`, `test_list_plugins_hides_secret`, `test_service_missing_token_raises_auth` |
| Audyt `plugin.discover` przed wyjściem; łańcuch niemodyfikowalny | `test_discover_audited_without_token` |
| RBAC: rola `user` bez `plugin:read` → 403; deny-by-default → 404 | `test_rbac_user_cannot_read_plugins`, `test_plugins_404_when_disabled` |
| Wynik NIEZAUFANY: limit `max_output_bytes` egzekwowany; koperta JSON-RPC + Bearer | `test_build_connector_uses_plugin_limits`, `test_list_tools_builds_jsonrpc_envelope_and_bearer` |

**Ograniczenia:** brak pinowania IP → DNS-rebinding endpointu możliwy (jak `web`/`git`,
odłożone). MVP odkrywa narzędzia, nie wywołuje ich — `tools/call` z autoryzacją
per-wywołanie wchodzi z pętlą function-calling. Transport `stdio` (sandbox) poza MVP.
Rejestr wtyczek jest first-party (bez `entry_points`) — brak wektora RCE/łańcucha dostaw.

### Etap 12 — hardening po przeglądzie adwersaryjnym (data: 2026-08-13)

Przegląd (3 wymiary, 6 potwierdzonych findingów) i utwardzenia:

| Poprawka | Test |
|---|---|
| Anty-DNS-rebinding: nazwa rozwiązywana, każdy adres sprawdzony wobec bloku wewnętrznego; metadane/prywatne mimo allowlisty → blok | `test_domain_resolving_to_metadata_blocked`, `test_domain_resolving_to_private_blocked_under_allow_policy` |
| Nierozwiązywalna nazwa → fail-closed (odmowa) | `test_unresolvable_domain_fails_closed` |
| `security.egress.allowlist`: wpis pusty/whitespace/URL odrzucony przy starcie (koniec częściowego wildcardu) | `test_empty_allowlist_entry_rejected_at_config` |
| Nierozwiązywalny `token_ref` → `PluginSecretError` → HTTP 500 (nie 502 „wina serwera") | `test_service_missing_token_raises_secret_error`, `test_unresolved_token_returns_500_not_502` |
| Anty-„slow-drip": bezwzględny deadline wall-clock na pętli odczytu transportu | (obrona w kodzie: `HttpxPluginTransport`; brak realnej sieci w testach) |
| TLS `verify=True` jawnie; martwe pole `protocol_version` usunięte | (spójność docs↔kod; walidacja schematu) |

**Ograniczenia:** pełne pinowanie IP (okno TOCTOU) pozostaje odłożone; „slow-drip"
deadline jest sprawdzany między chunkami (ograniczenie ~`timeout` + jeden odczyt).

### Etap 13 — pętla narzędziowa (function-calling) (data: 2026-08-13)

**Zakres:** pierwszy egzekutor narzędzi (model steruje wykonaniem) — największa nowa
powierzchnia ataku. Projekt z panelu 3 architektur + krytyki; przyjęte poprawki:
opt-in per agent (nie predykat z klasy), GLOBALNY budżet wywołań (nie tylko per-krok),
cap `rag.add`, wzbogacony audyt `web`, parytet ogradzania kontekstu, cięcie `ToolProtocol`.

| Niezmiennik | Test |
|---|---|
| L1: narzędzie spoza allowlisty agenta → deny, instancja NIGDY nie wołana (sandbox nietknięty) | `test_tool_outside_allowlist_denied_never_executes` |
| L0: agent `roe_required` → pętla nie startuje (fail-closed), model NIE wywołany | `test_roe_agent_refused_without_model_call` |
| Wynik NIEZAUFANY ogrodzony przed re-injekcją; marker akcji z wnętrza wyniku zneutralizowany | `test_injected_marker_in_tool_result_neutralized`, `test_result_is_fenced_before_reinjection` |
| Parser akcji działa tylko na treści asystenta (nie na ogrodzonym wyniku) → brak eskalacji | `test_injected_marker_in_tool_result_neutralized` |
| Limit iteracji per krok + globalny budżet wywołań kończą deterministycznie (anty-amplifikacja) | `test_iteration_limit_terminates`, `test_global_budget_terminates` |
| Kontekst (niezaufane obserwacje) ogrodzony — parytet z `BaseAgent` | `test_context_is_fenced_like_base_agent` |
| Audyt bez surowej treści/sekretów (rozmiar+sha256; web=host); łańcuch niemodyfikowalny | `test_audit_arg_summary_has_no_raw_content` |
| Dispatch: zły kształt args → `ok=False` bez wyjątku/efektu; brak `getattr` na danych modelu | `test_bad_args_shell_command_not_list`, `test_unknown_tool_and_action` |
| `rag.add` z tekstem ponad limit odrzucony (anty-OOM współdzielonego magazynu) | `test_rag_add_oversize_rejected` |
| `workspace_dir` rozłączny z `data_dir`/`artifacts_dir` (izolacja zapisu) | walidacja `_cross_validate` |

**Ograniczenia:** ogrodzenie NL to obrona miękka (nie powstrzyma perswazji wolnotekstowej —
twardą barierą jest allowlista + sandbox); `shell` z `python` = RCE-w-sandboxie (dla takich
agentów granicą jest sandbox); `web` model-sterowane bez pinowania IP (DNS-rebinding — jak Git);
audyt wiąże agenta, nie użytkownika (korelacja principal↔wywołanie — follow-up). Pętla jest
**opt-in** (`tool_loop_enabled`, domyślnie false) — w dostarczonej konfiguracji wyłączona.

### Etap 14 — pamięć długoterminowa (RAG) (data: 2026-08-13)

**Zakres:** produkcyjny `EmbeddingRagBackend` (wektorowa pamięć). Powierzchnia: treść
NIEZAUFANA (trwały kanał injekcji cross-agent), embeddingi ~ PII (odwracalne). Projekt z
panelu + krytyki suwerenności; at-rest/trwałość świadomie odłożone do 14b (bez teatru).

| Niezmiennik | Test |
|---|---|
| Izolacja cross-agent: `add` w kolekcji A nie wypływa w `search` B (namespace) | `test_cross_agent_memory_no_leak`, `test_store_namespace_isolation` |
| Rozłączne kolekcje wymuszone przy starcie (kolizja namespace → błąd) | `test_rag_collections_must_be_disjoint` |
| Suwerenność embeddingów: WAN pod deny-all → `EgressError` PRZED wysłaniem wektora | `test_embedder_egress_blocks_wan` |
| Airgap: nielokalny endpoint embeddera → błąd startu (embeddingi ~ PII) | `test_airgap_rejects_nonlocal_embedder_endpoint` |
| Klucz embeddera WYŁĄCZNIE jako referencja (surowy → odrzucony) | `test_embedder_key_must_be_reference` |
| Wymiar wektora walidowany fail-closed (anty-korupcja magazynu) | `test_ollama_embedder_dim_mismatch_fails_closed`, `test_embedding_backend_dim_mismatch_rejected` |
| Wzrost bounded: `max_items` + ewikcja FIFO (model-sterowany `add`) | `test_store_growth_capped`, `test_store_cap_and_fifo_eviction` |
| Wynik `search` re-injektowany jako ogrodzone DANE (pętla) | `test_embedding_memory_add_then_search_in_loop` |

**Ograniczenia (świadome, odłożone do 14b):** MVP jest ulotny (RAM) — trwałość
(`SqliteVectorStore`) + szyfrowanie at-rest (`AesGcmCipher`) wchodzą RAZEM z przewleczeniem
`SecretsProvider` do produkcji (inaczej klucz nierozwiązywalny = teatr). `FakeEmbedder` to
test-double, nie realne wyszukiwanie — produkcja wymaga Ollamy; domyślny backend `memory`
(słowny) zapewnia brak regresji. Deszyfruj-przed-scoringiem (14b) → koszt O(N), stąd `max_items`.

### Etap 14b — trwałość + szyfrowanie at-rest pamięci (data: 2026-08-13)

**Zakres:** `SqliteVectorStore` (trwały plik) + szyfrowanie at-rest CAŁEGO rekordu
(`AesGcmCipher`, AES-256-GCM) + przewleczenie `SecretsProvider`/`data_dir` do produkcji —
domknięcie blockera z Etapu 14 (klucz teraz realnie rozwiązywany, nie martwe pole).
Powierzchnia: dane pamięci na dysku (PII/treść), sekret klucza, ścieżka pliku.
**Wymóg bramki jakości:** instalacja `[dev,memory]` (extra `cryptography`) — test at-rest jest
twardy (nie skip).

| Niezmiennik | Test |
|---|---|
| At-rest: plik `.db` NIE zawiera jawnego tekstu, metadanych, wektora ANI odcisku treści (`id`) | `test_sqlite_at_rest_no_plaintext_on_disk`, `test_sqlite_encrypted_dedup_and_blinded_id` |
| Jawna kolumna `id` zaślepiona (`blind_id`=HMAC pod DEK) — brak membership-oracle/korelacji | `test_sqlite_encrypted_dedup_and_blinded_id` |
| Niekontrolowany błąd sqlite (`sqlite3.Error`) → `RagBackendError` (degradacja, nie HTTP 500) | (opakowanie `upsert/search/count`) |
| Brak extry `cryptography` przy at-rest → błąd PRZY BUDOWIE (nie odroczony `ImportError`) | `build_cipher` (fail-closed) |
| Pola at-rest wymagają `store: sqlite`; `store: sqlite` wymaga `backend: embedding` | `test_atrest_fields_require_sqlite_and_embedding` |
| Niezgodny wymiar wektora w trwałym magazynie → `RagBackendError` (nie cicha `0.0`) | `test_sqlite_dim_mismatch_fails_closed` |
| `AAD=namespace` (anti-swap): rekord kolekcji A nie odszyfruje się jako B | `test_aesgcm_wrong_aad_rejected` |
| Zły klucz → odrzucenie (InvalidTag → `RagBackendError`, bez wycieku) | `test_aesgcm_bad_key_rejected` |
| Fail-closed: sqlite + at-rest + brak klucza → błąd startu (nigdy plaintext) | `test_sqlite_encrypt_without_key_fails_closed`, `test_build_cipher_gates` |
| Globalny `at_rest=true` nie może być wyłączony lokalnie dla trwałego magazynu | `test_global_at_rest_forbids_local_encrypt_false` |
| `IdentityCipher` dozwolony WYŁĄCZNIE gdy at-rest wyłączony (dev) | `test_sqlite_unencrypted_allowed_when_global_off` |
| Trwałość: pamięć przeżywa reopen; dedup + cap FIFO na dysku | `test_sqlite_persists_across_reopen`, `test_sqlite_dedup_and_cap` |
| Sekret przewleczony przez pętlę → szyfrowana pamięć na dysku (E2E) | `test_sqlite_encrypted_memory_persists_and_via_loop` |
| Izolacja `namespace` w sqlite (search filtruje kolekcję) | `test_sqlite_roundtrip_and_namespace` |

**Ograniczenia (świadome):** DEK = SHA-256 sekretu (KDF-lite) — bez soli/rotacji; pełne
KMS/rotacja odłożone. Scoring deszyfruje O(N) rekordów (brak indeksu ANN) — sufit `max_items`.
SQLCipher / szyfrowany wolumen odłożone na rzecz przenośnej koperty app-level. Schematy
`vault:`/`sops:` są przyjmowane przez walidator `encryption_key_ref`, ale dostarczony resolver
CLI rozwiązuje `env:`/`file:` — `vault:`/`sops:` wymagają wspierającego `SecretsProvider`
(zachowanie i tak fail-closed: brak klucza → błąd, nie plaintext). Połączenie `SqliteVectorStore`
żyje przez cykl życia stacku; przy `POST /api/config/runtime` nowe łączenie powstaje bez zamknięcia
starego (follow-up: `close()` w protokole). Szczegóły i alternatywy — ADR-0018.
