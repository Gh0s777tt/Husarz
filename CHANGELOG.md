# Changelog

Wszystkie istotne zmiany w projekcie Husarz. Format: [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie: [SemVer](https://semver.org/lang/pl/).

## [Unreleased]

### Dodane (Etap 0 — szkielet + loader konfiguracji)
- Struktura repozytorium (src-layout: `src/husarz/{config,core,router,orchestrator,agents,tools,memory,security,api,launcher}`).
- System konfiguracji „zero hardcode":
  - Schematy Pydantic v2 dla wszystkich sekcji (`platform`, `models`, `routing`,
    `security`, `agents`, `tools`, `roe`) z surową walidacją (`extra="forbid"`).
  - Loader z hierarchią nadpisań: defaults → `config/*.yaml` → ENV (`HUSARZ_*`) → runtime.
  - Walidacja krzyżowa (referencje modeli/narzędzi, reguły profilu `airgap`).
  - Czytelne błędy po polsku zamiast crasha.
  - Interfejs dostawców sekretów (`none`/`env`; Vault/SOPS — zaślepki na Etap 4).
- Przykładowa konfiguracja działająca out-of-the-box (profil `dev`):
  3 modele (GLM-5.2, Bielik v3, Hermes), 7 agentów Chorągwi, 6 narzędzi, szablon ROE.
- Prompty systemowe agentów w `prompts/*.md`.
- Launcher CLI: `husarz validate` / `husarz version` (`up` — zaślepka na Etap 5).
- Narzędzia jakości: `pyproject.toml` (ruff, black, mypy `strict`, pytest),
  `.pre-commit-config.yaml` (gitleaks, ruff, black), `.gitleaks.toml`, `.gitignore`, `.env.example`.
- Testy: jednostkowe (loader, schemat, ENV, walidacja krzyżowa, sekrety, CLI)
  oraz bezpieczeństwa (niezmienniki domyślnej konfiguracji). Wszystkie zielone.
- Dokumentacja: README, SECURITY, CONTRIBUTING, docs/{ARCHITEKTURA,AGENCI,BEZPIECZENSTWO},
  ADR-0001 (układ repo), ADR-0002 (hierarchia konfiguracji), ROADMAP, CLAUDE.md.
- CI (GitHub Actions): lint + typy + testy + gitleaks.

### Poprawione (adwersaryjny przegląd wieloagentowy Etapu 0)
- Loader: walidacja narzędzi agenta działa też przy pustym rejestrze narzędzi
  (każde odwołanie = błąd), a nie jest wtedy pomijana.
- Loader: obca zmienna `HUSARZ_*` (np. `HUSARZ_HOME`) jest ignorowana zamiast
  wywracać start (przyjmowane tylko znane sekcje).
- Loader: nadpisania ENV zachowują wielkość liter w kluczach map (id modelu,
  nazwa agenta), więc identyfikatory z wielkimi literami da się nadpisać.
- Loader: wykrywanie duplikatów kluczy odporne na klucze liczbowe (normalizacja `str`).
- Loader: wymóg rejestru modeli sprawdzany po scaleniu warstw — modele mogą
  pochodzić z ENV/runtime zgodnie z zadeklarowaną hierarchią.
- Loader: docstring coercji ENV zgodny z faktycznym (bezpiecznym) zachowaniem.
- CLI: profile podkomendy `up` pochodzą z enuma `Profile` (koniec duplikacji listy).
- Docs: `README` (pełne wyjście `validate`) i `ARCHITEKTURA` (reguły `prefer`/`auto`)
  zsynchronizowane z kodem.

### Bezpieczeństwo
- Domyślne niezmienniki: deny-all egress, sandbox bez sieci, audit log
  niemodyfikowalny, szyfrowanie at-rest, zero telemetrii — pokryte testami.
- `models/`, `.env` i sekrety w `.gitignore`; `gitleaks` skonfigurowany.
- **Hardening po przeglądzie**: bazowa linia bezpieczeństwa dla profili `prod`
  i `airgap` (sandbox włączony, audyt włączony i niemodyfikowalny, szyfrowanie
  at-rest — nie można ich cicho wyłączyć); profil `airgap` wymusza lokalne
  endpointy modeli; `ROE.is_active_at()` egzekwuje okno czasowe, a `is_active`
  wymaga niepustej referencji podpisu; `ModelSpec.api_key_ref` (klucz API jako
  referencja do sekretu, nie w `params`); allowlista `gitleaks` zawężona
  (koniec ślepej plamy na `docs/` i `prompts/`).

[Unreleased]: https://github.com/Gh0s777tt/Husarz/commits/main
