# Husarz 🪽

**Suwerenna, samodzielnie hostowana, wieloagentowa platforma AI.**

Husarz to lokalna platforma AI z architekturą agentową („Chorągiew", motyw
husarski), w pełni konfigurowalna, z własnym launcherem i interfejsem WWW,
zaprojektowana pod **suwerenność danych**: modele i dane **nie opuszczają
infrastruktury użytkownika bez wyraźnej zgody**. Domyślnie **deny-all egress**.

> Status: **Etap 0 (szkielet + loader konfiguracji) — ukończony.**
> Kolejne etapy: patrz [ROADMAP.md](ROADMAP.md).

---

## Zasada „zero hardcode"

Nic istotnego nie jest zaszyte w kodzie. Wszystko pochodzi z konfiguracji,
walidowanej schematem Pydantic przy starcie:

```
defaults (kod)  ->  config/*.yaml  ->  ENV (HUSARZ_*)  ->  sekrety (Vault/SOPS)  ->  runtime (panel)
```

Model, agent, narzędzie czy polityka bezpieczeństwa zmienia się **wyłącznie
przez edycję konfiguracji** — nie kodu.

## Chorągiew — roster agentów

| Agent      | Rola                                   | Model domyślny |
|------------|----------------------------------------|----------------|
| **Husarz**    | Orkiestrator (hetman): dekompozycja, routing, synteza | GLM-5.2 |
| **Bielik**    | Język i zadania polskie                | Bielik-11B-v3.0 |
| **Kopijnik**  | Kod: edycja plików, shell (sandbox), git, testy | Hermes / GLM |
| **Zwiadowca** | Research: web (allowlist), dokumentacja, RAG | Hermes |
| **Puszkarz**  | Bezpieczeństwo: **wyłącznie autoryzowany pentest** (ROE-gate, dry-run) | Hermes |
| **Kanclerz**  | Dokumentacja: README, ADR, changelog, raporty | GLM / Bielik |
| **Chorąży**   | Router/planner pomocniczy (intencje, koszty) | mały / Hermes |

Klasy do rozszerzeń: **Towarzysz** (agent pełny), **Pocztowy** (podwykonawca).
Nowy agent = nowy plik `config/agents/<nazwa>.yaml`, bez zmian w rdzeniu.

## Modele (domyślne z konfiguracji, wymienne)

- **GLM-5.2** (Z.ai) — orkiestracja, rozumowanie, kod.
- **Bielik-11B-v3.0-Instruct** (SpeakLeash) — język i zadania PL.
- **Hermes** (NousResearch) — pętle narzędziowe, function-calling.

Każdy rozwijalny niezależnie (wymiana wag, fine-tune, LoRA, nowy endpoint)
przez zmianę `config/models.yaml`. Wagi trzymane lokalnie (`models/`, gitignored).

## Stos technologiczny

- **Rdzeń:** Python 3.11+, Pydantic (walidacja configu), FastAPI (API — Etap 5).
- **Router modeli:** warstwa OpenAI-compat do vLLM / Ollama / SGLang (Etap 1).
- **Dane:** PostgreSQL + pgvector, Redis, MinIO/S3, Vault/SOPS (Etap 3-4).
- **Sandbox narzędzi:** Docker + gVisor (opcjonalnie Firecracker) (Etap 3).
- **Frontend:** własne UI (Next.js/React) — czat + panel konfiguracji (Etap 5).
- **Launcher:** CLI + opcjonalnie desktop (Tauri).
- **Konteneryzacja:** docker-compose (profile dev/prod/airgap) + k8s + NetworkPolicy (Etap 6).

## Szybki start (dev)

Wymagania: Python 3.11+ (repo testowane na 3.13).

```bash
# 1) Środowisko
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Linux/macOS:
# .venv/bin/python -m pip install -e ".[dev]"

# 2) Walidacja przykładowej konfiguracji (działa out-of-the-box)
.venv\Scripts\python.exe -m husarz.launcher.cli validate --config ./config

# 3) Testy i bramki jakości
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
```

Oczekiwany wynik `validate`:

```
Konfiguracja Husarza wczytana poprawnie.
  profil:            dev
  log_level:         INFO
  model domyślny:    glm-main
  modele (rejestr):  bielik, glm-main, hermes
  agenci:            bielik, chorazy, husarz, kanclerz, kopijnik, puszkarz, zwiadowca
  narzędzia:         file_edit, git, rag, run_tests, shell, web
  ROE (zlecenia):    example-zlecenie
  egress:            deny
  sandbox:           docker+gvisor (sieć: nie)
```

## Konfiguracja

| Plik                         | Zawartość |
|------------------------------|-----------|
| `config/husarz.yaml`         | Ustawienia globalne (profil, logi, katalogi) |
| `config/models.yaml`         | Rejestr modeli + domyślny wybór + fallbacki |
| `config/routing.yaml`        | Router: model per agent, reguły po tagach, koszty |
| `config/security.yaml`       | Egress, sandbox, mTLS, OIDC/RBAC, audit, szyfrowanie |
| `config/agents/*.yaml`       | Definicje agentów (jeden plik = jeden agent) |
| `config/tools/*.yaml`        | Definicje narzędzi (allowlisty, sandbox, egress) |
| `config/roe/*.yaml`          | Rules of Engagement dla Puszkarza (autoryzacja pentestu) |
| `prompts/*.md`               | Prompty systemowe agentów (edytowalne bez rekompilacji) |

Nadpisania przez ENV: prefiks `HUSARZ_`, zagnieżdżenie przez `__`, np.
`HUSARZ_PLATFORM__PROFILE=airgap`. Szczegóły: [.env.example](.env.example).

## Bezpieczeństwo i prywatność

Twarde wymagania (patrz [SECURITY.md](SECURITY.md) i [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md)):

- **Deny-all egress** domyślnie; profil `airgap` = brak WAN.
- **Sekrety** wyłącznie w Vault/SOPS — nigdy w repo, obrazach ani logach (hook gitleaks).
- **Sandbox** narzędzi bez sieci, limity CPU/RAM/czasu, allowlisty komend i ścieżek.
- **Szyfrowanie at-rest** i mTLS; **OIDC + RBAC**; **niemodyfikowalny audit log**.
- **Zero telemetrii**; filtry anty-prompt-injection; izolacja treści niezaufanych.
- **Puszkarz**: tylko autoryzowany pentest (ROE-gate, dry-run); nie tworzy exploitów.

## Dokumentacja

- [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md) — architektura i przepływy.
- [docs/AGENCI.md](docs/AGENCI.md) — role i konfiguracja agentów.
- [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md) — model bezpieczeństwa i weryfikacje.
- [docs/adr/](docs/adr/) — rejestr decyzji architektonicznych (ADR).
- [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencja

Apache-2.0 — patrz [LICENSE](LICENSE).
