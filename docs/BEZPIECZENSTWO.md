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
