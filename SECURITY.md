# Polityka bezpieczeństwa — Husarz

Bezpieczeństwo i suwerenność danych to fundament Husarza, nie dodatek.
Ten dokument opisuje zasady, model zagrożeń w skrócie i sposób zgłaszania podatności.

## Zgłaszanie podatności

Podatności zgłaszaj **prywatnie** do opiekunów projektu (nie przez publiczne
issue). Podaj: opis, kroki reprodukcji, wpływ, propozycję naprawy. Do czasu
wydania poprawki prosimy o nieujawnianie szczegółów publicznie.

## Twarde zasady (niezmienniki)

1. **Deny-all egress** domyślnie. Żaden agent/model nie łączy się na zewnątrz,
   dopóki domena nie jest na allowliście. Profil `airgap` = brak WAN
   (walidacja konfiguracji wymusza pusty egress i brak sieci w sandboxie).
2. **Sekrety wyłącznie w Vault lub SOPS/age.** Nigdy w repo, obrazach ani
   logach. W konfiguracji dozwolone są tylko *referencje* (np. `sops:...`,
   `vault:secret/...`). Hook `gitleaks` (pre-commit + CI) blokuje wycieki.
3. **Sandbox narzędzi** (Docker + gVisor): bez sieci, limity CPU/RAM/czasu,
   dostęp tylko do `workspace`, allowlisty komend i ścieżek.
4. **Szyfrowanie at-rest** (dane, wektory, obiekty) i **mTLS** między usługami.
   **OIDC + RBAC** dla dostępu.
5. **Niemodyfikowalny audit log**: każde wywołanie narzędzia i decyzja routingu
   (łańcuch skrótów — tamper-evidence).
6. **Zero telemetrii.** Żadnego „phone home". Filtry anty-prompt-injection,
   izolacja treści niezaufanych, twarde allowlisty narzędzi per agent.
7. **Wagi modeli** trzymane lokalnie, niepublikowane (`models/` w `.gitignore`).

## Model dwuwarstwowy egress

Ruch wychodzący jest kontrolowany w dwóch warstwach:

- **Warstwa globalna** (`config/security.yaml -> egress`): domyślnie `deny`,
  z pustą allowlistą (pełny deny-all).
- **Warstwa narzędzia** (`config/tools/*.yaml -> requires_egress + allowlist`):
  narzędzie deklaruje potrzebne domeny.

Aby ruch faktycznie przeszedł, **obie** warstwy muszą go dopuścić — operator
świadomie dodaje domenę do globalnej allowlisty. Sama deklaracja narzędzia nie
otwiera sieci.

## Agent Puszkarz — granice (autoryzowany pentest)

- Działa **wyłącznie** na celach z podpisanego `config/roe/<zlecenie>.yaml`
  (właściciel, cele CIDR/domeny, okno czasowe, dozwolone techniki, zgoda).
  Cele spoza zakresu = **twardy blok**.
- **Domyślnie dry-run.** Akcje aktywne wymagają flagi `--authorized` i
  potwierdzenia operatora.
- **Integruje** istniejące narzędzia (recon, skanery) i wiedzę defensywną (RAG).
  **NIE generuje** działającego malware ani exploitów — w takim wypadku zwraca
  odmowę i proponuje działanie defensywne (audyt/hardening/detekcja).
- Każda akcja logowana z odniesieniem do ROE. **ROE-gate** to twarda bramka w
  `core/security`, przez którą przechodzą WSZYSTKIE narzędzia tego agenta
  (implementacja: Etap 4).

## Weryfikacja bezpieczeństwa w CI i testach

- `tests/security/` (marker `security`) pilnują niezmienników domyślnej
  konfiguracji (deny-all, brak sieci w sandboxie, audit włączony, zero
  telemetrii, ROE nieaktywne bez podpisu).
- CI uruchamia: `ruff`, `mypy`, `pytest`, `gitleaks`. Zielone CI jest warunkiem
  scalenia.

## Zakres wg etapów

Model bezpieczeństwa jest budowany etapami (patrz ROADMAP). Na Etapie 0
egzekwowana jest **konfiguracja** niezmienników i walidacja. Komponenty runtime
(sandbox, mTLS, OIDC, ROE-gate, audit hash-chain) dochodzą w Etapach 3–4.
Szczegóły i notatki weryfikacyjne: [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md).
