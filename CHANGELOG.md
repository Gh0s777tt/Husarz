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

### Bezpieczeństwo
- Domyślne niezmienniki: deny-all egress, sandbox bez sieci, audit log
  niemodyfikowalny, szyfrowanie at-rest, zero telemetrii — pokryte testami.
- `models/`, `.env` i sekrety w `.gitignore`; `gitleaks` skonfigurowany.

[Unreleased]: https://github.com/Gh0s777tt/Husarz/commits/main
