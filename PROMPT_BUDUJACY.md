# PROMPT_BUDUJACY — specyfikacja Husarza

Ten dokument utrwala specyfikację, według której budowany jest Husarz.
Jest źródłem prawdy o intencjach produktu; kod i konfiguracja muszą być z nim zgodne.

## Rola i cel

Budujemy **Husarz** — suwerenną, samodzielnie hostowaną, wieloagentową platformę
AI. Praca metodyczna: planuj, implementuj małymi krokami, po każdym kroku
uruchamiaj testy i dokumentuj zmiany. **Zero hardcode** — wszystko istotne z konfiguracji.

Husarz to lokalna platforma AI z architekturą agentową (podejście Anthropic),
w pełni konfigurowalna, z własnym launcherem i UI, pod **suwerenność danych**:
modele i dane **nie opuszczają** infrastruktury użytkownika bez wyraźnej zgody.

## Modele (domyślne w konfiguracji, wymienne)

- **GLM-5.2** (Z.ai, open weights, MoE) — orkiestracja, rozumowanie, kod.
- **Bielik-11B-v3.0-Instruct** (SpeakLeash) — zadania i język polski.
- **Hermes** (NousResearch) — silnik agentów: pętle narzędziowe, function-calling.

Każdy rozwijalny niezależnie (wymiana wag, fine-tune, LoRA, nowe endpointy)
wyłącznie przez zmianę konfiguracji.

## Roster agentów — „Chorągiew"

- **Husarz** — orkiestrator (hetman): dekompozycja, routing, synteza (GLM-5.2).
- **Bielik** — specjalista języka i zadań polskich (Bielik v3).
- **Kopijnik** — agent kodujący: edycja plików, shell w sandboxie, git, testy (Hermes/GLM).
- **Zwiadowca** — agent researchu: web, dokumentacja, RAG (Hermes).
- **Puszkarz** — bezpieczeństwo: WYŁĄCZNIE autoryzowany pentest/audyt (Hermes).
- **Kanclerz** — dokumentacja: README, ADR, raporty, changelog (GLM/Bielik).
- **Chorąży** — router/planner pomocniczy: klasyfikacja intencji, koszty (mały/Hermes).

Klasy do rozszerzeń: **Towarzysz** (agent pełny), **Pocztowy** (podwykonawca).
Nowy agent = nowy plik `config/agents/<nazwa>.yaml`, BEZ zmian w rdzeniu.

## Zasada „zero hardcode"

- Żadnych kluczy, adresów, nazw modeli, ścieżek ani polityk w kodzie.
- Hierarchia: `defaults -> config/*.yaml -> ENV (HUSARZ_*) -> sekrety (Vault/SOPS) -> runtime (panel)`.
- Każdy config walidowany schematem (Pydantic) przy starcie; błąd = czytelny komunikat, nie crash.
- Pliki: `config/{husarz,models,routing,security}.yaml`, `config/agents/*`, `config/tools/*`, `config/roe/*`.
- Prompty systemowe agentów w `prompts/*.md` (edytowalne bez rekompilacji).

## Zasady bezpieczeństwa i prywatności (twarde wymagania)

1. Domyślnie **DENY-ALL egress**. Profil `airgap` = brak WAN.
2. Sekrety wyłącznie w Vault lub SOPS/age. Nigdy w repo, obrazach ani logach.
   Hook pre-commit (gitleaks) + `.gitignore` dla `models/`, `.env`, sekretów.
3. Każde narzędzie w sandboxie (Docker+gVisor): bez sieci, limity CPU/RAM/czasu,
   dostęp tylko do workspace. Allowlisty komend i ścieżek.
4. Szyfrowanie at-rest i mTLS między usługami. OIDC + RBAC.
5. Niemodyfikowalny audit log: każde wywołanie narzędzia i decyzja routingu.
6. Zero telemetrii. Filtry anty-prompt-injection, izolacja treści niezaufanych,
   twarde allowlisty narzędzi per agent.
7. Wagi modeli lokalnie, niepublikowane (`models/` gitignored).

## Agent Puszkarz — granice (autoryzowany pentest)

- Działa TYLKO na celach z podpisanego `config/roe/<zlecenie>.yaml`
  (właściciel, cele CIDR/domeny, okno czasowe, techniki, zgoda). Poza zakresem = twardy blok.
- Domyślnie **DRY-RUN**. Akcje aktywne wymagają `--authorized` i potwierdzenia operatora.
- INTEGRUJE istniejące narzędzia (recon, skanery) i wiedzę defensywną (RAG).
  **NIE generuje** malware ani exploitów — zwraca odmowę i proponuje działanie
  defensywne (audyt/hardening/detekcja).
- Każda akcja logowana z odniesieniem do ROE. **ROE-gate** — twarda bramka w
  `core/security`, przez którą przechodzą WSZYSTKIE narzędzia tego agenta.

## Stos technologiczny

- Rdzeń: Python 3.11+, FastAPI, Pydantic (walidacja configu).
- Router modeli: warstwa OpenAI-compat (wzorzec OmniRoute + LiteLLM) do vLLM/Ollama/SGLang.
- Dane: PostgreSQL + pgvector, Redis, MinIO/S3, Vault/SOPS.
- Sandbox: Docker + gVisor (opcjonalnie Firecracker).
- Frontend: własne UI (Next.js/React). Launcher: CLI + opcjonalnie Tauri.
- Konteneryzacja: docker-compose (profile dev/prod/airgap) + k8s z NetworkPolicy.

## Plan pracy (etapy)

Etap 0 (szkielet) → 1 (router) → 2 (agenci/orkiestrator) → 3 (narzędzia/sandbox)
→ 4 (bezpieczeństwo/ROE) → 5 (API/launcher/UI) → 6 (deploy/profile).
Po KAŻDYM etapie: testy + wpis do docs + commit. Szczegóły: [ROADMAP.md](ROADMAP.md).

## Wymagania jakościowe („ukończone")

- Kod otypowany, pokryty testami (unit + integration + e2e + bezpieczeństwo).
- Każdy komponent ma sekcję w `docs/`; istotne decyzje mają ADR.
- README aktualne; przykładowe configi działają out-of-the-box w profilu dev.
- Brak sekretów (gitleaks czysty). Brak połączeń wychodzących w profilu airgap.
- Nazwy agentów i profile zgodne z tą specyfikacją.

## Styl pracy

- Małe, weryfikowalne kroki; po każdym pokaż wynik testów.
- Niejednoznaczność → rozsądny domyślny wariant, udokumentuj, kontynuuj.
- Kompozycja i konfiguracja nad dziedziczeniem i hardcode.
- Komentarze i dokumentacja po polsku; identyfikatory w kodzie po angielsku.
