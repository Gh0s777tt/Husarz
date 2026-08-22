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
- CI uruchamia: `ruff`, `mypy`, `pytest`, `gitleaks`, `pip-audit` (SCA), `hadolint`,
  build obrazu oraz **bramkę jakości `husarz eval`**. Zielone CI jest warunkiem scalenia.

### Testy sprawdzają SKUTEK, nie deklarację

Test asertujący, że przekazaliśmy Dockerowi `--network none`, dowodzi wyłącznie tego, że
przekazaliśmy flagę — nie tego, że silnik ją egzekwuje. Dla warstw bezpieczeństwa taki test
jest **niewystarczający jako jedyny dowód**. Niezmienniki L2 (izolacja sandboxa) i wdrożeniowe
są dlatego weryfikowane także na uruchomionym kontenerze
(`tests/integration/test_sandbox_real.py`, `test_api_image.py`, `test_k8s_manifests.py`).

Testy wymagające środowiska (Docker, `kubectl`) **pomijają się z czytelnym powodem**, gdy go
brak — nigdy nie udają sukcesu. Weryfikacja, która zaokrągla „nie dało się sprawdzić" do
„w porządku", jest gorsza niż jej brak.

### Nośność testów bezpieczeństwa

Każdy nowy test niezmiennika bezpieczeństwa musi zostać sprawdzony **przez cofnięcie
poprawki** — test, który przechodzi bez niej, nie chroni niczego. Wynik takiej weryfikacji
odnotowuj w notatce w [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md).

## Audyty ciągłe

Przy KAŻDEJ zmianie dotykającej bezpieczeństwa, a nie raz na etap:

| Kontrola | Kiedy |
|---|---|
| `gitleaks protect --staged` | przed każdym commitem |
| Ręczny przegląd zmienionych plików pod kątem danych prywatnych | przed każdym pushem |
| Przegląd zrzutów ekranu (ścieżki, tokeny, e-maile, treść rozmów) | przed dołączeniem do docs |
| `pip-audit` (znane podatności zależności) | przy zmianie zależności i w CI |
| Notatka weryfikacyjna w `docs/BEZPIECZENSTWO.md` | przy każdej zmianie powierzchni ataku |
| Przegląd adwersaryjny (niezależne soczewki + próba OBALENIA każdego zgłoszenia) | przy KAŻDEJ zmianie w warstwie bezpieczeństwa, nie tylko przed zamknięciem etapu |
| Weryfikacja spójności wersji (tag = CHANGELOG = obrazy wdrożeniowe) | przy każdym wydaniu |

**Dlaczego przegląd adwersaryjny, a nie ponowne przeczytanie.** Powód jest empiryczny.
W Etapie 17 trzy takie przeglądy dały bilans: **z 19 zgłoszeń sprawdzonych osobno 18 okazało
się realnych** — w tym trzy wady w kodzie napisanym chwilę wcześniej i uznanym za sprawdzony
oraz dwa twierdzenia w tym samym dokumencie, które były nieprawdziwe. Wszystko przy zielonym
zestawie testów. Testy sprawdzały to, co autor MYŚLAŁ, że sprawdzają.

Dwie konsekwencje, obie zapisane w [CLAUDE.md](CLAUDE.md), sekcja „Testowanie, audyt
i dokumentacja KAŻDEJ zmiany": zgłoszenie odcięte przez limit weryfikacji traktujemy jako
prawdopodobne (nie hipotetyczne), ale sprawdzamy je sami uruchomieniem — bo jedno
z dziewiętnastu jednak się nie potwierdziło.

Nowa powierzchnia ataku = nowe niezmienniki w `tests/security/`. Bez nich zmiana nie jest
ukończona.

## Kod wrażliwy — obowiązek opisu

Fragmenty dotykające sieci, sekretów, deserializacji, wykonywania poleceń, sandboxa
i kryptografii są **obowiązkowo opisane** w kodzie i w `docs/BEZPIECZENSTWO.md`. Dla każdego
takiego fragmentu odpowiadamy jawnie na trzy pytania:

1. **Po co istnieje** — jaką zdolność daje i dlaczego jest konieczna.
2. **Jakie ryzyko niesie** — konkretny scenariusz nadużycia, nie ogólnik.
3. **Co go chroni** — które bramki, w jakiej kolejności, i co się stanie, gdy któraś zawiedzie.

Do tego pytanie rozstrzygające: **czy da się go usunąć albo zastąpić bezpieczniejszym?**
Jeżeli nie — uzasadnij, dlaczego jest niezbędny, i opisz obronę wielowarstwową.

## Zapis sekretów

Konfiguracja zawiera wyłącznie **referencje** (`env:` / `file:` / `vault:` / `sops:`), nigdy
materiał. `SecretsProvider` jest z założenia **jednokierunkowy — tylko odczyt**.

Jeżeli funkcja wymaga ZAPISU sekretu (np. token uzyskany w przepływie OAuth), musi to być
świadome rozszerzenie modelu, nie obejście: materiał trafia do magazynu sekretów, a do
konfiguracji wraca referencja. Zapis sekretu jest nową powierzchnią ataku i wymaga własnej
notatki weryfikacyjnej, testów niezmienników oraz decyzji o szyfrowaniu at-rest.

## Podpisy commitów i tagów

Podpis to kryptograficzne oświadczenie **konkretnej osoby**, że firmuje daną zmianę. Dlatego:

- podpisujemy, gdy operator skonfigurował klucz (`user.signingkey` + `commit.gpgsign`),
- **nie konfigurujemy klucza za operatora i nie podpisujemy w jego imieniu** — decyzja
  o wystawieniu takiego oświadczenia należy do niego,
- współautorstwo zmian tworzonych z udziałem narzędzi AI oznaczamy `Co-Authored-By`,
  żeby pochodzenie kodu było jawne.

Podpis kodu artefaktów dystrybuowanych (Windows Authenticode, notaryzacja Apple) jest osobną
sprawą, wymaga certyfikatów operatora i pozostaje jego decyzją — patrz `docs/LAUNCHER.md`.

## Zakres wg etapów

Model bezpieczeństwa jest budowany etapami (patrz ROADMAP). Na Etapie 0
egzekwowana jest **konfiguracja** niezmienników i walidacja. Komponenty runtime
(sandbox, mTLS, OIDC, ROE-gate, audit hash-chain) dochodzą w Etapach 3–4.
Szczegóły i notatki weryfikacyjne: [docs/BEZPIECZENSTWO.md](docs/BEZPIECZENSTWO.md).
