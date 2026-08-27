# Husarz 🪽

**Suwerenna, samodzielnie hostowana, wieloagentowa platforma AI.**

Husarz to lokalna platforma AI z architekturą agentową („Chorągiew", motyw
husarski), w pełni konfigurowalna, z własnym launcherem i interfejsem WWW,
zaprojektowana pod **suwerenność danych**: modele i dane **nie opuszczają
infrastruktury użytkownika bez wyraźnej zgody**. Domyślnie **deny-all egress**.

> Status: **Etapy 0–6 ukończone** — konfiguracja, router modeli, agenci +
> orkiestrator, narzędzia + sandbox, rdzeń bezpieczeństwa (ROE-gate/audit/Puszkarz),
> REST API + launcher + konsola WWW, oraz **deploy** (obrazy, compose dev/prod/airgap,
> k8s + NetworkPolicy deny-all, CI z SCA). Realne uruchomienie na klastrze (gVisor,
> pgvector, Vault unseal) oraz pełny OIDC/mTLS — środowisko docelowe. Patrz
> [ROADMAP.md](ROADMAP.md) i [docs/DEPLOY.md](docs/DEPLOY.md).

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
- **Router modeli:** warstwa OpenAI-compat do vLLM / Ollama / SGLang (✅ Etap 1).
- **Dane:** PostgreSQL + pgvector, Redis, MinIO/S3, Vault/SOPS (Etap 3-4).
- **Sandbox narzędzi:** Docker + gVisor (opcjonalnie Firecracker) (Etap 3).
- **Frontend:** własne UI (Next.js/React) — czat + panel konfiguracji (Etap 5).
- **Launcher:** CLI + opcjonalnie desktop (Tauri).
- **Konteneryzacja:** docker-compose (profile dev/prod/airgap) + k8s + NetworkPolicy deny-all (✅ Etap 6).

## Szybki start (dev)

Wymagania: Python 3.11+ (repo testowane na 3.13).

```bash
# 1) Środowisko
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\python.exe -m pip install -e ".[dev]"
# Linux/macOS:
# .venv/bin/python -m pip install -e ".[dev]"
# Trwała, szyfrowana pamięć długoterminowej (sqlite + AES-256-GCM at-rest) wymaga extry:
#   .venv\Scripts\python.exe -m pip install -e ".[dev,memory]"
# Podpis ROE algorytmem ed25519 wymaga extry [roe] (hmac-sha256 działa na samej stdlib):
#   .venv\Scripts\python.exe -m pip install -e ".[dev,roe]"

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
  modele (rejestr):  bielik, glm-main, hermes, husarz-local, husarz-vision
  agenci:            bielik, chorazy, husarz, kanclerz, kopijnik, puszkarz, zwiadowca
  narzędzia:         file_edit, git, plugin_example, rag, run_tests, shell, web
  ROE (zlecenia):    example-zlecenie
  egress:            deny
  sandbox:           docker+gvisor (sieć: nie)
```

## Lokalny czat i kodowanie (Ollama)

Husarz ma **customowy model lokalny** (persona hetmana, PL, czat + kodowanie) budowany
w [Ollamie](https://ollama.com) — dane nie opuszczają Twojej maszyny:

```bash
# 1) Zbuduj customowy model 'husarz' (baza wymienna w ollama/Husarz.Modelfile)
ollama pull qwen2.5-coder:7b
ollama create husarz -f ollama/Husarz.Modelfile

# 2) Uruchom platformę i wejdź do konsoli
python -m husarz.launcher.cli up --profile dev      # http://127.0.0.1:8000/
```

Zakładka **Czat** rozmawia bezpośrednio z modelem (`POST /api/chat`) — dymki, Markdown,
bloki kodu z „kopiuj". Przycisk 📎 dołącza **pliki, foldery i zdjęcia**; obrazy wymagają
modelu wizyjnego (`models: vision: true`, np. `husarz-vision`) — typ rozpoznawany z bajtów,
bez egressu. Szczegóły: [ollama/README.md](ollama/README.md), [docs/API.md](docs/API.md).

> **Orkiestracja wymaga jeszcze jednego kroku.** Powyższy start uruchamia sam **czat**
> (`models.chat` wskazuje `husarz-local`). Przełącznik **Orkiestracja** kieruje agentów przez
> `config/routing.yaml`, a dostarczony szablon przypisuje im modele serwowane przez vLLM
> (`glm-main`, `hermes`) — bez nich hetman zwróci `502 Backend modelu zawiódł`. Aby uruchomić
> Chorągiew wyłącznie na Ollamie, przypisz agentów do modelu lokalnego — w pliku
> `config/routing.yaml`:
>
> ```yaml
> agent_models:
>   husarz: husarz-local
>   bielik: husarz-local
>   kopijnik: husarz-local
>   zwiadowca: husarz-local
>   puszkarz: husarz-local
>   kanclerz: husarz-local
>   chorazy: husarz-local
> ```
>
> …albo bez restartu, nadpisaniem runtime z panelu (**Konfiguracja → Walidacja nadpisań**)
> lub przez `POST /api/config/runtime`. Aktualnie obowiązujące przypisanie widać zawsze
> w zakładce **Agenci** (kolumna *Model*). Jeden model 7B obsługuje wtedy wszystkie role
> po kolei, więc orkiestracja trwa wyraźnie dłużej niż czat.

**Coś nie działa?** Zakładka **Diagnoza** w konsoli (albo `python -m husarz.launcher.cli doctor`
w terminalu — to ta sama funkcja) wymienia, który model, katalog albo port jest problemem
i co z tym zrobić. Sprawdza cały łańcuch: czat, orkiestrację i każdego agenta osobno.

Jeśli diagnoza mówi, że wszystko jest, a czat i tak zawodzi — dodaj `--probe`. Wtedy zamiast
sprawdzać katalog silnika, zadaje modelom **prawdziwe pytanie**. Wczytuje wagi, więc trwa;
dlatego jest opcjonalna.

Gdy diagnoza zgłasza brakujący model, `husarz bootstrap` proponuje go pobrać — pokazując
**rozmiar w GB przed pobraniem** i pytając o zgodę (domyślna odpowiedź: nie). Pobiera silnik,
nie Husarz; domyślnie wyłączone; w profilu `airgap` odmawia twardo.
Szczegóły: [docs/LAUNCHER.md](docs/LAUNCHER.md).

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
- **Anty-SSRF z pinowaniem IP** na WSZYSTKICH pięciu ścieżkach wychodzących (`web`, wtyczki
  MCP, Git, embedder RAG, router modeli): nazwa rozwiązywana raz, adres przypinany, `Host`/SNI
  po nazwie — okno DNS-rebindingu zamknięte ([ADR-0020](docs/adr/0020-pinowanie-ip-anty-ssrf.md)).
- **Sekrety** wyłącznie jako REFERENCJE (`env:`/`file:`/`vault:`/`sops:`/`husarz:`) —
  nigdy materiał w repo, obrazach ani logach (hook gitleaks). Token, który Husarz
  **dostaje** w czasie działania (kreator połączeń Git), trafia zaszyfrowany do
  zapisywalnego magazynu, a w konfiguracji zostaje sama referencja — niezmiennik „config
  nie zawiera materiału" obowiązuje bez wyjątku
  ([ADR-0023](docs/adr/0023-zapisywalny-magazyn-sekretow.md)).
- **Sandbox** narzędzi bez sieci, limity CPU/RAM/czasu, allowlisty komend i ścieżek.
- **Szyfrowanie at-rest** i mTLS; **OIDC + RBAC**; **niemodyfikowalny audit log**.
- **Zero telemetrii**; filtry anty-prompt-injection; izolacja treści niezaufanych.
- **Puszkarz**: tylko autoryzowany pentest (ROE-gate, dry-run); nie tworzy exploitów.
- **Podpis ROE**: zlecenie jest ważne dopiero z poprawnym podpisem kryptograficznym
  (`ed25519`/`hmac-sha256`) obejmującym kanoniczną treść — poszerzenie zakresu, wydłużenie
  okna czy podniesienie zgody unieważnia podpis. Podpis wygenerujesz przez
  `husarz roe sign --engagement <id>` ([ADR-0021](docs/adr/0021-podpis-roe.md)).
  Bramka jest **wpięta w orkiestrator**: agent wymagający ROE jest delegowany wyłącznie pod
  zleceniem ze zgodą, ważnym podpisem i w oknie czasowym — w trybie dry-run i **bez dostępu
  do narzędzi**. Autoryzacja na konkretny cel (`RoeGate.evaluate`) czeka na nadanie Puszkarzowi
  zdolności wykonawczych.

## Dokumentacja

Źródłem prawdy jest katalog [`docs/`](docs/). Publikujemy go jako **portal HTML** (MkDocs
Material) ze zrzutami ekranu oraz **interaktywny PDF** (strona „Wersja do druku"):

```bash
.venv/Scripts/python.exe -m pip install -e ".[docs]"
.venv/Scripts/python.exe -m mkdocs serve      # podgląd na żywo: http://127.0.0.1:8000/
.venv/Scripts/python.exe -m mkdocs build      # statyczny portal -> ./site/
# PDF: otwórz w portalu „Wersja do druku / PDF" (/print_page/) i wydrukuj do PDF.
```

Poszczególne dokumenty (renderowane też w portalu):

- [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md) — architektura i przepływy.
- [docs/ROUTER.md](docs/ROUTER.md) — router modeli (wybór, fallbacki, koszty).
- [docs/ORKIESTRATOR.md](docs/ORKIESTRATOR.md) — rdzeń agentów i hetman „Husarz".
- [docs/NARZEDZIA.md](docs/NARZEDZIA.md) — narzędzia i sandbox (allowlisty, izolacja).
- [docs/API.md](docs/API.md) — REST API i konsola WWW (`husarz up`).
- [docs/KONTA.md](docs/KONTA.md) — konta, logowanie/rejestracja, sesje, limity tokenów.
- [docs/GIT.md](docs/GIT.md) — integracje GitHub/GitLab, połączenia, tworzenie PR/MR.
- [docs/WTYCZKI.md](docs/WTYCZKI.md) — wtyczki/konektory MCP (rozszerzalność, egress, RBAC).
- [docs/LAUNCHER.md](docs/LAUNCHER.md) — pobierany launcher (`husarz-app`, PyInstaller, CI).
- [docs/DEPLOY.md](docs/DEPLOY.md) — wdrożenie: obrazy, compose (dev/prod/airgap), k8s.
- [docs/AGENCI.md](docs/AGENCI.md) — role i konfiguracja agentów.
- [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md) — model bezpieczeństwa i weryfikacje.
- [docs/adr/](docs/adr/) — rejestr decyzji architektonicznych (ADR).
- [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING.md](CONTRIBUTING.md).

## Licencja

Apache-2.0 — patrz [LICENSE](LICENSE).
