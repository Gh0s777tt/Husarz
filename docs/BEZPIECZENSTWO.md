# Bezpieczeństwo — model i weryfikacja

Dokument techniczny modelu bezpieczeństwa Husarza oraz **notatek weryfikacyjnych**
(co sprawdzono, jak, z jakim wynikiem). Uzupełnienie [SECURITY.md](https://github.com/Gh0s777tt/Husarz/blob/main/SECURITY.md).

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

### Granice integracji Git — trzy ustalenia (data: 2026-08-22)

Ustalone przy projektowaniu logowania OAuth; wszystkie trzy dotyczą stanu OBECNEGO i były
dotąd nieudokumentowane, więc ujawniłyby się dopiero przy nieudanym połączeniu.

**1. W profilu `airgap` integracja Git nie działa wcale — także w sieci lokalnej.**
`husarz.git.client` sprawdza allowlistę egress dla **każdego** hosta; świadomie nie stosuje
skrótu „adres lokalny = zawsze dozwolony", mimo że warstwa SSRF dopuszcza dla Gita
`allow_lan=True`. Walidacja krzyżowa profilu `airgap` wymusza **pustą** allowlistę, więc
żaden host nie przejdzie. To zamierzone, ale nieoczywiste: operator z własnym GitLabem
w LAN-ie spodziewa się, że „lokalne" zadziała.

**2. Brak możliwości wskazania własnego CA.** `HttpxGitTransport` ma `verify=True`
i `trust_env=False` na sztywno. Drugie jest celowe i wartościowe — zmienne `HTTP(S)_PROXY`
nie mogą przekierować przypiętego połączenia wraz z tokenem przez cudzy serwer — ale
powoduje, że `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` są ignorowane, a pola na bundle CA nie ma.
Instancja z certyfikatem prywatnego CA jest nieosiągalna. **To blokuje samodzielnie
hostowanego GitLaba mocniej niż wymóg `https`**: HTTPS operator sobie postawi, CA nie wstrzyknie.

**3. `security.mtls` jest sekcją czysto deklaratywną.** Grep po `src/husarz/` daje **zero**
odczytów pól `mtls` poza schematem — `enabled`, `ca_cert_ref`, `cert_ref`, `key_ref` są
walidowane i nieużywane (mTLS to Etap 6). Samo w sobie jest to ujawnione w ROADMAP-ie
i ARCHITEKTURZE, ale w połączeniu z punktem 2 tworzy **fałszywy trop**: `ca_cert_ref` wygląda
na rozwiązanie problemu CA i nim nie jest. Ustawienie go nie zmienia niczego.

**Konsekwencja dla przyszłego OAuth.** Zweryfikowano w dokumentacji GitHuba, że przepływ
„kod autoryzacyjny + PKCE" **nie zwalnia z `client_secret`** — jest on oznaczony jako
wymagany także z PKCE, a wyjątkiem jest wyłącznie device flow („The `client_secret` is not
needed for the device flow"). Wariant „przycisk → przeglądarka → gotowe" wymagałby więc
zarządzania drugim sekretem po stronie operatora i jest ślepą uliczką dla klienta publicznego.

Zmian w kodzie nie wprowadzono — to notatka o granicach stanu obecnego.


### Manifesty k8s — weryfikacja po ZBUDOWANIU, nie po parsowaniu (data: 2026-08-21)

**Ograniczenie dotychczasowej weryfikacji.** `test_deploy_invariants.py` parsuje surowe
pliki. Kustomize je jednak PRZEKSZTAŁCA (namespace, etykiety, selektory), a na klastrze liczy
się wynik przekształcenia. Klasa wad niewidoczna dla parsowania: selektor, który nie trafia
w pod (usługa bez endpointów, polityka bez skutku), Ingress wskazujący nieistniejącą usługę,
`targetPort` bez odpowiadającego portu kontenera.

**Co sprawdzono na ZBUDOWANYM overlayu** (`kubectl kustomize deploy/k8s`, 9 zasobów):

| Kontrola | Wynik |
|---|---|
| Selektor Deploymentu trafia w etykiety poda | ✅ |
| Selektor Service trafia w pod | ✅ |
| Selektory NetworkPolicy trafiają (albo są puste = wszystkie pody) | ✅ |
| Ingress wskazuje istniejącą usługę i port | ✅ |
| `targetPort` odpowiada portowi kontenera | ✅ |
| `default-deny-all` obejmuje wszystkie pody, oba kierunki | ✅ |

Manifesty okazały się spójne — nie znaleziono wady. Kontrola została mimo to utrwalona jako
`tests/integration/test_k8s_manifests.py`, bo to dokładnie ta klasa błędów, która przechodzi
parsowanie i ujawnia się dopiero przy `kubectl apply`. Testy używają `kubectl kustomize`,
żeby nie odtwarzać semantyki kustomize po swojemu — własna reimplementacja mogłaby się
rozjechać i dawać fałszywe poczucie bezpieczeństwa.

**Znaleziona pułapka aktualizacyjna.** `kustomization.yaml` używał `commonLabels`, co kubectl
zgłasza jako przestarzałe. Groźniejsza jest jednak druga właściwość tego pola: **wstrzykuje
etykiety do SELEKTORÓW**, a selektor Deploymentu jest **niemodyfikowalny po utworzeniu**.
Każda przyszła zmiana `commonLabels` zablokowałaby więc aktualizację działającego wdrożenia
komunikatem `field is immutable`, wymuszając ręczne usunięcie zasobu. Zamienione na składnię
`labels:` z `includeSelectors: false` — etykieta trafia wyłącznie do metadanych. Zmiana jest
bezpieczna teraz, bo nic nie zostało jeszcze wdrożone; po wdrożeniu byłaby przełomowa.

**Ograniczenie, które zostaje.** Nadal nie zweryfikowano manifestów na DZIAŁAJĄCYM klastrze
(brak CNI z obsługą NetworkPolicy, gVisor, Vault). Sprawdzona jest spójność wyniku budowy,
nie egzekwowanie polityk sieciowych przez konkretny CNI.


### Profile `prod` i `airgap` — pierwsze realne uruchomienie (data: 2026-08-21)

**Zakres.** Oba profile wdrożeniowe były dotąd sprawdzane wyłącznie przez parsowanie YAML-a.
Uruchomiono je po raz pierwszy (obrazy budowane lokalnie; `.env` z wartościami próbnymi,
poza repozytorium).

**Profil `prod` — niezmienniki potwierdzone:**

| Niezmiennik | Wynik |
|---|---|
| Profil w runtime | ✅ `"profile": "prod"` |
| API **nie publikuje** portów | ✅ `8000/tcp` (bez mapowania na host) |
| API nieosiągalne bezpośrednio z hosta | ✅ `HTTP 000` — jedynym wejściem jest Caddy |
| Token wymagany | ✅ bez tokenu **401**, z tokenem **200** |

Sprzeczności `ports` + `internal` tu **nie ma** i nigdy nie było: API portów nie publikuje,
bo ruch mostkuje proxy w sieci brzegowej `husarz_edge`. To potwierdza, że wzorzec z `prod`
był poprawny, a wadliwe były profile bez proxy.

**Profil `airgap` — ta sama wada co w `dev`.** Nakładka deklarowała
`ports: "127.0.0.1:8000:8000"` i zapowiadała w komentarzu „dostęp do API wyłącznie przez
loopback HOSTA", ale dziedziczyła sieć `internal: true` z base. Skutek: kontener wstawał
jako `healthy` z poprawnym profilem w środku (`"profile": "airgap"`), a `curl` z hosta nie
łączył się w ogóle (`HTTP 000`). **Profil nie spełniał własnej obietnicy.**

Nakładka nadpisuje teraz `internal: false`. Dlaczego to nie osłabia airgapu:

1. Na maszynie faktycznie odciętej **nie ma trasy do WAN** — nie ma czego blokować.
2. Profil `airgap` w konfiguracji wymusza przy STARCIE: deny-all egress, pustą allowlistę,
   brak sieci w sandboxie i wyłącznie lokalne endpointy modeli (walidacja krzyżowa schematu).
   To bramka aplikacyjna, niezależna od sieci Dockera.
3. Ruch narzędzi przechodzi dodatkowo przez pinowanie IP (ADR-0020).

`prod` zostaje na sieci `internal` z base — tam sprzeczności nie ma.

**Luka we WŁASNYM teście.** Niezmiennik dodany dzień wcześniej
(`test_published_ports_are_not_paired_with_internal_only_network`) sprawdzał **wyłącznie
główny `docker-compose.yaml`** i dlatego przegapił nakładkę `airgap`. Dołożono
`test_overlay_profiles_have_no_port_contradiction`, który scala nakładki z base — bo
`internal` bywa nadpisywane właśnie tam.

**Znalezione przy okazji — tag obrazu kłamał o wersji.** `docker-compose.base.yml` przypinał
`husarz-api:0.1.0`, gdy projekt był w **0.14.0**. Compose sam buduje ten obraz i nadaje mu
etykietę, więc wdrożony artefakt niósł nieprawdziwą wersję. Tag jest teraz parametrem
(`HUSARZ_IMAGE_TAG`) z domyślną wartością sparowaną z `husarz.__version__`, a
`test_compose_image_tag_matches_project_version` pilnuje, żeby nie rozjechał się przy
kolejnym wydaniu.


### Profil `dev` w compose — kontener był NIEOSIĄGALNY (data: 2026-08-21)

**Objaw.** `docker compose up -d` kończył się kontenerem w stanie **`healthy`**, ale
`curl http://127.0.0.1:8000/api/health` nie łączył się w ogóle. API działało — sprawdzone
od środka kontenera (`{"status":"ok",...}`) — tylko nie było do niego drogi z hosta.

**Przyczyna.** Sprzeczność w dostarczonym `docker-compose.yaml`: deklaracja
`ports: "127.0.0.1:8000:8000"` obok sieci `husarz_internal` z `internal: true`. **Docker
cicho wyłącza publikowanie portów dla sieci internal** — zamiast
`127.0.0.1:8000->8000/tcp` raportuje samo `8000/tcp`, a deklaracja `ports` jest martwa.
Ten sam mechanizm odcinał dostęp do modelu na hoście, więc czat w tym profilu też nie miał
prawa działać.

**Dlaczego nie wykryły tego testy.** `test_deploy_invariants.py` asertował OBIE wartości
naraz — publikowanie na loopbacku ORAZ `internal: true` — nie zauważając, że się wykluczają.
Statyczna asercja przechodziła, a rzecz nie działała. To ten sam wzorzec, co przy sandboxie
i obrazie API: weryfikacja deklaracji zamiast skutku.

**Naprawa.** Sieć profilu `dev` nie jest już `internal`. To świadomy kompromis, opisany
w pliku: dev **nie ma proxy**, które mogłoby mostkować ruch (w `prod` robi to Caddy w sieci
`husarz_edge`, a samo API pozostaje wewnętrzne i portów nie publikuje — tam sprzeczności nie
było). Egress w profilu `dev` jest egzekwowany **warstwą aplikacji**:
`security.egress.default_policy: deny` + allowlista + pinowanie IP (ADR-0020). Wymuszenie
sieciowe pozostaje tam, gdzie było zaprojektowane — k8s NetworkPolicy deny-all
(`deploy/k8s/`) oraz profil `airgap` bez trasy do WAN.

**Nowy niezmiennik w testach.** `test_published_ports_are_not_paired_with_internal_only_network`
odrzuca każdą usługę, która publikuje porty, będąc wyłącznie w sieci `internal`. Nośność
potwierdzona: przywrócenie `internal: true` czerwieni test.

**Zweryfikowane po naprawie** (`docker compose up -d`): mapowanie
`127.0.0.1:8000->8000/tcp`, `/api/health` → `{"status":"ok","profile":"dev"}`, konsola → 200,
`/api/agents` → 7 agentów.


### Obraz `husarz-api` — hardening zweryfikowany na kontenerze (data: 2026-08-21)

**Dlaczego osobna notatka.** Niezmienniki obrazu (non-root, brak zapisu do rootfs, fail-closed
przy nasłuchu poza loopbackiem) były dotąd sprawdzane wyłącznie przez **parsowanie plików
wdrożeniowych** (`tests/security/test_deploy_invariants.py`). To weryfikacja deklaracji, nie
skutku — dokładnie ta sama luka, którą zamknęliśmy dzień wcześniej dla sandboxa.

**Co zweryfikowano na uruchomionym obrazie** (`husarz-api:ci`, 250 MB):

| Niezmiennik | Sprawdzenie | Wynik |
|---|---|---|
| Non-root | `id` w kontenerze | ✅ `uid=1000(husarz)` |
| Rootfs niezapisywalny dla użytkownika aplikacji | `touch /probny` | ✅ `Permission denied` |
| Konfiguracja działa wewnątrz obrazu | `husarz validate` | ✅ „wczytana poprawnie" |
| **Fail-closed przy nasłuchu poza loopbackiem** | `up --host 0.0.0.0` bez auth | ✅ odmowa startu, kod ≠ 0 |
| Liveness bez tokenu | `GET /api/health` | ✅ 200 |
| Endpoint chroniony bez tokenu | `GET /api/agents` | ✅ **401** |
| Endpoint chroniony z tokenem | `GET /api/agents` | ✅ 7 agentów |
| Konsola WWW | `GET /` | ✅ 200 |

Czwarty wiersz jest tu najważniejszy: konteneryzacja z natury nasłuchuje na `0.0.0.0`, więc
gdyby bramka nie zadziałała, **pierwsze `docker run` wystawiłoby nieuwierzytelnione API**.
Bramka zadziałała — kontener odmówił startu z czytelnym komunikatem i wskazówką, co ustawić.

Testy: `tests/integration/test_api_image.py` (marker `integration`), pomijane bez obrazu.

**Znalezione przy okazji — obrazu NIE DAŁO SIĘ zbudować na macOS.** `docker build` przerywa
już przy wysyłaniu kontekstu: `failed to xattr ._CHANGELOG.md: operation not permitted`.
Przyczyną są sidecary AppleDouble (`._*`), które macOS tworzy na wolumenach bez natywnych
atrybutów rozszerzonych (exFAT/NTFS/sieciowe). Wada jest niewidoczna w CI, bo Linux tych
plików nie tworzy.

!!! warning "Sprostowanie: `.dockerignore` tego NIE naprawia"
    Pierwotnie zapisano tu, że dodanie `._*` do `.dockerignore` rozwiązuje problem. **To
    nieprawda** — zweryfikowane empirycznie: z jednym sidecarem `docker build` kończy się
    kodem **1** mimo wpisu, bez sidecara kodem **0**. Błąd powstaje w nadawcy kontekstu,
    zanim reguły ignorowania zostaną zastosowane. Wpis w `.dockerignore` zostaje, bo jest
    poprawny co do zasady (nie wpuszcza śmieci do obrazu), ale **sam problem rozwiązuje
    wyłącznie usunięcie plików przed budową**.

    Sidecary **odrastają przy każdym zapisie** na wolumen, więc to krok do powtarzania:

    ```bash
    python scripts/clean_sidecars.py && docker build -t husarz-api:ci .
    ```

    Skrypt kasuje wyłącznie pliki o sygnaturze AppleDouble (`0x00051607`) i pomija wnętrze
    `.git` — skasowanie tam `._pack-*.idx` potrafi uszkodzić indeks paczek.


### Sandbox — pierwsza weryfikacja na REALNYM silniku (data: 2026-08-21)

**Dlaczego to notatka osobna.** Izolacja sandboxa była dotąd sprawdzana **wyłącznie po
`argv`** (`build_docker_argv`). To dobre testy jednostkowe, ale odpowiadają tylko na pytanie
„czy poprosiliśmy Dockera o właściwe flagi" — nie na pytanie, czy silnik faktycznie je
egzekwuje. Flaga w wierszu poleceń i zachowanie runtime'u to dwie różne rzeczy, a cała
warstwa L2 opierała się na tym niesprawdzonym założeniu.

**Środowisko.** Docker 29.6.1 (Docker Desktop, macOS arm64), obraz `husarz-sandbox:latest`
zbudowany z `docker/husarz-sandbox.Dockerfile` (377 MB, python:3.13-slim + git + pytest).
gVisor (`runsc`) **niedostępny** — dostępne runtime'y to `runc` i `io.containerd.runc.v2`,
więc `runtime_class` zostało w testach ustawione na `None`. To jedyna zmieniona wartość;
wszystkie pozostałe niezmienniki obowiązywały w postaci dostarczonej w `config/security.yaml`.

**Co zweryfikowano — skutki, nie deklaracje:**

| Niezmiennik | Sprawdzenie | Wynik |
|---|---|---|
| Wykonanie w kontenerze | `python --version` | ✅ `Python 3.13.15` |
| Non-root | `id` | ✅ `uid=1000(sandbox) gid=1000(sandbox)` |
| **Brak sieci** | `urlopen('https://1.1.1.1')` — literał IP, żeby mierzyć sieć, a nie DNS | ✅ pada |
| Rootfs tylko-do-odczytu | `touch /probny` | ✅ `Read-only file system` |
| `/tmp` zapisywalny mimo powyższego | `touch /tmp/ok` | ✅ działa |
| Montaż workspace | odczyt pliku z `/workspace` | ✅ |
| `run_tests` przez pełną warstwę narzędzi | zestaw zielony / czerwony | ✅ `exit=0` / `exit=1` |

Testy: `tests/integration/test_sandbox_real.py` (marker `integration`). Bez Dockera albo bez
obrazu są **pomijane z czytelnym powodem** — nigdy nie udają sukcesu.

**Ograniczenie, które zostaje.** Nie zweryfikowano ścieżki z gVisorem, bo `runsc` nie jest
dostępny na tej maszynie. Profil produkcyjny deklaruje `engine: docker+gvisor`, więc
**dodatkowa warstwa izolacji jądra pozostaje niesprawdzona empirycznie** — sprawdzone jest
wszystko poza nią. To zadanie dla środowiska docelowego (Etap 6).

**Znalezione przy okazji — ciche pomijanie nieznanych argumentów narzędzia.** Docstring
`ToolDispatcher.dispatch` obiecywał „Nieznane tool/action/args → `ToolResult(ok=False)`",
ale nieznane **argumenty** były w rzeczywistości po cichu odrzucane. Model proszący o
`run_tests.run(path="x")` dostawał przebieg CAŁEGO zestawu i był przekonany, że zawęził
zakres. To ta sama klasa wady, którą projekt domknął w Etapie 3b dla martwych kluczy
konfiguracji. Dispatch waliduje teraz argumenty wobec `ActionSpec.params` — zbioru
zamkniętego, identycznego z tym, który model widzi w manuale — i zwraca komunikat wprost
poprawialny: `Akcja 'run_tests.run' nie przyjmuje argumentów: path. Dozwolone: extra_args.`


### Pomiar przebiegów agenta — prywatność z konstrukcji (data: 2026-08-21)

**Kontekst.** Etap 16 wymaga materializacji przebiegu agenta: bez liczby nie da się ocenić,
czy zmiana promptu, routingu albo modelu cokolwiek poprawiła. Przebieg agenta jest jednak
**najwrażliwszym materiałem w całym systemie** — niesie treść zadania użytkownika, wyniki
narzędzi, fragmenty plików i zapytania do pamięci.

**Decyzja: rekord niesie METRYKI, nie TREŚĆ.** `husarz.runs.RunRecord` **fizycznie nie ma pola
na tekst**. Zapisujemy: rodzaj tury (`final`/`action`/`malformed`), nazwę narzędzia i akcji,
czy wywołanie się powiodło, czy zablokowała je bramka, długości w znakach, zużycie tokenów
i powód zakończenia. Nie zapisujemy: treści zadania, odpowiedzi modelu, argumentów narzędzi.

Uzasadnienie kolejności:

1. **Bezpieczeństwo z konstrukcji, nie z konfiguracji.** Struktura nie ma pola na swobodny
   tekst, więc nie wycieknie go przez błąd ani przez złe ustawienie domyślne.

    !!! warning "Doprecyzowanie po przeglądzie adwersaryjnym"
        Sama nieobecność pola tekstowego NIE wystarcza. Pola `tool` i `action` pochodzą
        z bloku akcji, czyli **od modelu** — zapisywane wprost dawały 64-znakowy kanał na
        dowolną treść. Dlatego wpuszczamy je do rekordu wyłącznie, gdy należą do **zbiorów
        zamkniętych**: `tool` musi być w allowliście agenta, a `action` musi być wywoływalna
        wg rejestru dispatcha; w przeciwnym razie zapisujemy `<nieznane>`. Niezmiennika
        pilnuje `test_model_controlled_tool_name_never_reaches_the_record`.
2. **To wystarcza do celu.** Cztery weryfikatory Etapu 16 potrzebują wyłącznie metryk.
3. **Rozmiar i retencja.** Metryki to setki bajtów na przebieg; transkrypcje wymagałyby
   szyfrowania, retencji i polityki kasowania — czyli osobnego projektu.

**To nie jest telemetria.** Telemetria oznacza wysyłanie danych na zewnątrz i pozostaje
w Husarzu twardo zakazana (`platform.telemetry_enabled` odrzuca `true` przy starcie). Pomiar
to lokalny plik JSONL w `data_dir`, którego nikt poza operatorem nie widzi. Katalog jest
w `.gitignore` (zweryfikowane dla `data/runs/runs.jsonl`).

**Domyślnie wyłączone.** `platform.runs.enabled: false` — opt-in, jak pętla narzędziowa
(ADR-0016) i pamięć trwała (ADR-0018). Nowa instalacja nie zaczyna po cichu produkować
plików o pracy operatora. Fabryka `build_tool_loop` wpina wtedy `NullRunStore`, który nie
dotyka dysku.

**Zapis nie może wywrócić pracy agenta.** `JsonlRunStore.save` połyka `OSError` (brak
uprawnień, pełny dysk, kolizja ścieżki). Utrata pomiaru jest kosztem akceptowalnym; utrata
odpowiedzi agenta nie jest. Świadomie NIE budujemy tu łańcucha skrótów — to dane pomiarowe,
a rozliczalnością zajmuje się audyt, który ma tamper-evidence.

**Co sprawdzono (`tests/security/test_runs_privacy.py`, marker `security`):**

| Niezmiennik | Test | Wynik |
|---|---|---|
| Struktura nie ma pól na treść (`task`, `prompt`, `output`, `args`…) | `test_record_has_no_free_text_fields` | ✅ |
| Objętość mierzona licznikiem znaków, nie tekstem | `test_only_lengths_are_carried` | ✅ |
| Treść zadania/odpowiedzi/argumentów nie trafia do rekordu | `test_no_secret_reaches_the_record` | ✅ |
| …ani do pliku JSONL na dysku | `test_no_secret_reaches_the_file` | ✅ |
| Dostarczona konfiguracja ma pomiar wyłączony | `test_disabled_by_default_in_shipped_config` | ✅ |
| Fabryka wpina `NullRunStore`, gdy wyłączone | `test_factory_wires_null_store_when_disabled` | ✅ |

**Weryfikacja nośności testów:** do `RunRecord` dodano tymczasowo pole `task: str` i wypełniono
je treścią zadania — **3 z 7 testów czerwone**, na trzech niezależnych osiach (kontrola
strukturalna pól, zrzut rekordu, zawartość pliku). Testy nie są puste.

**Domknięte przy okazji.** Wpis audytu `toolloop.limit` jako jedyny w pętli narzędziowej nie
przekazywał `principal` — „osiągnięto limit iteracji" gubiło informację, na czyje żądanie
przebieg powstał. Poprawione. Odmowa allowlisty sygnalizowana jest teraz **strukturalnie**
(`ToolResult.metadata["denied"]`), a nie porównaniem treści komunikatu — pomiar nie może
zależeć od brzmienia napisu widzianego przez model.


### Granice walidacji airgap dla endpointów modeli (data: 2026-08-20)

**Powód notatki.** Przy ocenie zewnętrznej bramki LLM (ADR-0022) padło pytanie: czy taki
proces, postawiony obok Husarza, przeszedłby naszą walidację profilu `airgap`? **Przeszedłby.**
To zachowanie jest projektowe, ale jego konsekwencja nie była nigdzie zapisana — a operator
musi ją znać, zanim na niej polegnie.

**Stan faktyczny.** Walidacja krzyżowa `airgap` używa DWÓCH różnych progów, świadomie:

| Co | Próg | Co przepuszcza |
|---|---|---|
| modele (`models.registry[].endpoint`) | `is_local_endpoint` | loopback, **cały prywatny LAN** (RFC 1918 / ULA), `.local`, `.internal` |
| embedder narzędzia `rag` | `is_local_endpoint` | jak wyżej |
| wtyczki MCP (`plugins[].endpoint`) | `is_loopback_endpoint` | **wyłącznie** loopback |

Ostrzejszy próg dla wtyczek jest uzasadniony w kodzie: runtime konektora i tak przepuszcza
poza allowlistą tylko loopback, więc bramka startowa i runtime są spójne (ADR-0019).

**Dlaczego modele mają próg szerszy — i dlaczego to jest poprawne.** Airgap oznacza brak
trasy do WAN, nie brak sieci lokalnej. Serwer vLLM na osobnej maszynie z GPU, w tej samej
odciętej podsieci, to normalna i sensowna topologia wdrożenia. Zawężenie modeli do loopbacku
wykluczyłoby ją bez zysku bezpieczeństwa — maszyna GPU jest w tym samym obwodzie zaufania.

**Ryzyko rezydualne — nazwane wprost.** Walidator sprawdza ADRES, a nie NATURĘ usługi pod tym
adresem. Nie odróżni serwera modeli od bramki pośredniczącej. W konsekwencji:

- proces-pośrednik uruchomiony na `127.0.0.1` albo na hoście w LAN przechodzi walidację
  jako „endpoint lokalny";
- nasza bramka egress ocenia **nasz** wybór hosta, nie to, co ten host zrobi dalej;
- audyt zapisze wówczas wywołanie do adresu lokalnego, mimo że dane mogły pójść dalej.

Innymi słowy: `airgap` egzekwuje, że **Husarz** nie kieruje ruchu do WAN. Nie egzekwuje — bo
nie może — że nie robi tego oprogramowanie, któremu operator świadomie powierzył ruch. To
ograniczenie warstwy aplikacyjnej; pełne wymuszenie należy do warstwy sieciowej
(NetworkPolicy, reguły zapory), zgodnie z Etapem 6.

**Co z tego wynika dla operatora.**

1. W `airgap` wpisuj do `models.registry` wyłącznie endpointy usług, nad którymi masz
   kontrolę i których naturę znasz. Adres lokalny nie jest dowodem lokalności DANYCH.
2. Jeśli topologia tego nie wymaga, trzymaj endpointy modeli na loopbacku — próg jest
   szerszy z myślą o maszynie GPU w LAN, a nie jako zachęta.
3. Odcięcie od WAN egzekwuj na poziomie sieci. Walidacja konfiguracji jest bramką startową,
   nie zaporą.
4. Szczególna ostrożność przy embedderze `rag`: komentarz w schemacie słusznie zaznacza, że
   embeddingi bywają **odwracalne do PII**, a obowiązuje tam ten sam szerszy próg.

**Sprawdzone:** `src/husarz/config/schema.py` (modele, wtyczki, embedder), definicje progów
w `src/husarz/config/net.py`. Zachowanie zgodne z zamierzeniem; zmian w kodzie nie wprowadzono —
notatka utrwala świadomą decyzję i jej granice.


### Ekspozycja szczegółów audytu przez API (data: 2026-08-17)

**Problem.** Dziennik audytu zapisuje na dysku pełny kontekst zdarzenia (`AuditEntry.detail`) —
dla wywołania narzędzia są to `tool`, `action`, `ok`, **`args`**, `bytes` i `pinned_ip`.
`GET /api/audit` nie zwracał `detail` w ogóle, więc konsola pokazywała wiersz `tool.call` bez
nazwy narzędzia: dziennik odpowiadał „coś wywołano", ale nie „**co**". Dla platformy, której
agenci wykonują narzędzia, to luka w rozliczalności dokładnie tej klasy, co brakujący
`principal` w widoku API (Etap 13c) — funkcja istniała, ale była niewidoczna z zewnątrz.

**Rozwiązanie.** `husarz.api.audit_view.public_detail` — **allowlista, deny-by-default**:

| Akcja | Wystawiane przez API | Zostaje WYŁĄCZNIE na dysku |
|---|---|---|
| `tool.call` | `tool`, `action`, `ok` | `args`, `bytes`, `pinned_ip` |
| `tool.deny` | `tool`, `action`, `reason` | — |
| `toolloop.limit` | `max_iterations` | — |
| każda inna | *(nic)* | całość |

Cztery bariery, świadomie ustawione w tej kolejności:

1. **Deny-by-default** — akcja spoza mapy nie ujawnia nic. Nowy typ wpisu NIE zacznie wyciekać
   payloadu przez przeoczenie; trzeba go dopisać razem z testem.
2. **Allowlista kluczy, nie blocklista** — blocklista („wszystko oprócz `args`") pękłaby przy
   pierwszym nowym polu z danymi wrażliwymi.
3. **Tylko skalary** — wartość niebędąca `str`/`int`/`bool` jest odrzucana nawet pod dozwolonym
   kluczem; zagnieżdżona struktura mogłaby przemycić treść pod niewinną nazwą.
4. **Limit długości** (64 znaki) — dozwolone pole nie może być kanałem wycieku przez rozmiar.

**Dlaczego akurat te pola zostają na dysku.** `args` niesie treść pochodzącą od modelu lub
użytkownika (ścieżki, zapytania, potencjalnie materiał sekretny); `pinned_ip` ujawnia topologię
sieci operatora; `bytes` jest kanałem bocznym o rozmiarze odpowiedzi. Rola `audit:read`
odpowiada na pytanie o rozliczalność, nie daje wglądu w treść.

**Co sprawdzono (`tests/security/test_audit_view_exposure.py`, marker `security`):**

| Niezmiennik | Test | Wynik |
|---|---|---|
| `args` nigdy nie opuszczają dysku | `test_args_are_never_exposed` | ✅ |
| `pinned_ip`/`bytes` nie są wystawiane | `test_network_and_size_details_are_not_exposed` | ✅ |
| Zagnieżdżona wartość odrzucana pod dozwolonym kluczem | `test_nested_values_are_dropped_even_under_allowed_key` | ✅ |
| Długie wartości przycinane | `test_long_values_are_truncated` | ✅ |
| Akcja spoza allowlisty nie ujawnia nic | `test_unknown_action_exposes_nothing` | ✅ |
| `bool` nie degraduje się do `int` | `test_bool_keeps_its_type_not_collapsed_to_int` | ✅ |
| API pokazuje narzędzie, ale nie argumenty | `test_api_exposes_tool_name_but_not_args` | ✅ |
| Dziennik na dysku zachowuje pełny kontekst | `test_disk_log_still_carries_everything` | ✅ |

**Weryfikacja nośności testów:** allowlista tymczasowo wyłączona (`return dict(detail)`) —
8 z 13 testów czerwone, w tym wszystkie trzy o wycieku `args`. Testy nie są puste.

**Weryfikacja na żywo** (Ollama, model `husarz`, pętla narzędziowa włączona dla `bielik`):
`GET /api/audit` zwraca `{"action":"search","ok":true,"tool":"rag"}`, a ciągi `args`, `query`,
`pinned_ip` i `bytes` **nie występują w odpowiedzi API**; ten sam wpis na dysku niesie
`{"tool":"rag","action":"search","ok":true,"args":{"query":"…"},"bytes":0}`. Łańcuch skrótów
zweryfikowany. Zrzut: `docs/assets/screenshots/console-audyt.png`.

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
- ~~SSRF przez DNS rebinding wymaga pinowania IP~~ — **DOMKNIĘTE w Etapie 15** (ADR-0020):
  nazwa rozwiązywana raz, adres przypinany, `Host`/SNI po nazwie.
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
wówczas tylko obecność — **domknięte w Etapie 4b**, patrz ADR-0021); audyt bez `hmac_key` jest tamper-evident
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

**Ograniczenia:** ~~`husarz.git` nadal używa własnej, węższej walidacji hosta~~ —
**DOMKNIĘTE w Etapie 15b**: Git korzysta ze współdzielonej warstwy `husarz.ssrf`
z pinowaniem IP (ADR-0020). Egzekwowanie egress pozostaje kontrolą na poziomie aplikacji
(pełne wymuszenie sieciowe: NetworkPolicy).

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

**Ograniczenia (stan na Etap 12; pinowanie IP DOMKNIĘTE w Etapie 15 — ADR-0020):** MVP odkrywa narzędzia, nie wywołuje ich — `tools/call` z autoryzacją
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

**Ograniczenia:** „slow-drip" deadline jest sprawdzany między chunkami (ograniczenie
~`timeout` + jeden odczyt). Pełne pinowanie IP — **domknięte w Etapie 15** (ADR-0020).

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
agentów granicą jest sandbox); audyt wiąże agenta, nie użytkownika (korelacja principal↔wywołanie — follow-up). Pętla jest
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
(zachowanie i tak fail-closed: brak klucza → błąd, nie plaintext). Cykl życia połączenia
`SqliteVectorStore` jest DOMKNIĘTY (follow-up): protokoły mają `close()`, a `POST /api/config/runtime`
zamyka starą pętlę po atomowej podmianie (brak wycieku uchwytu pliku przy rekonfiguracji).
Szczegóły i alternatywy — ADR-0018.

### Etap 13b — wywołanie wtyczki MCP (`tools/call`) (data: 2026-08-14)

**Zakres:** realne wywołanie zdalnego narzędzia MCP w pętli (`kind: plugin`, akcje `list`/`call`).
Powierzchnia: egress+zdolności zdalne, `arguments` jako kanał eksfiltracji, wynik NIEZAUFANY.
Projekt z panelu (3 architektury → synteza) + adwersaryjna krytyka; wdrożone MUST-FIX (M1 audyt,
M2 airgap-loopback) i SHOULD-FIX (cap params, cap bajtowy wyniku, TOCTOU udokumentowane) — ADR-0019.

| Niezmiennik | Test |
|---|---|
| Deny-by-default: `allow_call=false` → odmowa PRZED egress (transport nietknięty) | `test_call_allow_call_false_denied_before_egress`, `test_call_allow_call_false_never_touches_network` |
| Allowlista: nazwa spoza `call_allowlist` → odmowa przed egress | `test_call_tool_outside_allowlist_denied_before_egress` |
| Config fail-closed: `allow_call=true` + pusta `call_allowlist` → błąd startu | `test_allow_call_true_empty_allowlist_rejected_at_config` |
| SSRF re-walidowany PER wywołanie (link-local/prywatne/IPv4-mapped) → `EgressError` | `test_call_ssrf_blocked_per_invocation` |
| Airgap na starcie: włączona wtyczka nielokalna → błąd (LOOPBACK, spójne z runtime) | `test_airgap_rejects_nonloopback_plugin_at_startup` |
| Sekret token WYŁĄCZNIE w nagłówku (nie w URL); `arguments` VERBATIM (env: NIE rozwiązywane) | `test_arguments_env_ref_passed_verbatim_not_resolved` |
| Cap żądania (name+arguments) PRZED egress; nieserializowalne → `PluginArgsError` | `test_call_args_too_large_raises_before_egress` |
| Wynik NIEZAUFANY: brak dereferencji `resource`/binariów; cap bajtowy tekstu | `test_parse_call_result_shapes`, `test_parse_call_result_caps_text_by_bytes` |
| Wynik ogrodzony jako DANE; `[[HUSARZ_ACTION]]` w wyniku NIE wykonane | `test_plugin_call_result_fenced_and_action_not_executed` |
| Degradacja nie crash: `PluginError`/`EgressError`/transport → `ToolResult(ok=False)` | `test_transport_error_degrades_to_ok_false`, `test_plugin_tool_denied_degrades_to_ok_false` |
| Audyt: `arguments` logowane jako `{bytes, sha256}` (nie `<dict>` — eksfiltracja wykrywalna) | (M1: `_arg_summary` w `tool_loop.py`) |

**Ograniczenia (świadome, udokumentowane w ADR-0019):** `call` to kanał egress+zdolności POZA
bramką ROE — `call_allowlist` nie ogranicza treści `arguments` ani semantyki zdalnej (opt-in
operatora, analogicznie do `web`/`shell`). TOCTOU DNS-rebinding: **domknięty w Etapie 15**
(ADR-0020 — IP przypinane per wywołanie). Puszkarz (ROE) wykluczony
z pętli i brak endpointu HTTP `call` — `call` niedosięgalne dla ROE-agenta.

### Etap 15 — pinowanie IP (anty-DNS-rebinding) (data: 2026-08-14)

**Zakres:** współdzielona warstwa `husarz.ssrf` dla obu ścieżek wychodzących sterowanych
przez model/konfigurację: narzędzie `web` i konektor MCP. Domyka ryzyko rezydualne
zapisane w ADR-0015/0016/0019: między walidacją hosta a połączeniem następowało **drugie**
rozwiązanie DNS, które atakujący kontrolujący strefę mógł podmienić (TOCTOU). Projekt
i uzasadnienie — [ADR-0020](adr/0020-pinowanie-ip-anty-ssrf.md).

| Niezmiennik | Test |
|---|---|
| Nazwa rozwiązywana DOKŁADNIE RAZ; połączenie idzie do przypiętego literału IP | `test_web_connects_to_pinned_ip_with_original_host_and_sni`, `test_plugin_connects_to_pinned_ip_and_keeps_token_out_of_url` |
| `Host` i SNI (weryfikacja certu) po ORYGINALNEJ nazwie — pin nie degraduje TLS | `test_httpx_fetcher_connects_to_ip_and_keeps_host_and_sni`, `test_httpx_plugin_transport_pins_ip_and_keeps_host_sni_and_bearer` |
| Domena z allowlisty rozwiązująca się na metadane chmury → blok przed siecią | `test_web_allowlisted_domain_resolving_to_metadata_is_blocked`, `test_plugin_domain_resolving_to_metadata_never_reaches_transport` |
| Mieszane A/AAAA z JEDNYM adresem wewnętrznym → odmowa (nie „weź czysty") | `test_mixed_records_with_one_internal_are_rejected`, `test_web_mixed_records_with_one_internal_are_blocked` |
| Nierozwiązywalna nazwa / śmieć z resolvera → fail-closed | `test_unresolvable_name_fails_closed`, `test_garbage_resolver_output_fails_closed`, `test_web_unresolvable_domain_fails_closed` |
| `web`: loopback zabroniony także PRZEZ NAZWĘ (domknięta luka `is_local_endpoint`) | `test_web_rejects_loopback_by_name_even_when_allowlisted`, `test_loopback_rejected_when_flag_unset` |
| Publiczna nazwa NIE może rozwiązać się na loopback (ochrona tokenu Bearer wtyczki) | `test_plugin_name_resolving_to_loopback_is_blocked`, `test_name_resolving_to_loopback_rejected_even_with_allow_loopback` |
| IPv4-mapped (`::ffff:169.254.169.254`) rozwijane przed klasyfikacją — bypass zamknięty | `test_parse_ip_literal_normalizes`, `test_internal_literals_rejected` |
| Pin jest ŚWIEŻY dla każdej operacji (nie cache'owany między wywołaniami) | `test_web_pin_is_fresh_for_every_fetch`, `test_plugin_pin_is_fresh_for_every_operation` |
| Odmowa allowlisty egress NIE powoduje nawet zapytania DNS (brak wycieku nazwy) | `test_plugin_denied_egress_does_not_even_resolve_dns` |
| Lokalny serwer MCP (loopback) łączy się wprost — bez DNS i bez pinu | `test_plugin_loopback_endpoint_needs_no_dns`, `test_public_literal_connects_directly_without_dns` |
| `pin_fields` nie przenosi userinfo do połączenia ani do nagłówka `Host` | `test_pin_fields_drops_userinfo` |
| `web`: ciało czytane strumieniowo z twardym sufitem (anty-OOM, było: pobierz-potem-utnij) | `test_httpx_fetcher_caps_body_at_max_bytes` |
| Chory port w URL od modelu → `ok=False`, nie surowy `ValueError` wywracający pętlę | `test_malformed_port_is_egress_error_not_raw_valueerror`, `test_web_malformed_port_degrades_to_result_not_exception` |

#### Hardening po adwersaryjnym przeglądzie (3 soczewki, 18 potwierdzonych findingów)

| Poprawka | Test |
|---|---|
| Jawna lista sieci deny — `ipaddress` NIE uznaje za prywatne: CGNAT `100.64.0.0/10` (metadane Alibaba, pule węzłów k8s), IPv6 site-local `fec0::/10`, tunele osadzające IPv4 (6to4 `2002::/16`, Teredo `2001::/32`, NAT64 `64:ff9b::/96`) | `test_internal_addresses_always_blocked`, `test_web_blocks_addresses_stdlib_calls_public`, `test_plugin_blocks_addresses_stdlib_calls_public` |
| `*.localhost` NIE jest przepustką — nazwa rozwiązywana, każdy adres musi być loopbackiem (RFC 6761 tylko ZALECA; glibc wysyła to do DNS) | `test_localhost_suffix_is_not_trusted_without_dns`, `test_plugin_localhost_suffix_must_prove_loopback_by_dns` |
| `trust_env=False` — `HTTP(S)_PROXY`/`SSLKEYLOGFILE` ze środowiska nie przekierują przypiętego połączenia (obejście pinu i deny-all) | `test_production_clients_ignore_environment_proxy_settings` |
| `UnicodeError` z kodeka `idna` (etykieta >63 znaków) łapany — brak fail-open na wyjątek | `test_resolver_unicode_error_fails_closed_not_crash` |
| `.local`/`.internal` nie omijają allowlisty egress dla `web` (niezmiennik „brak WAN" w airgapie) | `test_web_internal_suffix_does_not_bypass_egress_allowlist` |
| Komunikat odmowy NIE zawiera rozwiązanego adresu wewnętrznego (model = potencjalny skaner) | `test_denial_message_does_not_leak_resolved_internal_address` |
| Odczyt chunkami (64 KiB) — `iter_bytes()` bez `chunk_size` oddawał cały zdekompresowany blok naraz | `test_httpx_fetcher_caps_body_at_max_bytes` |
| Przekroczenie deadline'u → `FetchError` (było: ciche obcięcie z `ok=True`) | `test_web_transport_failure_degrades_to_result_not_exception` |
| `web` przyjmuje wyłącznie `http(s)://` (było: dowolny schemat) | `test_web_rejects_non_http_schemes` |
| 3xx od serwera MCP → czytelny `PluginError` (było: ciche „brak narzędzi") | `test_redirect_response_is_error_not_empty_tool_list` |
| Audyt `tool.call` zapisuje `pinned_ip` — z JAKIM adresem faktycznie się połączono | (`tool_loop.py`, metadata `web`) |

**Kod wrażliwy — czy da się go usunąć?** Nie. Bez pinowania walidacja adresu i połączenie
korzystają z DWÓCH niezależnych rozwiązań DNS, więc atakujący kontrolujący strefę wchodzi
między nie. Pinowanie **zawęża** powierzchnię (usuwa drugie rozwiązanie) i nie osłabia TLS:
`verify=True` pozostaje włączone, a certyfikat jest weryfikowany wobec nazwy przez
`sni_hostname`. Alternatywy (własny resolver w prywatnym API `httpcore`, kontrola
`getpeername` po połączeniu, cache DNS z minimalnym TTL) są słabsze lub kruche — analiza
w ADR-0020.

**Ograniczenia (świadome):** pin dotyczy JEDNEGO adresu — brak automatycznego przejścia na
kolejny rekord A przy awarii (odtworzyłoby to zamykane okno). Router modeli nie pinuje (endpointy modeli to typowo loopback/LAN
operatora). Przekierowania pozostają wyłączone. Pełne wymuszenie sieciowe (NetworkPolicy
deny-all, sandbox bez sieci) jest warstwą komplementarną, nie zastępczą.

### Etap 15b — `husarz.git` na wspólnej warstwie anty-SSRF (data: 2026-08-14)

**Zakres:** ostatnia ścieżka wychodząca poza wspólną warstwą. Stawka jest tu najwyższa
z trzech: połączenie niesie **token PAT z prawem zapisu do repozytoriów**. Poprzednia,
własna walidacja (`_is_internal_host`) blokowała tylko loopback/link-local/unspecified
dla LITERAŁÓW i **nie rozwiązywała nazw wcale** — czyli nie było ani ochrony przed
rebindingiem, ani pinu.

| Niezmiennik | Test |
|---|---|
| Domena z allowlisty rozwiązana na metadane chmury → blok, token nigdzie nie leci | `test_git_domain_resolving_to_metadata_never_reaches_transport` |
| Połączenie po PRZYPIĘTYM IP; `Host`/SNI po nazwie; token WYŁĄCZNIE w nagłówku | `test_git_connects_to_pinned_ip_and_keeps_token_in_header_only` |
| `allow_lan`: samodzielnie hostowany GitLab (RFC 1918) działa, ale loopback/metadane/CGNAT/site-local nadal blokowane | `test_git_allows_self_hosted_lan_but_not_loopback_or_metadata` |
| Loopback twardo zablokowany — także `localhost` i `*.localhost` (bez DNS) | `test_git_hard_blocks_loopback_endpoints` |
| Pin ŚWIEŻY dla każdej operacji (klient budowany per wywołanie) | `test_git_pin_is_fresh_for_every_operation` |
| Odmowa allowlisty egress NIE powoduje nawet zapytania DNS | `test_git_denied_egress_does_not_even_resolve_dns` |
| Transport: `trust_env=False`, `follow_redirects=False`, cap rozmiaru + deadline, generyczny błąd | (`HttpxGitTransport`; parytet z MCP/`web`) |
| **Cały zestaw testów jest offline** — `socket.getaddrinfo` zablokowany fixture'em autouse | `tests/conftest.py::_no_real_dns` |

**Trzecia polaryzacja — dlaczego `allow_lan` tylko tutaj.** `web` jest sterowane przez
model, a konektor MCP celuje w usługę na tej maszynie; żadne z nich nie ma powodu sięgać
LAN operatora. Git przeciwnie: samodzielnie hostowany GitLab pod adresem RFC 1918 to
podstawowy scenariusz suwerenności, a zablokowanie go wypychałoby operatora do chmury —
odwrotnie do celu projektu. Luz jest WĄSKI i jawny (`_LAN_NETWORKS` = RFC 1918 + ULA);
świadomie **nie** realizujemy go przez `ipaddress.is_private`, bo ta właściwość obejmuje
także loopback, link-local (metadane chmury) i zakresy testowe — „przepuść prywatne"
odblokowałoby wtedy dokładnie to, co ma zostać zamknięte.

#### Hardening po adwersaryjnym przeglądzie 15b (3 soczewki, 8 potwierdzonych findingów)

| Poprawka | Test |
|---|---|
| **Fail-open kill-switch**: `git_service` przebudowywany przy `POST /api/config/runtime` (zmiana polityki egress / przejście na `airgap` obowiązuje bez restartu); magazyn połączeń przekazywany, więc przebudowa nie kasuje danych | `test_git_service_rebuilt_on_runtime_override` |
| 3xx dostawcy → `GitError` (było: `[]` „brak repozytoriów" i PR z pustym URL — cicha degradacja) | `test_git_redirect_is_error_not_silent_success` |
| Pusty separator `?`/`#` w `api_base` odrzucany (bramka na WARTOŚCIACH przepuszczała własny przypadek brzegowy) | `test_git_rejects_api_base_with_query_or_fragment` |
| Chunkowany odczyt we WSZYSTKICH trzech transportach (transport MCP był pominięty przy utwardzeniu 15) | `test_all_production_transports_read_in_bounded_chunks` |
| Konstruktor transportu nie obiecuje nastawy, której kod nie honoruje (martwe `self._timeout`) | (usunięty parametr) |

**Ograniczenia:** ryzyko rezydualne `allow_lan` jest realne i świadome — operator, który
doda do `security.egress.allowlist` domenę kontrolowaną przez atakującego, może zostać
przekierowany w obręb własnej sieci (nie do metadanych chmury). Barierą pozostaje sama
allowlista (jawna decyzja operatora) oraz to, że `api_base` pochodzi z konfiguracji,
nie od modelu.

### Etap 15c — embedder pamięci i router modeli na wspólnej warstwie (data: 2026-08-15)

**Zakres:** dwie ostatnie ścieżki wychodzące. Po tym etapie **wszystkie pięć** dróg, którymi
Husarz wychodzi na sieć, korzysta z jednej implementacji anty-SSRF (`husarz.ssrf`).
Obie celują we WŁASNĄ infrastrukturę operatora (lokalny Ollama/vLLM), więc mają najbardziej
permisywną polaryzację (`allow_loopback=True`, `allow_lan=True`) — ale pin nadal blokuje
metadane chmury i zakresy infrastrukturalne, czyli miejsca, w których wylądowałby **klucz API
modelu** albo **wektor embeddingu** (odwracalny do PII).

| Niezmiennik | Test |
|---|---|
| Nazwa endpointu embeddera rozwiązana na metadane chmury → blok, wektor NIE wychodzi | `test_embedder_blocks_name_resolving_to_metadata` |
| Loopback (domyślny Ollama) bez DNS; LAN operatora przypinany normalnie | `test_embedder_local_and_lan_endpoints_work` |
| Endpoint modelu rozwiązany na metadane → `ModelBackendError`, transport nietknięty (klucz API nie leci) | `test_model_endpoint_resolving_to_metadata_is_blocked` |
| Połączenie do modelu po PRZYPIĘTYM IP; `Host`/SNI po nazwie | `test_model_endpoint_is_pinned_with_host_and_sni` |
| `trust_env=False` w obu transportach (proxy z ENV nie przekieruje pinu) | (`HttpxTransport`, `HttpxEmbeddingTransport`) |
| Odczyt embeddera chunkowany z twardym sufitem (anty-OOM) | (parytet z pozostałymi transportami) |

**Zmiana ubocza (poprawka wycieku):** `HttpxTransport` routera echował w błędzie pełny URL
i wnętrzności httpx (`f"Błąd HTTP przy {url}: {exc}"`), a komunikat trafia przez
`ModelBackendError` do odpowiedzi API i audytu. Teraz jest generyczny — parytet z pozostałymi
czterema transportami.

**Ograniczenia:** router nie przewleka `resolve` przez `ModelRouter` — wstrzyknięcie w testach
idzie istniejącym szwem `client_factory`. Endpointy modeli są konfiguracją operatora (nie są
sterowane przez model), więc powierzchnia ataku jest tu najwęższa z pięciu ścieżek.

### Etap 4b — kryptograficzny podpis ROE (data: 2026-08-15)

**Zakres:** domknięcie ostatniej otwartej pozycji rdzenia bezpieczeństwa (Etap 4).
ROE to JEDYNY artefakt uprawniający Puszkarza do aktywnych działań wobec konkretnych celów,
a jego ważność sprowadzała się do „pole `signature` jest niepuste" — czyli dopisanie
`signature: "abc"` czyniło zlecenie ważnym. Kto mógł edytować plik, mógł poszerzyć zakres;
skutkiem byłby **atak na osobę trzecią z użyciem Husarza jako narzędzia**. Projekt i analiza
alternatyw — [ADR-0021](adr/0021-podpis-roe.md).

> **Stan wpięcia:** ryzyko było **utajone**, nie żywe — orkiestrator twardo pomija agentów
> `roe_required` (`SKIPPED_ROE`), więc `RoeGate` nie jest jeszcze ścieżką runtime. Prymityw
> autoryzacji domykamy ZANIM bramka trafi do produkcji.

| Niezmiennik | Test |
|---|---|
| Podpis obejmuje całą treść autoryzacyjną — zmiana dowolnego pola go unieważnia | `test_any_field_change_invalidates_signature` |
| **Poszerzenie zakresu** (dopisanie CIDR) → podpis nieważny | `test_scope_widening_invalidates_signature` |
| **Usunięcie `out_of_scope`** (odsłonięcie hosta krytycznego) → podpis nieważny | `test_removing_out_of_scope_exclusion_invalidates_signature` |
| **Wydłużenie okna** i **podniesienie `consent`** → podpis nieważny | `test_window_extension_invalidates_signature`, `test_raising_consent_invalidates_signature` |
| Dawny „podpis" (dowolny tekst, np. `abc`) → NIEWAŻNY | `test_malformed_signature_is_denied_not_crash` |
| Zły format/base64/algorytm → ODMOWA, nie wyjątek wywracający bramkę | `test_malformed_signature_is_denied_not_crash` |
| Downgrade-guard: algorytm z pliku musi zgadzać się z konfiguracją | `test_algorithm_downgrade_is_denied` |
| Zły klucz (HMAC i Ed25519) → odmowa | `test_wrong_key_does_not_verify`, `test_ed25519_other_key_does_not_verify` |
| `verify_signature=true` bez `key_ref` / bez dostawcy sekretów → **błąd startu** | `test_verifier_enabled_without_key_ref_fails_closed_at_startup`, `test_verifier_enabled_without_secrets_provider_fails_closed` |
| Nierozwiązywalny klucz w runtime → `RoeSignatureError` (nigdy „przepuść") | `test_verifier_unresolvable_key_raises_never_passes` |
| Klucz rozwiązywany LENIWIE przy każdej weryfikacji (rotacja bez restartu) | `test_verifier_resolves_key_lazily_on_each_call` |
| Bramka honoruje weryfikator: podrobiony podpis → `roe.deny` w audycie | `test_gate_denies_when_signature_invalid` |
| Profil `prod`/`airgap` z aktywnym zleceniem wymaga weryfikacji + `key_ref` | `test_prod_with_consented_roe_requires_signature_verification` |
| Sam szablon (`consent: false`) NIE wymusza klucza (brak zbędnej friction) | `test_prod_without_consented_roe_does_not_require_key` |
| Narzędzie operatora domyka pętlę: `roe sign` → wklejenie → `roe verify` = 0 | `test_cli_roe_sign_then_verify_round_trip` |

#### Hardening po adwersaryjnym przeglądzie 4b (3 soczewki, 12 potwierdzonych findingów)

| Poprawka | Test |
|---|---|
| **FAIL-OPEN**: niewyrównany CIDR w `out_of_scope` (`192.0.2.5/29`) był CICHO ignorowany — wykluczenie znikało, czyli zakres się POSZERZAŁ, a podpis obejmował taki dokument jako ważny | `test_out_of_scope_malformed_entry_is_rejected_at_config`, `test_out_of_scope_exclusion_actually_blocks_after_validation` |
| Wpis zakresu normalizowany tak samo jak cel (białe znaki, kropka końcowa FQDN, port/schemat, wielkość liter) — różnica w zapisie nie może decydować o autoryzacji | `test_scope_entry_is_normalized_like_target` |
| **Kotwica profilu**: `POST /api/config/runtime` nie może nadpisać `platform.profile` — jedno żądanie degradowało prod→dev, wyłączając naraz wymagania sandboxa, audytu, szyfrowania i podpisu ROE | `test_runtime_override_cannot_downgrade_profile` |
| `husarz up` bez `--profile` NIE nadpisuje profilu (domyślne `dev` po cichu degradowało `profile: prod` z pliku) | `test_up_subcommand_is_wired` |
| `HUSARZ_SECURITY__ROE__*` działa — `roe` jest też nazwą kolekcji zleceń, więc segment trafiał jako klucz mapy i nie dopasowywał się do pola schematu | `test_env_override_reaches_security_roe_section` |
| `roe sign --algorithm` niezgodny z configiem → błąd (runtime i tak odrzuciłby taki podpis) | (`_cmd_roe_sign`) |
| `roe sign` ostrzega, gdy działają nadpisania `HUSARZ_ROE__*` (podpis obejmuje treść EFEKTYWNĄ) | (`_cmd_roe_sign`) |
| Stan weryfikacji podpisu widoczny w `husarz validate` (wyłączona weryfikacja nie może być niewidoczna) | (`_roe_signature_status`) |
| Błąd weryfikatora (np. zniknął sekret) → odmowa Z WPISEM w audycie, nie wyjątek z `evaluate` | `test_gate_denies_and_audits_when_key_is_unresolvable` |

**Kod wrażliwy (kryptografia) — czy da się go usunąć?** Nie. Bez weryfikacji podpisu
autoryzacją jest dowolny tekst, a bramka ROE — jedyne zabezpieczenie przed użyciem Husarza
przeciwko celom bez zgody — opiera się na dokumencie, którego integralności nikt nie sprawdza.
Alternatywy słabsze (hash pliku nie jest podpisem i nie wykrywa nadpisań runtime; detached
signature to drugi artefakt do rozjechania) — analiza w ADR-0021. Porównanie HMAC jest
stałoczasowe, klucz prywatny Ed25519 **nigdy nie trafia do runtime'u** (podpisuje operator).

**Ograniczenia (świadome):** `RoeGate` nie jest jeszcze wpięty w runtime — ta warstwa zacznie
chronić realny przepływ dopiero wtedy. Klucze prywatne chronione hasłem oraz rotacja/
wersjonowanie kluczy pozostają do zrobienia (dziś jeden `key_ref`; rotacja unieważnia
wszystkie podpisy, co jest właściwością podpisów, nie usterką).

### Etap 4c — wpięcie ROE-gate w runtime orkiestratora (data: 2026-08-15)

**Zakres:** domknięcie ostatniego kroku Etapu 4. Do tej pory `RoeGate` był kompletny
i przetestowany, ale **nieużywany**: orkiestrator twardo pomijał każdego agenta
z `roe_required`, więc ani bramka, ani weryfikacja podpisu (Etap 4b) nie miały konsumenta.
Teraz podpis jest **nośny** — decyduje o tym, czy Puszkarz w ogóle zostanie zadelegowany.

**Co to NIE jest.** Wpięcie nie nadaje żadnej nowej zdolności ofensywnej. Puszkarz nie ma
narzędzi: pętla narzędziowa wyklucza agentów `roe_required` na poziomie L0, więc nawet pod
ważnym zleceniem agent wytwarza wyłącznie analizę tekstową, w trybie dry-run. Zmiana brzmi:
„Puszkarz nie działa nigdy" → „Puszkarz działa wyłącznie pod kryptograficznie zweryfikowanym
zleceniem, bez narzędzi, w dry-run".

**Poziom orkiestracji ≠ poziom celu.** Bramka na delegacji odpowiada na pytanie „czy istnieje
ważne zlecenie", a nie „czy wolno zaatakować cel X". Rozróżnienie jest celowe: zadanie kroku
planu to wolny tekst od modelu, więc wyłuskiwanie z niego celu i techniki oznaczałoby
autoryzację **sterowaną przez model** — dokładnie to, przed czym ROE ma chronić. Autoryzacja
na cel pozostaje w `RoeGate.evaluate` i obowiązuje, gdy pojawi się konkretny cel.

| Niezmiennik | Test |
|---|---|
| Brak wpiętego runtime'u ROE → agent pominięty (brak konfiguracji ≠ zgoda) | `test_orchestrator_skips_roe_agent_without_runtime` |
| **Podrobiony podpis → odmowa delegacji** (podpis jest nośny) | `test_forged_signature_denies_delegation` |
| Zgoda bez podpisu / poza oknem czasowym → odmowa | `test_unsigned_engagement_denies_delegation`, `test_outside_window_denies_delegation` |
| Brak jakiegokolwiek zlecenia → odmowa ze śladem w audycie | `test_no_engagements_denies_delegation` |
| Odmowa wytwarzania ofensywy jest BEZWARUNKOWA — ważne zlecenie jej nie znosi | `test_offensive_request_refused_even_with_valid_engagement` |
| ...i działa też, gdy nie ma żadnego zlecenia (bramka `None`) | `test_offensive_request_refused_without_any_engagement` |
| Pod ważnym zleceniem: delegacja + notatka dry-run w kontekście agenta | `test_orchestrator_delegates_under_valid_engagement_with_dry_run_note` |
| Agenci bez `roe_required` nie są bramkowani (zero regresji) | `test_orchestrator_does_not_gate_normal_agents` |
| Runtime przebudowywany przy `POST /api/config/runtime`; błąd (np. zgoda bez klucza) → `ok=false`, config NIE stosowany | (`_build_stack`, `config_apply`) |

**Notatka dry-run w kontekście.** Agentowi wstrzykiwana jest instrukcja, że działa w dry-run
i nie ma narzędzi. Bez tego model mógłby raportować „wykonałem skan", którego nie wykonał —
a taki wynik trafiłby do syntezy hetmana jako fakt.

**Ograniczenia (świadome):** weryfikator budowany jest tylko, gdy istnieje zlecenie ze zgodą
(`consent: true`) — szablon bez zgody i tak nie przejdzie `is_active`, więc żądanie klucza od
wdrożeń bez testów byłoby friction bez zysku. Autoryzacja NA CEL nie ma dziś konsumenta
(Puszkarz nie wykonuje akcji); pojawi się wraz z nadaniem mu zdolności wykonawczych — i wtedy
`RoeGate.evaluate` (zakres, techniki, `--authorized`) jest już gotowe i pokryte testami.

### Etap 13c — korelacja principal↔wywołanie w audycie (data: 2026-08-15)

**Zakres:** domknięcie otwartej pozycji z Etapu 13. Dziennik odpowiadał na pytanie „kto
WYKONAŁ" (`actor`: `kopijnik`, `puszkarz`, `api`), ale nie „na czyje ŻĄDANIE". Przy jednym
operatorze to bez znaczenia, ale przy wielu kontach audyt przestaje być śladem
**rozliczalności**: widać, że agent uruchomił `shell`, lecz nie widać, kto go o to poprosił.

| Niezmiennik | Test |
|---|---|
| `principal` jest objęty łańcuchem skrótów — podmiana wpisu unieważnia go | `test_principal_is_covered_by_hash_chain` |
| Usunięcie `principal` (odpięcie wywołania od użytkownika) też jest wykrywane | `test_stripping_principal_is_detected` |
| **Zgodność wstecz**: dzienniki sprzed zmiany nadal przechodzą `verify` | `test_legacy_entries_without_principal_still_verify` |
| Wpisy z principalem i bez mieszają się w łańcuchu bez fałszywego alarmu | `test_chain_continues_across_mixed_entries` |
| Referencja to ID konta, NIE nazwa użytkownika (brak PII w logu) | `test_principal_ref_uses_account_id_not_username` |
| Token maszynowy odróżnialny od wywołania człowieka (`token:<rola>`) | `test_machine_token_is_distinguishable_from_user` |
| Wpisy z GŁĘBI orkiestracji niosą wywołującego (nie tylko wpis wejściowy) | `test_orchestration_audit_entries_carry_caller` |

**Dlaczego pole jest opcjonalne w payloadzie.** `principal` trafia do hashowanego payloadu
wyłącznie, gdy jest niepusty. Ma to dwie konsekwencje i obie są zamierzone: stare dzienniki
hashują się bez zmian (aktualizacja Husarza nie może sprawić, że cała historia wygląda na
zmanipulowaną), a jednocześnie każda ingerencja w to pole — dopisanie albo usunięcie —
zmienia payload i psuje skrót. Nie ma tu luki „usuń pole, żeby przeszło".

**Dlaczego ID konta, a nie nazwa.** Dziennik jest z założenia niemodyfikowalny, więc nie
wkładamy do niego danych, których nie da się usunąć, a które mogą być PII (nazwa bywa
e-mailem). Identyfikator konta jest losowy i wystarcza do powiązania z użytkownikiem przez
magazyn kont — spójnie z resztą audytu, gdzie zapisujemy skróty i rozmiary, nie treść.

**Weryfikacja na uruchomionej aplikacji.** Zestaw testów przepuścił dwie luki, które wyszły
dopiero po realnym starcie serwera: widok `/api/audit` nie zwracał `principal` (testy API
sprawdzały wpisy w obiekcie `AuditLog`, nie odpowiedź HTTP), a `husarz up` bez `--config`
nie przekazywał katalogu konfiguracji, przez co `POST /api/config/runtime` kończył się
wcześniej i kotwica profilu nigdy nie była testowana w realnym uruchomieniu. Obie domknięte
i pokryte regresją (`test_api_audit_view_exposes_principal`,
`test_up_passes_resolved_config_dir_to_app`).

**Ograniczenia:** korelacja obejmuje ścieżki przechodzące przez API (czat, orkiestracja,
config, Git, wtyczki). Wywołania biblioteczne (np. `Orchestrator.run` wprost z kodu) mają
`principal=""` — to poprawne, bo nie ma wtedy uwierzytelnionego wywołującego.

### Etap 17 — zapisywalny magazyn sekretów i kreator połączeń (data: 2026-08-22)

**Co doszło.** `husarz.security.secret_store` — pierwsze miejsce w projekcie, w którym Husarz
**przyjmuje** materiał sekretu, zamiast wyłącznie rozwiązywać referencje do materiału
umieszczonego gdzie indziej przez operatora. Powierzchnia jest nowa i wymaga jawnego opisu.

**Po co ten kod istnieje.** Bez niego token wklejony w konsoli nie miał gdzie trafić: albo
do pliku konfiguracji (złamanie niezmiennika „config nie zawiera materiału"), albo tylko do
pamięci procesu (utrata po restarcie, a w praktyce obejście — operator i tak wpisze token
gdzieś na stałe). Uzasadnienie i odrzucone alternatywy:
[ADR-0023](adr/0023-zapisywalny-magazyn-sekretow.md).

**Czy da się go usunąć.** Nie bez usunięcia funkcji. Da się natomiast **nie włączać** —
magazyn jest domyślnie wyłączony, a instalacja korzystająca z Vaulta czy SOPS-a nie ma
powodu go włączać. To zalecana konfiguracja tam, gdzie zarządzanie sekretami już istnieje.

**Co go chroni (defense-in-depth).**

| Warstwa | Mechanizm |
|---|---|
| Poufność na dysku | AES-256-GCM, DEK z klucza głównego spoza magazynu |
| Integralność i anti-swap | `AAD` = nazwa wpisu; przeniesienie szyfrogramu pod inną nazwę unieważnia tag |
| Nierozróżnialność | losowy nonce per zapis — dwa równe sekrety dają różne szyfrogramy |
| Prawa systemu plików | plik `0600` w katalogu `0700`, nadane przez `os.open` **przy tworzeniu** |
| Atomowość | zapis do pliku tymczasowego + `os.replace`; brak stanu połowicznego |
| Fail-closed przy starcie | brak/nierozwiązywalny `key_ref` = magazyn NIE powstaje; nie ma trybu zapisu jawnego |
| Fail-closed przy odczycie | uszkodzony plik to błąd, nie „pusty magazyn" (inaczej awaria wyglądałaby jak wygaśnięcie tokenu) |
| Ograniczenie kręgu zaufania | `secret_store.key_ref` nie przyjmuje schematu `husarz:` — magazyn nie odblokuje się własnym sekretem |
| Higiena wyjścia | token nie występuje w modelu odpowiedzi, w audycie ani w komunikatach błędów (**pierwotne brzmienie tego wiersza było nieprawdziwe** — patrz sprostowanie w „Etap 17c") |

**Osobne ryzyko: echo wartości w błędzie walidacji.** Domyślna obsługa
`RequestValidationError` w FastAPI zwraca odrzuconą wartość w polu `input`. Gdyby pole
`token` miało ograniczenie `max_length` Pydantica, przekroczenie limitu odesłałoby token
w treści odpowiedzi 422 — a stamtąd trafiłby do dziennika dostępu serwera. Dlatego pole jest
**celowo bez ograniczeń Pydantica**, a długość i pustkę sprawdza endpoint, zgłaszając
komunikat, który wartości nie powtarza.

!!! danger "Sprostowanie: powyższe zamykało JEDEN wariant z sześciu"
    Rezygnacja z ograniczeń na polu `token` nie zamyka kanału `input` — zamyka wyłącznie
    ten wariant, w którym błąd dotyczy samego pola `token`. Pięć innych dróg działało dalej.
    Właściwą bramką jest handler `RequestValidationError` na poziomie aplikacji; opis
    i lista wariantów: „Etap 17c" niżej.

**Kolejność operacji jako kontrola bezpieczeństwa** (uzupełnione w Etapie 17c: sama
kolejność NIE wystarcza pod współbieżnością — potrzebny jest zamek). Kreator sprawdza kolizję nazwy
**przed** zapisem sekretu. Naiwna kolejność (zapisz → dodaj połączenie → posprzątaj po
błędzie) przy zajętej nazwie nadpisałaby token istniejącego połączenia, a sprzątanie
skasowałoby go zupełnie — cicha utrata działającego poświadczenia. Wykryte i domknięte
w trakcie implementacji, pokryte regresją.

**Usuwanie sekretu przy usuwaniu połączenia** dotyczy wyłącznie referencji `husarz:git/<ta
sama nazwa>`. Sekret wskazany przez `env:`/`vault:` nie jest własnością Husarza — operator
mógł go użyć także gdzie indziej — i nie jest ruszany.

| Niezmiennik | Test |
|---|---|
| Materiał NIE występuje jawnie w pliku magazynu | `test_zapisany_token_nie_wystepuje_jawnie_w_pliku` |
| Materiał NIE występuje w pliku połączeń | `test_token_nie_trafia_do_pliku_polaczen` |
| Materiał NIE występuje w dzienniku audytu | `test_token_nie_trafia_do_dziennika_audytu` |
| Materiał NIE występuje w ŻADNYM pliku powstałym podczas operacji | `test_token_nie_wycieka_do_zadnego_artefaktu_na_dysku` |
| Odpowiedź HTTP niesie referencję, nie token | `test_odpowiedz_zawiera_referencje_a_nie_token` |
| Komunikat o za długim tokenie nie powtarza wartości | `test_za_dlugi_token_nie_wraca_w_komunikacie_bledu` |
| Zły klucz główny → `None`, nie śmieci i nie wyjątek | `test_zly_klucz_glowny_nie_odszyfrowuje` |
| Podmiana szyfrogramu pod inną nazwę jest wykrywana (AAD) | `test_podmiana_wpisu_pod_inna_nazwe_jest_wykrywana` |
| Prawa `0600` / `0700` po realnym zapisie | `test_prawa_pliku_i_katalogu_sa_wlasciwe` |
| Zapis nie zostawia pliku tymczasowego z szyfrogramem | `test_po_zapisie_nie_zostaje_plik_tymczasowy` |
| Uszkodzony plik nie udaje pustego magazynu | `test_uszkodzony_plik_nie_udaje_pustego_magazynu` |
| Bez klucza głównego magazyn nie powstaje | `test_brak_klucza_glownego_blokuje_budowe` |
| Ten sam sekret dwa razy → różne szyfrogramy (losowy nonce) | `test_ten_sam_sekret_dwa_razy_daje_rozne_szyfrogramy` |
| Wyłączony magazyn = odmowa, nie cichy zapis | `test_bez_magazynu_kreator_odmawia_zamiast_zapisac_gdziekolwiek` |
| Kolizja nazwy nie niszczy istniejącego sekretu | `test_kolizja_nazwy_nie_niszczy_istniejacego_sekretu` |
| Usunięcie połączenia nie rusza referencji zewnętrznej | `test_usuniecie_polaczenia_nie_rusza_referencji_zewnetrznej` |

**Nośność testów sprawdzona.** Trzynaście mutacji kodu (stałe `AAD`, prawa `0644`/`0755`,
zapis jawny zamiast szyfrowania, uszkodzony plik traktowany jak pusty, przepuszczony brak
klucza, audyt zapisujący token, komunikat echujący token, usunięty pre-check kolizji,
kasowanie sekretu bez sprawdzenia właściciela) — **każda** czerwieni odpowiedni test.
Testy chronią mechanizm, a nie tylko go opisują.

**Weryfikacja na uruchomionej aplikacji.** Kreator przeszedł pełny obieg na realnej instancji
(`husarz up`, profil dev): dodanie połączenia, odczyt stanu magazynu, restart procesu,
usunięcie połączenia. Sprawdzone na artefaktach z dysku, nie na deklaracjach:

- token nie występuje jawnie w `data/`, `audit/` ani w logu serwera (przeszukanie całego drzewa),
- plik magazynu zawiera szyfrogram base64, prawa `-rw-------`, katalog `drwx------`,
- plik połączeń zawiera wyłącznie `husarz:git/<nazwa>`,
- ten sam plik odszyfrowany właściwym kluczem zwraca token bajt w bajt; **innym kluczem — `None`**,
- po restarcie procesu wpis i połączenie są nadal obecne i rozwiązywalne,
- druga instancja z wyłączonym magazynem odmawia (HTTP 409) i **nie tworzy żadnego pliku**.

**Ograniczenia — wprost.**

1. **Siła magazynu = ochrona klucza głównego.** Klucz w ENV obok pliku magazynu chroni kopie
   zapasowe i wyniesiony dysk, ale nie napastnika działającego już na koncie operatora.
   Vault daje realną separację. Sekret jest z definicji odszyfrowywalny przez sam proces
   Husarza — żadna konstrukcja tego nie zmieni.
2. **DEK wyprowadzany przez SHA-256, nie przez KDF z solą i rozciąganiem.** Poprawne dla
   materiału z dostawcy sekretów (klucz losowy). Gdyby kiedykolwiek dopuścić **hasło
   operatora** jako źródło, `derive_key` MUSI zostać zastąpione przez scrypt/Argon2.
3. **Brak rotacji i wygasania.** Ponowny zapis pod tą samą nazwą zastępuje wartość i to cała
   dostępna dziś „rotacja"; nic nie przypomina, że token dostawcy wygasa.
4. **Nie weryfikowano** zachowania na systemie plików bez atomowego `rename` (niektóre udziały
   sieciowe) ani współbieżnego zapisu z dwóch procesów Husarza wskazujących ten sam plik.
   Drugi przypadek jest z założenia niewspierany — magazyn ma być jedną instancją na proces.

### Etap 17b — własne CA dla połączeń Git (data: 2026-08-22)

**Co doszło.** Pole `ca_bundle` na połączeniu Git: ścieżka do pliku PEM z certyfikatem urzędu,
który podpisał certyfikat samodzielnie hostowanego GitLaba. Bez tego taka instancja była
nieosiągalna — udokumentowane wcześniej jako ograniczenie, teraz domknięte.

**Kluczowa decyzja: zaufanie ZAWĘŻONE, nie rozszerzone.** `ssl.create_default_context(cafile=…)`
ładuje wyłącznie wskazany plik — magazyn systemowy przestaje obowiązywać **dla tego jednego
połączenia**. Odwrotna semantyka (dołożenie do magazynu systemowego, jak robi `SSL_CERT_FILE`)
byłaby wygodniejsza i **wyraźnie gorsza**: prywatny urząd zyskałby prawo poświadczania
dowolnego hosta na wszystkich ścieżkach wychodzących, więc jego przejęcie pozwoliłoby podszyć
się pod `api.github.com`. Zawężenie kosztuje operatora tyle, że bundle musi zawierać pełny
łańcuch — i to jest właściwa cena.

**Czego to pole NIE robi.** Nie wyłącza weryfikacji i nie ma przełącznika „ignoruj błędy
certyfikatu". Kontekst zachowuje `check_hostname=True` i `verify_mode=CERT_REQUIRED`, więc
własne CA nie jest tylnymi drzwiami do akceptowania dowolnego hosta podpisanego przez ten
urząd. Przy przypiętym adresie IP weryfikacja nadal idzie po NAZWIE (`sni_hostname`) —
pin nie degraduje TLS, a CA tego nie zmienia.

**Fail-closed przy błędnej ścieżce.** Nieistniejący plik, katalog albo plik, który nie jest
zbiorem certyfikatów, dają błąd **przy dodawaniu połączenia** (HTTP 400). Cicha degradacja do
CA systemowych byłaby gorsza niż błąd: operator dostałby niepowiązany błąd TLS przy pierwszej
operacji. Komunikat NIE zawiera treści pliku — operator może omyłkowo wskazać klucz prywatny,
a komunikat trafia do odpowiedzi API.

| Niezmiennik | Test |
|---|---|
| Własne CA **zastępuje** magazyn systemowy, a nie go rozszerza | `test_wlasne_ca_ZASTEPUJE_magazyn_systemowy_a_nie_go_rozszerza` |
| Urząd, którego nie wskazano, nie jest zaufany | `test_obce_ca_nie_jest_zaufane` |
| `check_hostname` i `CERT_REQUIRED` pozostają włączone | `test_kontekst_zachowuje_bezpieczne_ustawienia` |
| Nieistniejąca ścieżka to błąd, nie cicha degradacja | `test_nieistniejaca_sciezka_jest_bledem_a_nie_cicha_degradacja` |
| Komunikat błędu nie ujawnia zawartości pliku | `test_komunikat_bledu_nie_ujawnia_zawartosci_pliku` |
| **Realne uzgodnienie TLS przechodzi z własnym CA** | `test_polaczenie_z_wlasnym_ca_dochodzi_do_skutku` |
| **To samo połączenie BEZ CA zawodzi** (dowód skutku) | `test_bez_wlasnego_ca_to_samo_polaczenie_ZAWODZI` |
| Poprawny kontekst z niewłaściwym urzędem też zawodzi | `test_obce_ca_nie_wystarcza` |
| Certyfikat na inną nazwę jest odrzucany mimo zaufanego CA | `test_wlasne_ca_nie_wylacza_weryfikacji_nazwy_hosta` |
| Połączenia zapisane przed zmianą nadal się wczytują | `test_polaczenia_zapisane_przed_zmiana_nadal_sie_wczytuja` |

**Test mutacyjny wykrył realną lukę w pokryciu — i tak powstał test integracyjny.** Sześć
mutacji czerwieniło odpowiednie testy, ale siódma — **podmiana `verify=self._ssl_context` na
`verify=True` w samym transporcie** — przechodziła przez CAŁY zestaw na zielono. Cały łańcuch
był sprawdzony (kontekst budowany poprawnie, wstawiany do transportu), a ostatnie ogniwo,
to które faktycznie decyduje, nie było sprawdzone wcale. Dokładnie ten wzorzec — weryfikacja
deklaracji zamiast skutku — przepuścił w tym projekcie sześć wcześniejszych wad.

Domknięte testem `tests/integration/test_git_ca_bundle_tls.py`: podnosi PRAWDZIWY serwer TLS
na loopbacku z certyfikatem podpisanym przez wygenerowany na miejscu urząd i wykonuje
PRAWDZIWE uzgodnienie. Dowodem nie jest to, że połączenie z własnym CA działa — dowodem jest
to, że **bez niego zawodzi**. Po dodaniu tego testu mutacja `verify=True` czerwieni zestaw.

Test omija `build_provider`, bo bramka egress twardo blokuje loopback dla Gita (i słusznie);
konstruuje `PinnedTarget` ręcznie. Globalny bezpiecznik DNS z `tests/conftest.py` zostaje
w mocy dla całego zestawu — ten jeden test przepuszcza wyłącznie `127.0.0.1`/`::1`.

**Ograniczenia — wprost.**

1. **Zakres jest wąski: tylko integracja Git.** Pozostałe cztery ścieżki wychodzące (`web`,
   konektory MCP, embedder pamięci, router modeli) nadal używają wyłącznie magazynu
   systemowego. Samodzielnie hostowany vLLM albo serwer MCP za prywatnym CA pozostaje
   nieosiągalny po HTTPS. Ujednolicenie: ROADMAP.
2. **Ścieżka wskazuje plik na maszynie Husarza**, nie treść certyfikatu w konfiguracji.
   Przy wdrożeniu w kontenerze plik trzeba zamontować.
3. **Nie weryfikowano** zachowania z urzędem pośrednim w osobnym pliku ani z certyfikatem
   odwołanym (CRL/OCSP — Python domyślnie ich nie sprawdza, co jest zachowaniem sprzed tej
   zmiany i jej nie dotyczy).

### Etap 17c — sprostowania po przeglądzie adwersaryjnym (data: 2026-08-22)

Commit 5f4039d (Etap 17) przeszedł adwersaryjny przegląd: pięć niezależnych soczewek nad tą
samą zmianą, każde zgłoszenie oceniane potem przez dwóch sceptyków (jeden próbuje OBALIĆ,
drugi ODTWORZYĆ awarię). Zgłoszeń było dwadzieścia, pięć trafiło do weryfikacji, wszystkie
pięć potwierdzono uruchomieniem kodu. **Trzy z nich to wady wprowadzone albo utrwalone przez
tamten commit**, a dwie z tych trzech dotyczyły twierdzeń, które sam ten dokument zawierał.

Zapisujemy to jawnie, bo dokumentacja, której nie można ufać w jednym miejscu, przestaje być
wiarygodna w całości.

#### 1. Sprostowanie: „token nie występuje w komunikatach błędów" było NIEPRAWDĄ

Tabela obrony w sekcji „Etap 17" twierdziła, że materiał tokenu nie wraca w komunikatach
błędów, a akapit o echu w błędzie walidacji uznawał sprawę za zamkniętą przez rezygnację
z ograniczeń Pydantica na polu `token`. **To zamykało jeden wariant z sześciu.**

Domyślna obsługa `RequestValidationError` w FastAPI zwraca `exc.errors()`, a każdy wpis niesie
`input` z odrzuconą wartością. Brak ograniczeń na polu `token` sprawia jedynie, że dla TEGO
pola nie da się wywołać błędu. Odtworzone warianty, w których token wracał w ciele 422:

| Wariant | Co wracało w `input` |
|---|---|
| brak innego wymaganego pola (np. `name`) | CAŁE ciało żądania wraz z tokenem |
| literówka w nazwie pola (`token_ref` zamiast `token`) | CAŁE ciało żądania |
| `Content-Type: application/x-www-form-urlencoded` (zwykłe `curl -d`) | surowe ciało jako napis |
| ciało jako lista JSON | cała lista |
| **surowy token wklejony w `token_ref`** na `POST /api/git/connections` | sam token |

Ostatni wariant jest w praktyce najbardziej prawdopodobny i **istniał przed Etapem 17** —
nowy był wyłącznie fałszywy zapis, że kanał jest zamknięty. Konsola podbija jego szansę: jedno
pole przełącza się między trybem „wklej token" a „podaj referencję", a przy wyłączonym
magazynie tryb jest wymuszany na referencję. Operator ma wtedy token w schowku i pole, które
go przyjmie.

**Naprawa.** Handler `RequestValidationError` zarejestrowany dla CAŁEJ aplikacji zwraca
wyłącznie `type`, `loc` i `msg`. Wołający wie, GDZIE i CO jest nie tak, ale nie dostaje
z powrotem tego, co wysłał. Bramka na poziomie aplikacji, a nie pojedynczego pola, obejmuje
także endpointy, które dopiero powstaną. Brak ograniczeń na polu `token` zostaje jako druga
warstwa.

**Waga.** Przegląd zgłosił „krytyczna", sceptyk skorygował na średnią i ta korekta jest
słuszna: endpointy są za `git:write`, więc sekret wraca do tego, kto go właśnie wysłał — to
odbicie własnego wejścia, nie wyciek między użytkownikami. Panel nie renderuje wartości
(`detail` jest tablicą, więc konkatenacja daje `[object Object]`). Realne drogi to zakładka
sieciowa w przeglądarce, pośrednik logujący ciała odpowiedzi i zrzut z narzędzi deweloperskich
dołączony do zgłoszenia błędu. Nasze wcześniejsze uzasadnienie mówiło o „dzienniku dostępu
serwera" — typowy `access log` nginksa czy Caddy'ego ciał odpowiedzi NIE zapisuje, więc ten
argument był słabszy, niż go przedstawiono.

| Niezmiennik | Test |
|---|---|
| Brak innego wymaganego pola nie odsyła tokenu | `test_brak_innego_wymaganego_pola_nie_odsyla_tokenu` |
| Literówka w nazwie pola nie odsyła tokenu | `test_literowka_w_nazwie_pola_nie_odsyla_tokenu` |
| Ciało formularzowe nie odsyła tokenu | `test_cialo_formularzowe_nie_odsyla_tokenu` |
| Ciało jako lista nie odsyła tokenu | `test_cialo_jako_lista_nie_odsyla_tokenu` |
| Surowy token w polu referencji nie wraca | `test_surowy_token_w_polu_referencji_nie_wraca` |
| `input` i `ctx` zniknęły z KAŻDEGO wpisu (kontrola strukturalna) | `test_pole_input_zniklo_z_kazdego_wpisu` |
| Komunikat nadal mówi, co jest nie tak (nośność) | `test_komunikat_nadal_mowi_co_jest_nie_tak` |

#### 2. Sprostowanie: pre-check kolizji NIE chronił pod współbieżnością

Sekcja „Etap 17" opisywała sprawdzenie kolizji nazwy przed zapisem sekretu jako kontrolę
bezpieczeństwa chroniącą przed cichą utratą poświadczenia. Jest to wzorzec **check-then-act**
i pod współbieżnością nie chroni niczego — a chronić miał właśnie przed tym, co sam wtedy
umożliwiał.

Odtworzony przebieg dwóch równoległych żądań kreatora o tej samej nazwie: oba przechodzą
pre-check (połączenia jeszcze nie ma), drugie **nadpisuje** sekret pierwszego, jego `add`
zawodzi na kolizji, a sprzątanie po nieudanym `add` kasuje token **zwycięzcy**. Zostaje
połączenie z referencją, która nie rozwiązuje się na nic. Ten sam wyścig zachodzi między
kreatorem a `DELETE`: usuwanie odczytuje połączenie, kreator w szczelinie tworzy nowe wraz
z sekretem, a usuwanie kasuje świeżo zapisany token.

**Naprawa.** Zamek obejmujący obie operacje w całości. Oba magazyny mają własną synchronizację
wewnętrzną, ale niebezpieczna jest SEKWENCJA dotykająca ich obu — to ona musi być
niepodzielna. Operacje są administracyjne i rzadkie, więc jeden zamek wystarcza i jest
łatwiejszy do uzasadnienia niż zamki per nazwa.

| Niezmiennik | Test |
|---|---|
| Dwa równoległe żądania nie niszczą tokenu zwycięzcy | `test_dwa_rownolegle_zadania_nie_niszcza_tokenu_zwyciezcy` |
| Usuwanie nie kasuje sekretu zapisanego w międzyczasie | `test_usuwanie_nie_kasuje_sekretu_zapisanego_w_miedzyczasie` |
| Po kolizji w magazynie jest dokładnie jeden wpis | `test_polaczenie_ktore_przegralo_nie_zostawia_sekretu` |
| Kolizja bez współbieżności nadal daje 409 (nośność) | `test_kolizja_bez_wspolbieznosci_nadal_zwraca_409` |
| Równoległe żądania o RÓŻNYCH nazwach nie giną (nośność) | `test_rownolegle_rozne_nazwy_dzialaja_niezaleznie` |

**Uwaga metodologiczna do samych testów.** Pierwsza wersja testu wyścigu `DELETE` przechodziła
także BEZ zamka, czyli nie chroniła niczego. Przyczyna: pauzę wstrzyknięto PRZED usunięciem
połączenia, więc drugie żądanie widziało je jeszcze i odpadało na pre-checku z kodem 409.
Groźna jest szczelina MIĘDZY usunięciem połączenia a usunięciem jego sekretu. Po przestawieniu
pauzy test czerwieni się bez zamka — jak powinien od początku.

#### 3. Regresja fail-closed przy braku `cryptography`

Przed Etapem 17 `build_cipher` jawnie importowało `AESGCM` przy budowie, żeby magazyn nie
powstał bez działającego backendu kryptograficznego. Przeniesienie prymitywu do
`husarz.core.crypto` **zgubiło tę kontrolę**, a komentarz w kodzie nadal twierdził, że ona
istnieje. Potwierdzone przez zasymulowanie braku biblioteki: magazyn pamięci i magazyn
sekretów budowały się bez przeszkód, a awaria wychodziła dopiero przy pierwszym zapisie —
czyli w chwili, gdy operator już liczy na to, że dane są zabezpieczone.

**Naprawa.** Kontrola dostępności backendu mieszka teraz w konstruktorze `AesGcmCipher`, czyli
w JEDNYM miejscu obejmującym wszystkich wołających. Docstring `build_secret_store`, który
deklarował fail-closed, jest znów zgodny z kodem.

#### Czego przegląd NIE objął

Cap na liczbę weryfikowanych zgłoszeń odciął piętnaście pozycji. Kilka wygląda na realne
i pozostaje do rozstrzygnięcia — zapisane w ROADMAP, żeby nie zginęły: brak przebudowy
magazynu sekretów przy nadpisaniu konfiguracji w runtime (wyłączenie w panelu może być
fail-open), mutacja stanu w pamięci przed udanym zapisem pliku, brak `fsync` przed
`os.replace`, brak walidacji znaków w nazwie połączenia po stronie kreatora, wpis audytu
kreatora bez `principal`, oraz zerowe pokrycie testami `SecretStoreConfig` i sklejenia
konfiguracja→magazyn w launcherze.

### Etap 17d — domknięcia odcięte przez limit weryfikacji (data: 2026-08-23)

Sekcja „Etap 17c" kończyła się listą piętnastu zgłoszeń, których przegląd nie objął, bo cap
na liczbę weryfikowanych pozycji je odciął. Sześć z nich sprawdzono osobno — **wszystkie
sześć okazało się realne** — i domknięto tutaj. Zapisujemy je z tą samą starannością co
zgłoszenia, które przeszły pełną weryfikację: pozycja odcięta przez limit nie jest pozycją
nieistotną.

#### 1. Fail-open: wyłączenie magazynu w runtime nic nie robiło

`POST /api/config/runtime` przebudowuje router, orkiestrator, wtyczki, pętlę narzędziową
i serwis Gita, ale magazyn sekretów jest domknięciem z chwili startu i przebudowie nie
podlegał. Odtworzone na żywej instancji: wyłączenie kończyło się `ok: true`, a kreator
**nadal przyjmował i zapisywał token** (HTTP 200). Kontrola bezpieczeństwa wyglądała na
wyłączoną, będąc włączoną — najgorszy możliwy stan, bo operator sądzi, że powierzchnia
zapisu jest zamknięta.

**Naprawa.** Bramka czyta BIEŻĄCĄ konfigurację (`state["config"]`), a nie domknięcie
z chwili startu. Świadomie NIE przebudowujemy samego magazynu: klucz główny bywa rozwiązywalny
wyłącznie ze środowiska procesu launchera, więc próba odtworzenia go w API mogłaby zawieść
i zamienić „włącz z powrotem" w nieodwracalne wyłączenie do restartu. Instancja zostaje,
zmienia się wyłącznie bramka — ponowne włączenie działa natychmiast (zweryfikowane na żywo).

**Przy okazji domknięta pułapka projektowa.** Poprawka odsłoniła, że parametr `secret_store`
w `create_app` mógł być po cichu MARTWY, gdy konfiguracja wyłącza magazyn. To ta sama klasa
błędu, co `internal: true` w compose, które bezgłośnie wyłączało publikowanie portów, a testy
utrwalały sprzeczność zamiast ją wykryć. Sprzeczność jest teraz wykrywana przy KONSTRUKCJI
i zgłaszana wyjątkiem; zmiana konfiguracji w runtime pozostaje legalna, bo to świadoma decyzja
operatora.

#### 2. Ukośnik w nazwie połączenia czynił je NIEUSUWALNYM

Nazwa połączenia jest segmentem ścieżki URL-a (`/api/git/connections/{name}`), a nie była
walidowana. Odtworzone na żywej instancji: utworzenie połączenia o nazwie `grupa/projekt`
zwracało **200**, a `DELETE` — **404**, także w wariancie z `%2F`. Połączenie zostawało na
liście i trzymało token bezterminowo, bez żadnej drogi usunięcia przez API.

**Naprawa.** Wzorzec `^[A-Za-z0-9][A-Za-z0-9._-]*$` na obu endpointach dodających — jeden
kontrakt dla obu dróg, inaczej jedna z nich zostawałaby furtką.

#### 3. Wpisy audytu bez `principal`

Wprowadzenie poświadczenia to zdarzenie, przy którym pytanie „kto to zrobił" jest jedynym
istotnym. Oba endpointy dodające zapisywały wpis bez wywołującego. Naprawione; dodano też
brakującą referencję dla `git.connection.remove`.

**Uwaga metodologiczna.** Pierwsza wersja testu sprawdzała, że pole `principal` ISTNIEJE
w rekordzie — i przechodziła także po usunięciu poprawki, bo `AuditLog` serializuje to pole
zawsze, również puste. Wykryte kontrolą nośności. Test sprawdza teraz WARTOŚĆ, na żądaniu
uwierzytelnionym tokenem maszynowym (`principal == "token:operator"`).

#### 4. Mutacja stanu przed udanym zapisem

`put` i `delete` zmieniały słownik w pamięci, a dopiero potem zapisywały plik. Nieudany zapis
zostawiał magazyn rozjechany: proces widział sekret, którego w pliku nie było, więc po
restarcie referencja przestawała się rozwiązywać, choć wcześniej „działała". Odtworzone
przez zasymulowanie awarii zapisu.

**Naprawa.** Praca na kopii: utrwalamy, a stan w pamięci podmieniamy dopiero PO udanym
zapisie. `_persist` przyjmuje wpisy parametrem, żeby ta kolejność była widoczna w sygnaturze,
a nie tylko w komentarzu.

#### 5. `os.replace` bez `fsync` — atomowość bez trwałości

`os.replace` gwarantuje, że czytelnik nie zobaczy połowy zapisu. To NIE to samo, co trwałość
wobec awarii zasilania: bez `fsync` dane mogą siedzieć w buforze systemu, a po nagłym
restarcie plik bywa pusty albo obcięty — czyli magazyn staje się nieczytelny i (fail-closed)
blokuje start aplikacji, tracąc WSZYSTKIE sekrety naraz.

**Naprawa.** `fsync` pliku przed podmianą ORAZ katalogu po niej — sama zawartość pliku nie
wystarcza, gdy w buforze zostaje wpis katalogowy wskazujący na nową nazwę. Synchronizacja
katalogu jest nieobsługiwana na części systemów (m.in. Windows) i tam jej brak NIE jest
błędem. Dodano też pełną pętlę `os.write`: krótszy zapis jest legalny, a cichy zapis połowy
JSON-a zniszczyłby wszystkie sekrety, nie tylko bieżący.

#### 6. Zerowe pokrycie konfiguracji i sklejenia w launcherze

`SecretStoreConfig` oraz ścieżka konfiguracja → magazyn → referencja `husarz:` nie miały ani
jednej asercji: cały walidator dało się usunąć, a zestaw zostawał zielony. Był to jedyny kod
czyniący kreator UŻYTECZNYM (bez niego token jest zapisany, ale serwis Gita go nie odczyta)
i jednocześnie jedyny bez testów. Dodano 19 testów w `tests/unit/test_secret_store_config.py`.

| Niezmiennik | Test |
|---|---|
| Wyłączenie w runtime blokuje kreator | `test_wylaczenie_w_runtime_blokuje_kreator` |
| Panel widzi stan bieżący, nie startowy | `test_wylaczenie_w_runtime_widac_w_stanie_dla_panelu` |
| Ponowne włączenie działa bez restartu | `test_ponowne_wlaczenie_dziala_bez_restartu` |
| Sprzeczny parametr przy konstrukcji jest głośny | `test_sprzeczny_parametr_przy_konstrukcji_jest_glosny` |
| Nazwa niebezpieczna w URL-u odrzucana (5 wariantów) | `test_nazwa_niebezpieczna_w_url_jest_odrzucana` |
| Ten sam kontrakt na obu endpointach | `test_ta_sama_walidacja_na_endpoincie_z_referencja` |
| Poprawne nazwy nadal przechodzą (nośność) | `test_poprawne_nazwy_nadal_przechodza` |
| Audyt kreatora niesie WARTOŚĆ principala | `test_wpis_audytu_kreatora_niesie_principala` |
| Audyt zwykłego dodania — to samo | `test_wpis_audytu_zwyklego_dodania_tez_niesie_principala` |
| Audyt usunięcia — to samo | `test_usuniecie_polaczenia_tez_niesie_principala` |
| Nieudany zapis nie rozjeżdża pamięci z dyskiem | `test_nieudany_zapis_nie_rozjezdza_pamieci_z_dyskiem` |
| To samo dla usuwania | `test_nieudane_usuwanie_nie_rozjezdza_pamieci_z_dyskiem` |
| Zapis synchronizowany na dysk (plik i katalog) | `test_zapis_jest_synchronizowany_na_dysk` |
| Konfiguracja magazynu: 11 niezmienników | `tests/unit/test_secret_store_config.py` |
| Sklejenie launchera i schemat `husarz:` | `test_scheme_secrets_rozwiazuje_referencje_husarz` |

**Nośność sprawdzona dla każdej poprawki.** Osiem mutacji; dwie z nich ujawniły problemy
w samych testach (mutacja obejmująca tylko jeden z dwóch modeli oraz asercja na obecność
pola zamiast na jego wartość) — oba poprawione, po czym wszystkie osiem czerwieni zestaw.

**Weryfikacja na uruchomionej aplikacji.** Wyłączenie magazynu w runtime → kreator odmawia
(409), panel pokazuje `enabled: false`, token nie osiada w żadnym pliku; ponowne włączenie →
kreator działa natychmiast; nazwa z ukośnikiem → 422 zamiast nieusuwalnego połączenia.

**Czego nadal NIE domknięto.** Z listy z Etapu 17c zostają pozycje o mniejszej wadze,
zapisane w ROADMAP: brak limitu liczby wpisów w magazynie, brak rotacji i sygnalizacji
wygasania tokenów, oraz nieprzetestowane zachowanie na systemach plików bez atomowego
`rename` (część udziałów sieciowych).

### Etap 17e — trzy wady z drugiego przeglądu (data: 2026-08-23)

Drugi adwersaryjny przegląd objął commity 1bb2191 (własne CA, sprostowania) i 5277d49
(domknięcia). Faza szukania dała szesnaście zgłoszeń; trzy o największej wadze sprawdzono
osobno i **wszystkie trzy potwierdzono uruchomieniem**. Dwie z nich to wady, które przetrwały
poprzednie dwa przeglądy — bo dotyczyły magazynu POŁĄCZEŃ, podczas gdy uwagę skupiał magazyn
SEKRETÓW.

#### 1. Przebudowa serwisu Git kasowała połączenia, a token zostawał na dysku

Fabryka serwisu Git w launcherze domykała na `git_service` **z chwili startu**. Gdy Git był
wtedy wyłączony — a domyślnie jest — domknięta wartość zostawała `None`, więc każda kolejna
przebudowa po `POST /api/config/runtime` budowała PUSTY magazyn i kasowała połączenia dodane
przez API. Przy włączonym magazynie sekretów token zostawał wtedy na dysku jako sierota,
a `DELETE` zwracał `ok: true`, nie usuwając niczego.

Komentarz w `_active_git` twierdził przy tym, że „magazyn połączeń jest przekazywany dalej,
więc przebudowa nie gubi połączeń". Było to prawdą tylko dla ścieżki, w której Git działał od
startu — kolejna rozbieżność kod↔dokumentacja.

**Naprawa.** Fabryka przyjmuje magazyn JAWNIE, a API podaje jej magazyn serwisu AKTUALNEGO
(uchwyt aktualizowany po każdej udanej budowie stosu). Kontrakt `git_service_factory` zmienił
się z jednoargumentowego na dwuargumentowy.

#### 2. Magazyn połączeń miał tę samą wadę, którą domknięto w magazynie sekretów

`FileGitConnectionStore` mutował słownik w pamięci PRZED zapisem pliku i wypuszczał surowy
`OSError`. Skutek był podwójny: stan w pamięci rozjeżdżał się z dyskiem (połączenie znikało
po restarcie), a kreator — który łapie `GitConnectionError` — nie łapał `OSError`, więc awaria
zapisu dawała 500 i **pomijała sprzątanie świeżo zapisanego sekretu**, zostawiając go
osieroconym.

To ta sama poprawka, którą wykonano w Etapie 17d dla sekretów. Wniosek na przyszłość: gdy
poprawka dotyczy wzorca, a nie pojedynczego miejsca, trzeba przeszukać repozytorium pod kątem
tego wzorca — nie poprzestawać na module, w którym wadę zgłoszono.

Przy okazji domknięto pomniejszą wadę w OBU magazynach: `tmp.unlink(missing_ok=True)` w bloku
sprzątającym sam potrafił rzucić `NotADirectoryError` (gdy katalog nadrzędny nie istnieje albo
jest plikiem) i przesłaniał wtedy właściwą przyczynę awarii.

#### 3. Sekret trwały przy ulotnym magazynie połączeń = gwarantowana sierota

Sekret jest zawsze zapisywany na dysk, a magazyn połączeń przy domyślnym
`git.connections_path: null` jest ULOTNY. Kreator produkował więc przy każdym restarcie stan,
w którym połączenie znika, a token zostaje — i był on **nie do usunięcia przez API**, bo
`DELETE` kasował sekret wyłącznie wtedy, gdy połączenie jeszcze istniało.

**Naprawa, dwutorowa.**

Zapobieganie: kreator odmawia (409) przy ulotnym magazynie połączeń, z komunikatem wskazującym
`git.connections_path`. Zgodność trwałości obu magazynów jest warunkiem sensowności kreatora,
a nie szczegółem — milczące produkowanie śmieci, o których operator dowie się po restarcie,
byłoby dokładnie tą klasą błędu, którą ten dokument opisuje od Etapu 17c.

Sprzątanie: `DELETE` usuwa sekret także wtedy, gdy połączenia JUŻ NIE MA — o ile nazwa należy
do naszej przestrzeni (`husarz:git/<nazwa>`). Referencji zewnętrznej (`env:`/`vault:`) nie
ruszamy nigdy. Odpowiedź niesie teraz SKUTEK (`removed`, `secret_removed`), a nie samo
`ok: true`, które przy sierocie było odpowiedzią nieprawdziwą.

Trwałość magazynu połączeń jest deklarowana jawnie (`GitConnectionStore.persistent`), a nie
zgadywana po typie obiektu.

| Niezmiennik | Test |
|---|---|
| Przebudowa nie gubi połączeń, gdy Git był przy starcie wyłączony | `test_przebudowa_nie_gubi_polaczen_gdy_git_byl_wylaczony_przy_starcie` |
| Nieudany zapis połączeń nie rozjeżdża pamięci z dyskiem | `test_nieudany_zapis_polaczen_nie_rozjezdza_pamieci_z_dyskiem` |
| Awaria zapisu daje błąd domenowy, nie surowy `OSError` | `test_awaria_zapisu_daje_blad_domenowy_a_nie_surowy_oserror` |
| Kreator odmawia przy ulotnym magazynie połączeń | `test_kreator_odmawia_przy_ulotnym_magazynie_polaczen` |
| Kreator działa przy trwałym (nośność) | `test_kreator_dziala_przy_trwalym_magazynie` |
| Osierocony sekret da się usunąć przez API | `test_osierocony_sekret_da_sie_usunac_przez_api` |
| Odpowiedź `DELETE` niesie skutek, nie samo „przyjęto" | `test_odpowiedz_delete_niesie_skutek_a_nie_samo_przyjeto` |
| Referencja zewnętrzna nadal nietknięta | `test_referencja_zewnetrzna_nadal_nietkniete` |
| Trwałość deklarowana jawnie | `test_trwalosc_magazynow_jest_deklarowana_jawnie` |

**Nośność — i znów wada w moim własnym teście.** Z pięciu mutacji jedna nie zaczerwieniła
zestawu: test przebudowy miał awaryjne przejście na ten sam PLIK, gdy fabryka dostawała
`None`, więc połączenia wracały z dysku i test przechodził także BEZ poprawki. Przepisany na
magazyn ULOTNY, gdzie utrata jest widoczna wprost. To trzeci raz w tym etapie, gdy kontrola
nośności znalazła wadę nie w kodzie, lecz w teście, który miał go chronić.

**Czego ten przegląd NIE objął.** Faza weryfikacji nie zdążyła się wykonać przed zakończeniem
poprzedniej sesji; z szesnastu zgłoszeń sprawdzono trzy o największej wadze. Pozostałe
trzynaście zapisano w ROADMAP — wśród nich: wyłączenie magazynu zamyka tylko ZAPIS (istniejące
tokeny nadal się rozwiązują i uwierzytelniają Gita), bramka czyta wyłącznie `enabled`
(zmiana `key_ref`/`path` w runtime jest cicho ignorowana), `ca_bundle` wraca dosłownie
w odpowiedzi 400, panel wyświetla błędy 422 jako `[object Object]`, oraz brak blokady pliku
przy dwóch procesach na tym samym magazynie.

### Etap 17f — dokończona weryfikacja drugiego przeglądu (data: 2026-08-23)

Faza weryfikacji przeglądu commitów 1bb2191 i 5277d49 dokończyła się i potwierdziła pięć
zgłoszeń. Dwa dotyczyły wad naprawionych już w `cab4d12` (Etap 17e). Cztery pozostałe opisano
niżej — w tym **regresję, którą wprowadziłem właśnie w `cab4d12`**.

Bilans dotychczasowy: z czternastu zgłoszeń sprawdzonych osobno **czternaście okazało się
realnych**. To zmienia sposób, w jaki traktuję resztę listy: zgłoszenie odcięte przez limit
weryfikacji jest domyślnie prawdopodobne, nie hipotetyczne.

#### 1. Regresja: sprzątanie sierot niszczyło DZIAŁAJĄCE poświadczenie

Etap 17e dodał usuwanie osieroconego sekretu, rozszerzając warunek o „połączenia nie ma, więc
to sierota". Pominąłem przypadek, w którym referencję **współdzieli inne połączenie** — np. po
zmianie nazwy, gdy operator dodał nowe połączenie wskazujące dotychczasowy wpis.

Odtworzone: przy istniejącym połączeniu `produkcja` o referencji `husarz:git/gh` żądanie
`DELETE /api/git/connections/gh` (nazwy `gh` już nie ma) kasowało wpis `git/gh`, unieważniając
poświadczenie działającego połączenia — i raportowało `secret_removed: true`, czyli sukces.

**Naprawa.** Sekret kasujemy tylko wtedy, gdy po usunięciu połączenia **żadne inne** nie
wskazuje tej referencji. Warunek sprawdzamy PO `remove`, więc usuwane połączenie nie liczy się
do siebie samego.

Wniosek metodologiczny: rozszerzenie warunku usuwania danych wymaga wypisania WSZYSTKICH
przypadków, w których dane mogą być jeszcze używane. Poprzednia wersja warunku była zawężona
i bezpieczna; rozszerzając ją, sprawdziłem, że działa dla sieroty, ale nie sprawdziłem, komu
jeszcze może zaszkodzić.

#### 2. Nadpisania runtime, których nie da się zastosować, odpowiadały `ok: true`

`_magazyn_wlaczony()` z Etapu 17d czyta z bieżącej konfiguracji WYŁĄCZNIE `enabled`. Sama
instancja magazynu — ścieżka pliku i klucz główny — pozostaje domknięciem z chwili startu.
Operator, który przez panel „przenosił" magazyn na wolumen szyfrowany i rotował klucz główny,
dostawał `ok: true`, po czym kolejny token trafiał do STAREJ ścieżki, zaszyfrowany STARYM
kluczem. Nowy plik nie powstawał nigdy.

Sceptyk weryfikujący zgłoszenie wykazał, że **nie jest to własność magazynu sekretów, lecz
całego endpointu**: identycznie zachowywało się nadpisanie `security.audit.path`. Ta uwaga
była trafna i poszerzyła zakres poprawki — sprawdzone niezależnie, oba pola faktycznie
milczały.

**Naprawa.** Lista pól niezmiennych w runtime (`security.secret_store.path`,
`security.secret_store.key_ref`, `security.audit`) i odmowa, gdy nadpisanie faktycznie je
ZMIENIA. Porównujemy WARTOŚCI wobec konfiguracji STARTOWEJ, nie obecność klucza w żądaniu:

- powtórzenie dotychczasowej wartości musi przejść (inaczej ponowne włączenie magazynu tym
  samym kluczem byłoby zablokowane),
- przy wyłączaniu magazynu ścieżka i klucz przestają mieć znaczenie, więc ich zniknięcie ze
  scalonej konfiguracji nie jest „zmianą",
- punktem odniesienia jest konfiguracja startowa, bo to z niej zbudowano żywe obiekty.

Obie te subtelności wyszły dopiero na czerwonych testach — pierwsza wersja bramki blokowała
wyłączenie magazynu, czyli psuła kontrolę naprawioną w Etapie 17d.

Świadomie NIE przebudowujemy magazynu ani dziennika: wymagają zasobów rozwiązywalnych zwykle
tylko w procesie launchera (klucz główny ze środowiska, prawa do katalogu), a nieudana
odbudowa w trakcie żądania zostawiłaby aplikację bez działającego audytu — gorzej niż przed
zmianą.

#### 3. Token wklejony w pole nazwy był trwale zapisywany

Wzorzec nazwy dodany w Etapie 17d (`^[A-Za-z0-9][A-Za-z0-9._-]*$`, do 64 znaków) przepuszcza
DOKŁADNIE kształt obsługiwanych tokenów: `ghp_` + 36 znaków i `glpat-` + 20. Nazwa wklejona
omyłkowo trafiała wtedy jednocześnie do:

- **niemodyfikowalnego dziennika audytu** (`detail.name` oraz `detail.token_ref`),
- pliku połączeń jawnym tekstem,
- magazynu sekretów jako **jawny klucz wpisu** — w pliku, którego cała racja bytu polega na
  tym, że bez klucza głównego jest bezużyteczny,
- odpowiedzi API i tabeli w panelu.

Odtworzone: `name="ghp_16C7e42F292c6912E7710c838347Ae17"` → HTTP 200 i token we wszystkich
czterech miejscach. Ponieważ dziennika audytu z definicji nie da się wyczyścić, jedynym
wyjściem byłoby unieważnienie tokenu u dostawcy.

**Naprawa.** Odrzucenie nazw zaczynających się prefiksem poświadczenia (`ghp_`, `gho_`,
`ghu_`, `ghs_`, `ghr_`, `github_pat_`, `glpat-`), na OBU endpointach dodających. Sprawdzenie
po prefiksie, a nie heurystyka entropii, która myliłaby się na sensownych nazwach w rodzaju
`gh-prod-2026`. Komunikat błędu celowo nie powtarza wartości.

**Uczciwie o wadze.** Sceptyk słusznie zakwestionował uzasadnienie prawdopodobieństwa
podane w zgłoszeniu: pola `name` i `token` w konsoli **nie sąsiadują** (dzielą je cztery
kontrolki — sam zmieniłem ten układ w Etapie 17). Historia „pomyłkowe wklejenie jest
niewidoczne" jest więc słabsza, niż ją przedstawiono. Skutek pozostaje jednak nieodwracalny,
a obrona kosztuje siedem prefiksów — dlatego poprawka wchodzi mimo skorygowanej wagi.

#### 4. Awaria zapisu przy `DELETE` nie zostawiała śladu

`GitConnectionError` z magazynu połączeń leciał niezłapany: surowe 500 i **zero wpisów
w dzienniku**, mimo że żądanie dotyczyło usunięcia poświadczenia. Naprawione: 503 z czytelnym
komunikatem oraz wpis `git.connection.remove.failed`.

Stan po awarii jest przy tym SPÓJNY — dzięki poprawce z Etapu 17e magazyn utrwala przed
podmianą stanu w pamięci, więc nieudany zapis nie usuwa niczego. Opis w zgłoszeniu mówił
o „połączeniu zniknietym i sekrecie osieroconym"; ta część była prawdziwa PRZED Etapem 17e
i przestała być prawdziwa po nim.

| Niezmiennik | Test |
|---|---|
| `DELETE` nie kasuje sekretu używanego przez inne połączenie | `test_delete_nie_kasuje_sekretu_uzywanego_przez_inne_polaczenie` |
| Prawdziwa sierota nadal da się usunąć (nośność) | `test_prawdziwa_sierota_nadal_da_sie_usunac` |
| Zmiana klucza głównego w runtime odrzucona | `test_zmiana_klucza_glownego_w_runtime_jest_odrzucana` |
| Zmiana ścieżki magazynu odrzucona | `test_zmiana_sciezki_magazynu_w_runtime_jest_odrzucana` |
| Zmiana ścieżki audytu odrzucona | `test_zmiana_sciezki_audytu_w_runtime_jest_odrzucana` |
| Wyłączenie magazynu nadal przechodzi (nośność) | `test_wylaczenie_magazynu_nadal_przechodzi` |
| Ponowne włączenie tym samym kluczem przechodzi (nośność) | `test_ponowne_wlaczenie_tym_samym_kluczem_przechodzi` |
| Zwykłe nadpisanie nadal działa (nośność) | `test_zwykle_nadpisanie_nadal_dziala` |
| Nazwa wyglądająca na token odrzucona (5 wariantów) | `test_nazwa_wygladajaca_na_token_jest_odrzucana` |
| Ten sam kontrakt na obu endpointach | `test_ta_sama_ochrona_na_endpoincie_z_referencja` |
| Sensowne nazwy nadal przechodzą (nośność) | `test_sensowne_nazwy_nadal_przechodza` |
| Awaria zapisu przy `DELETE` daje 503 i ślad w audycie | `test_awaria_zapisu_przy_delete_daje_503_i_slad_w_audycie` |

**Nośność.** Pięć mutacji, wszystkie czerwienią testy. Jedna wymagała powtórzenia, bo mój
wzorzec mutacji trafił w niewłaściwe wystąpienie `except GitConnectionError` (jest ich dwa) —
wada narzędzia sprawdzającego, nie testu.

### Etap 17g — pięć ostatnich zgłoszeń drugiego przeglądu (data: 2026-08-23)

Ostatnia partia zgłoszeń odciętych przez limit weryfikacji. **Cztery potwierdzone, jedno
obalone** — pierwszy przypadek w tej serii, gdy sprawdzenie nie odtworzyło opisanej wady.

Zaktualizowany bilans: **z dziewiętnastu zgłoszeń sprawdzonych osobno osiemnaście okazało się
realnych**. Wskaźnik jest wysoki, ale nie stuprocentowy — i dobrze, że tak zostało zapisane:
traktowanie każdego zgłoszenia jako pewnego byłoby tym samym błędem, co ignorowanie ich.

#### 1. `enabled: false` zamykało tylko ZAPIS — teraz jest KILL-SWITCHEM

Bramka z Etapu 17d chroniła kreator i widok stanu, ale **rozwiązywanie referencji szło obok
niej**: `_SchemeSecrets` w launcherze sięgało do magazynu niezależnie od konfiguracji.
Wyłączenie magazynu blokowało więc nowe wpisy, podczas gdy dotychczasowe tokeny nadal
uwierzytelniały operacje Gita.

**Decyzja i jej uzasadnienie.** Zmieniamy semantykę: wyłączenie odcina także ODCZYT. Operator
wyłączający magazyn robi to zwykle w reakcji na incydent i oczekuje, że przestanie on wydawać
materiał — a nie że zablokuje wyłącznie przyszłe zapisy, zostawiając napastnikowi dostęp przez
istniejące połączenie. Fail-closed jest tu spójne z resztą projektu.

Skutek uboczny jest zamierzony i GŁOŚNY: operacja Gita kończy się komunikatem „Nie udało się
rozwiązać tokenu połączenia" (zweryfikowane na realnej ścieżce `GitService.provider_for`), a nie
cichą degradacją. Ponowne włączenie działa natychmiast, bez restartu, więc koszt pomyłki jest
niski.

To **zmiana zachowania**, nie tylko poprawka — odnotowana jako taka w CHANGELOG-u.

#### 2. Bramka magazynu sprawdzana POZA zamkiem

`_require_secret_store()` wołane jest na początku kreatora, a zamek zakładany dopiero przed
sekwencją zapisu. Żądanie, które przeszło bramkę tuż przed wyłączeniem magazynu, zapisywało
token JUŻ PO tym wyłączeniu. Okno wąskie, ale kontrola bezpieczeństwa nie może mieć okna
„prawie zamkniętego", a koszt domknięcia to jedno porównanie pod zamkiem, który i tak trzymamy.

#### 3. `ca_bundle` wracał dosłownie w odpowiedzi 400

Druga droga echa obok tej, którą zamknął handler walidacji z Etapu 17c. Wartość jest ścieżką,
nie sekretem, więc waga jest niższa — ale niezmiennik brzmi „API nie odsyła tego, co dostało",
a pole przyjmuje dowolny tekst od operatora. Komunikat wskazuje teraz POLE i mówi, co jest nie
tak, nie powtarzając wartości.

#### 4. `POST /api/git/connections` był poza zamkiem

Druga droga dodawania nie była objęta `_mutex_polaczen`, więc mogła wyścigać się ze
sprzątaniem sekretu w `DELETE`. Objęta.

**Uczciwie o pokryciu tej poprawki.** Nie ma dla niej testu sprawdzającego SKUTEK. Groźne okno
to dwie sąsiednie instrukcje w `DELETE` (lista połączeń → usunięcie sekretu) i otwarcie go
wymagałoby pauzy wstrzykniętej w kod produkcyjny, czyli testu zmieniającego to, co bada.
Zamiast udawać pokrycie, zostawiliśmy kontrolę STRUKTURALNĄ (`svc.add` musi stać pod zamkiem)
z jawnym komentarzem, że jest słabszym dowodem: chroni przed usunięciem zamka, nie dowodzi
jego poprawności. Test współbieżnego dodawania również ma dopisane wprost, że przechodzi
z poprawką i bez niej.

#### 5. OBALONE: bezwzględne ścieżki operatora w odpowiedziach konfiguracji

Zgłoszenie mówiło, że `POST /api/config/validate` odsyła bezwzględną ścieżkę katalogu
konfiguracji. Sprawdzone: odpowiedź nie zawiera ani `config_dir`, ani żadnego przedrostka
ścieżki systemowej. Zgłoszenie nietrafione — odnotowane, żeby nie wracało.

| Niezmiennik | Test |
|---|---|
| Wyłączony magazyn nie rozwiązuje istniejących referencji | `test_wylaczony_magazyn_nie_rozwiazuje_istniejacych_referencji` |
| Kill-switch nie dotyka pozostałych schematów (nośność) | `test_killswitch_nie_dotyka_pozostalych_schematow` |
| Wyłączenie w trakcie żądania zatrzymuje zapis | `test_wylaczenie_w_trakcie_zadania_zatrzymuje_zapis` |
| Komunikat o błędzie CA nie powtarza wartości | `test_komunikat_o_bledzie_ca_nie_powtarza_wartosci` |
| Konsola nie skleja tablicy błędów ze stringiem | `test_konsola_nie_sklejaja_tablicy_bledow_ze_stringiem` |
| Obie drogi dodawania pod zamkiem (kontrola STRUKTURALNA) | `test_obie_drogi_dodawania_sa_pod_zamkiem` |

**Przy okazji: konsola pokazywała błędy walidacji jako `[object Object]`.** Odpowiedź 422
niesie TABLICĘ obiektów, a panel sklejał ją ze stringiem — komunikat ginął dokładnie tam, gdzie
użytkownik pomylił się w formularzu i najbardziej go potrzebował. Regresja własna, wprowadzona
razem z handlerem z Etapu 17c.

**Czego nadal NIE zrobiono.** Magazyn nie ma blokady pliku, więc dwa procesy Husarza
wskazujące ten sam plik mogą się nadpisać. Jest to z założenia niewspierane (jedna instancja
na proces) i zapisane w ROADMAP; przenośna blokada plikowa wymaga osobnych ścieżek dla POSIX
i Windows, więc to zadanie na osobny krok, nie doklejka.

### Etap 17g — magazyn sekretów przy dwóch procesach (data: 2026-08-23)

Ostatnia pozycja z serii przeglądów. Magazyn zakładał wyłączność jednego procesu, ale **nic
jej nie egzekwował**. Dwa procesy Husarza wskazujące ten sam plik gubiły sobie zapisy: każdy
trzyma wpisy w pamięci, więc zapis drugiego nadpisywał plik wersją bez sekretu zapisanego
przez pierwszy. Objawiało się to jako „token przestał działać" — bez żadnego błędu.

**Rozwiązanie: odczyt-modyfikacja-zapis pod blokadą, nie wykluczanie procesów.** Rozważane
było zajęcie blokady wyłącznej na czas życia procesu (druga instancja nie wstaje). Odrzucone:
byłoby prostsze, ale zamykałoby drogę narzędziom, które chcą tylko odczytać magazyn, i psułoby
testy symulujące restart. Blokada obejmuje więc pojedynczą operację zapisu, a nie cały proces.

Trzy elementy, każdy z osobnej awarii:

1. **Blokada NAJPIERW.** Bez niej dwa procesy nadpisywały sobie zapisy, bo każdy startował od
   swojej kopii wpisów.
2. **Ponowny odczyt z dysku POD blokadą.** Kopia w pamięci mogła się zestarzeć, odkąd inny
   proces coś dopisał — modyfikujemy stan faktyczny, nie zapamiętany.
3. **Przeładowanie przy odczycie**, gdy zmienił się znacznik pliku (czas modyfikacji +
   rozmiar). Bez tego sekret dopisany przez drugi proces byłby dla pierwszego nieistniejący,
   więc połączenie utworzone tam nie działałoby tutaj. Koszt: jedno `stat` na odczyt.

**Blokujemy OSOBNY plik `.lock`**, a nie sam magazyn: plik magazynu jest podmieniany przez
`os.replace`, więc blokada trzymana na nim dotyczyłaby po chwili i-węzła, którego już nikt nie
widzi. Plik blokady jest pusty i nigdy nic nie przechowuje — pilnuje tego test.

Blokada jest **doradcza**: chroni przed innym Husarzem, nie przed dowolnym procesem, który
zignoruje konwencję. To wystarcza dla scenariusza, który realnie zachodzi.

| Niezmiennik | Test |
|---|---|
| Zapis drugiego procesu nie kasuje sekretu pierwszego | `test_zapis_drugiego_procesu_nie_kasuje_sekretu_pierwszego` |
| Pierwszy proces widzi sekret dopisany przez drugi | `test_pierwszy_proces_widzi_sekret_dopisany_przez_drugi` |
| Własny zapis po obcym nie gubi żadnego | `test_wlasny_zapis_po_zapisie_obcym_nie_gubi_zadnego` |
| Usuwanie też startuje od stanu z dysku | `test_usuwanie_tez_startuje_od_stanu_z_dysku` |
| **Zapis CZEKA na zwolnienie blokady** (wzajemne wykluczanie) | `test_zapis_CZEKA_na_zwolnienie_blokady_przez_inny_proces` |
| Plik blokady jest pusty | `test_plik_blokady_powstaje_obok_i_jest_pusty` |

Testy używają PRAWDZIWYCH procesów potomnych, nie wątków: `flock` jest zakładany per
deskryptor, więc test wątkowy mógłby przejść z zupełnie innego powodu.

**Kontrola nośności znów wskazała lukę — w moich testach, nie w kodzie.** Mutacja zdejmująca
`flock` NIE zaczerwieniła żadnego z pierwszych pięciu testów. Przyczyna: wszystkie są
sekwencyjne (proces potomny kończy się, zanim rodzic zacznie pisać), więc blokada nigdy nie
jest w sporze — testy sprawdzały poprawność odczytu-modyfikacji-zapisu, a nie wykluczanie.
Dopisany test, w którym proces potomny TRZYMA blokadę przez sekundę, a my mierzymy, czy zapis
rodzica na nią zaczekał. Po nim mutacja czerwieni zestaw.

Druga mutacja też nie zaczerwieniła testu, ale to była wada narzędzia, nie pokrycia:
skierowałem ją na test, który tej ścieżki nie dotyka. Po wycelowaniu we właściwy — czerwony.

**Ograniczenia — wprost.**

1. **Ścieżka windowsowa (`msvcrt.locking`) NIE jest zweryfikowana** — nie ma tu Windowsa.
   Kod jest napisany i otypowany, ale jego działania nie potwierdzono uruchomieniem.
2. Blokada nie chroni przed procesem, który jej nie używa (doradcza z definicji).
3. Przeładowanie przy odczycie opiera się na czasie modyfikacji i rozmiarze. Zapis, który
   trafiłby w tę samą sekundę i dał identyczny rozmiar, nie zostałby wykryty. W praktyce
   nieosiągalne dla różnych treści (szyfrogram ma losowy nonce), ale to założenie, nie dowód.

## Etap 17h — diagnoza wystawiona przez HTTP (`GET /api/doctor`)

Notatka weryfikacyjna. Zmiana MODYFIKUJE decyzję bramki: dokłada nowe uprawnienie RBAC
i nową ścieżkę ruchu wychodzącego wyzwalaną żądaniem HTTP — czyli trzeci poziom audytu
wg tabeli w `CLAUDE.md`.

### Trzy powierzchnie, po kolei

**1. Kto może pytać — nowe uprawnienie `diagnostics:read`.**

Pierwszym odruchem było oparcie diagnozy na `config:read`, bo to „endpoint tylko do odczytu".
Sprawdzenie `DEFAULT_ROLE_PERMISSIONS` pokazało, że byłby to błąd: `config:read` ma rola
**`user`**, czyli konto zakładane samodzielną rejestracją. Diagnoza niesie natomiast
**endpointy silników** i **ścieżki katalogów operatora** — dane, których warstwa `config:read`
celowo nie wystawia (`GET /api/models` podaje backend, tagi i długość kontekstu, ale **nie**
`endpoint`). Wywołanie dodatkowo **otwiera połączenia wychodzące**, więc nie jest odczytem
stanu. Stąd osobne uprawnienie, przyznane `admin` i `operator`.

`viewer` go **nie** dostał: „podgląd" nie powinien wysyłać pakietów. Decyzja jest zapisana
w komentarzu przy definicji roli, żeby brak wpisu nie wyglądał na przeoczenie; ewentualna
rola NOC jest w ROADMAP.

**2. Czy to nie jest skaner portów.** Nie: sondowanie idzie przez `SondaSystemowa`, która pyta
`check_endpoint_allowed` — tę samą funkcję, której używa router. Endpoint spoza allowlisty nie
jest odpytywany, a kontrola kończy się stanem NIEZNANY z podaniem powodu. Bez tego rola
z `config:write` mogłaby wpisać dowolny adres jako endpoint modelu i odczytać z diagnozy, czy
odpowiada. Niezmiennik ma testy w `tests/unit/test_doctor.py`
(`test_sonda_NIE_odpytuje_endpointu_spoza_allowlisty` oraz jego kontrola nośności
`test_sonda_odpytuje_endpoint_dozwolony`).

**3. Co trafia do audytu.** Wywołanie zostawia wpis akcji `doctor` z referencją wywołującego.
W `detail` są **wyłącznie trzy liczby** (`blocking`, `warnings`, `unknown`) — żadnych endpointów
ani ścieżek, bo dziennik jest niemodyfikowalny. Allowlista `husarz.api.audit_view` działa
deny-by-default, więc `GET /api/audit` pokazuje ten wpis z pustym `detail`; sprawdzone testem,
nie założone.

### Kontrola portu bierze REALNY adres nasłuchu

`create_app` dostaje `listen_host`/`listen_port` z launchera. Świadomie **nie** czytamy nagłówka
`Host` z żądania: nagłówek pochodzi od klienta, więc kontrola bezpieczeństwa oparta na nim
dawałaby wynik sterowany przez pytającego.

### Sonda jest wstrzykiwana także przez API

`create_app(doctor_probe=...)`. Bez tego API zaszywałoby `SondaSystemowa` na sztywno i odebrało
modułowi diagnozy własność, dla której powstał — pełną testowalność offline. Żaden test tej
funkcji nie dotyka sieci.

### Nośność — dziewięć mutacji, dziewięć czerwonych

Mutowano po kolei: odebranie uprawnienia operatorowi, podmianę bramki na `config:read`,
zignorowanie portu z launchera, policzenie ostrzeżeń jako blokujących, użycie konfiguracji ze
startu zamiast aktualnej, usunięcie wpisu audytu, wstawienie opisu ustalenia bez escapowania,
zdjęcie osłony przed sięganiem do prototypu i odpięcie zakładki od przełącznika. Każda
zaczerwieniła swoje testy. Skrypt sam przywracał oryginał i kończył asercją równości treści;
`git diff` po przebiegu nie zawierał śladu mutacji.

### Ograniczenia — wprost

1. **Panel konsoli ma kontrolę ŹRÓDŁA, nie skutku.** `tests/unit/test_konsola_diagnoza.py`
   sprawdza obecność elementów, wiązań i escapowania w `console.html`, ale nie uruchamia
   przeglądarki. Skutek zweryfikowano **ręcznie**: uruchomiono `husarz up --port 8000`, otwarto
   konsolę, kliknięto zakładkę i przycisk „Sprawdź ponownie" — dwa `GET /api/doctor` w logu,
   zero błędów w konsoli przeglądarki, tabela zgodna co do znaku z wyjściem CLI. Zrzut:
   `docs/assets/screenshots/console-diagnoza.png`.
2. ~~**Brak limitu tempa dla `/api/doctor`.**~~ **Domknięte** — patrz „Etap 17k" niżej.
   `security.diagnostics.max_requests_per_minute`, domyślnie 6/min, sprawdzany PRZED
   sondowaniem.
3. **Tabela w konsoli nie odróżnia problemu blokującego od ostrzeżenia** (oba jako ✕), tak samo
   jak CLI (oba jako `[!!]`). Rozróżnienie niesie nagłówek z licznikami. Pole `severity` jest
   w odpowiedzi API, więc zmiana wymaga poprawienia OBU nośników naraz — zapisane w ROADMAP.

## Etap 17i — sonda głęboka diagnozy (`husarz doctor --probe`)

Notatka weryfikacyjna. Zmiana dokłada **nową drogę wychodzącą** wyzwalaną poleceniem
operatora, dotyka rozwiązywania sekretów i wpuszcza treść od modelu do wyjścia narzędzia —
trzeci poziom audytu wg tabeli w `CLAUDE.md`.

### Bilans przeglądu adwersaryjnego

Cztery niezależne perspektywy (poprawność, bezpieczeństwo, przestrzeń awarii, spójność),
każde zgłoszenie weryfikowane osobno przez agenta, którego zadaniem było je **obalić**.
**Z 36 zgłoszeń 33 potwierdzono uruchomieniem, 3 obalono.** Po odjęciu duplikatów między
perspektywami zostało **13 odrębnych wad** — wszystkie w kodzie, który przed chwilą
napisałem i uznałem za sprawdzony, przy komplecie zielonych testów.

Jeden werdykt sprostował samo zgłoszenie: scenariusz podany jako „odtworzone" był zmyślony
(2 s odpowiedzi przy limicie 60 s to NIE jest naruszenie), choć teza okazała się prawdziwa
z innego powodu. Potwierdza to zasadę z `CLAUDE.md`: nie przyjmować na wiarę ANI zgłoszenia,
ANI jego obalenia — każde sprawdziłem własnym uruchomieniem.

### Wady, które naprawiono

**1. Fałszywe OK dla modelu odpowiadającego dłużej, niż czat czeka (krytyczna).**
Kontrola porównywała czas z `spec.request_timeout_seconds`. To pole jest `None` w **każdym**
modelu dostarczonej konfiguracji, a `None` NIE znaczy „bez limitu": klient podstawia
`DEFAULT_TIMEOUT_SECONDS = 60`. Warunek nie odpalał się więc nigdy. Droga do fałszywego OK
prowadziła przez radę **samego narzędzia**: po pierwszym „timeout" operator podnosił
`--probe-timeout`, a wtedy model odpowiadający po 200 s dostawał czyste „OK" — mimo że czat
przerywa go po 60. Naprawione: porównanie z limitem EFEKTYWNYM, a komunikat mówi, skąd ten
limit pochodzi.

**2. Blokada anty-SSRF raportowana jako „zły format odpowiedzi" (poważna).**
`EgressError` z pinowania IP (ADR-0020) nie jest wyjątkiem `httpx`, więc wpadał do kategorii
„zła odpowiedź". Operator dostawał radę o zgodności z OpenAI, gdy naprawdę nazwa wskazała
zakres zabroniony albo DNS nie odpowiedział — i żądanie w ogóle nie opuściło maszyny. Nowa
kategoria `rozwiazanie-nazwy` z własną instrukcją.

**3. Wstrzyknięcie sekwencji ANSI z odpowiedzi modelu (poważna).**
Treść modelu trafiała do terminala po samym spłaszczeniu białych znaków — a `\x1b[2J\x1b[H`
nie zawiera białych znaków. Model mógł wyczyścić ekran i domalować własne „[ok] wszystkie
kontrole przeszły" nad wypisanymi wcześniej problemami. Diagnoza bezpieczeństwa, której
wyjście da się przemalować, jest gorsza niż jej brak. Naprawione: usuwamy wszystkie znaki
kategorii Unicode `C*`.

**4. `backend: mock` dawał „ODPOWIEDZIAŁ" w kilka mikrosekund (poważna).**
`MockClient` odpowiada z pamięci, bez sieci i bez modelu — więc JEDYNA kontrola skutku
w całej diagnozie nie sprawdzała żadnego skutku. Fałszywe OK w mechanizmie stworzonym po to,
żeby fałszywe OK wykrywać. Model `mock` jest teraz pomijany ze stanem NIEZNANY i wyjaśnieniem.

**5. Sonda strzelała do modelu `enabled: false`** — obok ustalenia, że jest wyłączony.

**6. `--probe-timeout` bez walidacji (poważna).** `model_copy(update=...)` **omija** walidację
schematu (`ge=1`), więc `0` i wartości ujemne docierały do klienta i przerywały każde żądanie
natychmiast, diagnozując sprawny silnik jako awarię. Walidacja przeniesiona do argparse.

**7. Sonda obcinała limit PONIŻEJ produkcyjnego (poważna).** Model z
`request_timeout_seconds: 120` sondowany domyślnymi 60 s dostawał fałszywy timeout, choć
router by poczekał. Teraz limit sondy to `max(--probe-timeout, limit modelu)`.

**8. Opis mówił „silnik nie odpowiedział" tam, gdzie nic nie wysłano** (egress, brak sekretu,
brak endpointu) — ta sama klasa błędu, którą naprawiono wcześniej dla kontroli katalogu.

**9. Pusta odpowiedź przy `finish_reason: length` obwiniana na model.** To skutek NASZEGO
limitu 32 tokenów sondy (model rozumujący zużywa go na preambułę), więc stan NIEZNANY
z wyjaśnieniem, nie problem blokujący.

**10. Nieoczekiwany wyjątek z `build_client` wywracał CAŁĄ diagnozę.** Komentarz „jedyny
powód, dla którego build_client zawodzi" był nieuprawniony — fabryka woła kod dostawcy
sekretów, a ten może zgłosić cokolwiek. Sprostowane i osłonięte.

**11. Kill-switch `security.secret_store.enabled` obchodzony przez sondę** — domyślne
`magazyn_dostepny=True` sprawiało, że diagnoza wydawałaby materiał z magazynu wyłączonego
po incydencie. Przekazywane jawnie, jak w `_build_git`.

**12. Brak endpointu kategoryzowany jako „404 z endpointu"** — instrukcja twierdziła, że
endpoint odpowiedział, choć nic nie wysłano. Własna kategoria.

**13. Brak śladu postępu** — narzędzie do diagnozowania zawieszeń samo milczało
kilkadziesiąt sekund na model.

### Wada SPRZED tej zmiany, którą sonda ujawniła

`_router_factory` w `husarz up` budował `ModelRouter(cfg)` **bez dostawcy sekretów**, więc
router dostawał `NullSecretsProvider`. Skutek: **każdy model z `api_key_ref` był w produkcji
nieużywalny** — `build_client` zgłaszał „Nie udało się rozwiązać sekretu klucza API" i żądanie
nie wychodziło. Dostarczona konfiguracja nie używa `api_key_ref`, więc nic tego nie wywoływało
i wada leżała niezauważona.

Ujawniła ją dopiero sonda: rozwiązywała klucz (bo dostała dostawcę) i meldowała OK dla drogi,
której router nie potrafił przejść — czyli fałszywe OK wynikające z tego, że narzędzie
pomiarowe było SPRAWNIEJSZE od mierzonego systemu. Naprawione razem z regresyjnym testem
`test_up_przekazuje_sekrety_do_routera`.

### Niezmienniki potwierdzone testem SKUTKU

| Niezmiennik | Test |
|---|---|
| `GET /api/doctor` NIGDY nie pyta modelu | `test_endpoint_API_NIE_zadaje_pytania_modelowi` |
| Bez `--probe` diagnoza nie ma czym zapytać (opt-in strukturalny) | `test_doctor_bez_probe_NIE_dostaje_sondy_glebokiej` |
| Bramka egress obowiązuje publiczną metodę sondy | `test_realna_sonda_NIE_wysyla_do_endpointu_spoza_allowlisty` |
| Sonda nie mutuje współdzielonej konfiguracji | `test_realna_sonda_nie_mutuje_wspoldzielonej_konfiguracji` |
| Znaki sterujące z odpowiedzi modelu nie trafiają na terminal | `test_sekwencje_sterujace_z_odpowiedzi_modelu_sa_usuwane` |
| Kill-switch magazynu sekretów obowiązuje diagnozę | `test_probe_respektuje_kill_switch_magazynu_sekretow` |
| Router produkcyjny potrafi rozwiązać `api_key_ref` | `test_up_przekazuje_sekrety_do_routera` |

Kontrola nośności: **23 mutacje, 23 czerwone** (w dwóch przebiegach). Dwie mutacje trafiły
najpierw w niewłaściwe wystąpienie wzorca (linie powtarzalne po sformatowaniu przez `black`
i trzy identyczne wywołania `_SchemeSecrets`) — po wycelowaniu w jednoznaczne sąsiedztwo
zaczerwieniły się. Jedna mutacja ujawniła wadę w moim WŁASNYM teście: `test_endpoint_API_NIE_
zadaje_pytania_modelowi` przechodził z niewłaściwego powodu, bo blokada DNS w `conftest`
sprawiała, że kontrola katalogu kończyła się stanem NIEZNANY i sonda głęboka nie była
osiągana. Test przepisany tak, żeby katalog się zgadzał — dopiero wtedy jest nośny.

### Ograniczenia — wprost

1. **Modele wskazywane wyłącznie jako `fallback` nie są sondowane** — `_role_modeli` mapuje
   czat, orkiestrację i `routing.agent_models`, a łańcuch fallbacków pomija. Dotyczy to także
   kontroli katalogu (stan sprzed tej zmiany). Zapisane w ROADMAP.
2. **Sonda pyta o odpowiedź, nie o jej jakość.** „Odpowiedział" znaczy tyle, że backend zwrócił
   niepustą treść w formacie OpenAI. Model odpowiadający bez sensu przejdzie kontrolę.
3. **Pomiar czasu jest jednorazowy.** Pierwsze żądanie wczytuje wagi, więc zgłoszony czas bywa
   o rząd wielkości wyższy od ustalonego. Komunikat naprawy mówi wprost, żeby powtórzyć sondę.
4. **Sonda głęboka nie jest dostępna w konsoli WWW** — świadomie (ADR-0024). Wymagałaby limitu
   tempa i osobnej zgody w konfiguracji; dziś nie ma ani jednego, ani drugiego.

## Etap 17j — pobieranie wag (`husarz bootstrap`)

Notatka weryfikacyjna. To **pierwsza droga, którą Husarz z własnej inicjatywy sięga do sieci
po treść** — w projekcie, którego pierwszą zasadą jest suwerenność danych i deny-all egress.
Trzeci poziom audytu wg tabeli w `CLAUDE.md`.

### Co dokładnie wychodzi do sieci

Dwa różne żądania, przez dwie różne bramki:

| Żądanie | Kto wykonuje | Bramka | Rozmiar |
|---|---|---|---|
| manifest modelu (rozmiar) | **Husarz** | `bootstrap.sources` + pin IP (ADR-0020) | zmierzone 857 B |
| pobranie wag | **silnik operatora** | `security.egress` (adres silnika) | gigabajty |

Husarz nie pobiera wag — prosi o to silnik. Konsekwencja jest praktyczna, nie retoryczna:
nie dotykamy plików wykonywalnych, nie weryfikujemy sum kontrolnych binarek, nie znamy
ścieżek instalacyjnych per system, nie potrzebujemy uprawnień administratora.

### Dlaczego dwie allowlisty

`bootstrap.sources` jest osobne od `security.egress.allowlist` i to jest sedno tej zmiany.
Gdyby wystarczała druga, **każda domena otwarta dla narzędzia `web` stawałaby się źródłem,
z którego Husarz gotów jest pobierać wagi** — a to zupełnie inna decyzja operatora.
Zależność nie działa też w drugą stronę: `check_endpoint_allowed` (router, `web`, wtyczki)
czyta wyłącznie `security.egress`, więc wpis w `bootstrap.sources` nie rozszczelnia deny-all.
Oba kierunki mają test.

Zapytanie o manifest przechodzi pin IP z `allow_loopback=False` i `allow_lan=False`. Rejestr
modeli jest w WAN, więc jego nazwa nie ma prawa rozwiązać się na adres wewnętrzny operatora
ani na metadane chmury — inaczej wpis w `bootstrap.sources` byłby dźwignią do skanowania
sieci wewnętrznej.

### Zgoda, która jest zgodą

1. **Rozmiar PRZED, nie w trakcie.** Ekran zgody podający GB byłby fikcją, gdyby liczbę
   poznawać ze strumienia pobierania — bajty już by leciały. Czytamy manifest (857 B), potem
   pytamy, dopiero potem prosimy silnik o wagi.
2. **Bez rozmiaru nie ma pobrania.** Model, którego rozmiaru nie da się ustalić, jest
   pokazany operatorowi WRAZ Z POWODEM, ale nie wchodzi do pobierania.
3. **Domyślna odpowiedź odmowna.** Enter naciśnięty odruchowo nie uruchamia transferu
   gigabajtów; `EOFError`/`KeyboardInterrupt` też znaczą „nie" (potok, usługa systemowa —
   tam nie ma komu wyrazić zgody).
4. **Airgap sprawdzany PRZED włącznikiem.** Operator ma usłyszeć, że zabrania profil, a nie
   że „wystarczy włączyć bootstrap" — ta druga odpowiedź sugerowałaby, że politykę da się
   obejść ustawieniem.
5. **Włączenie bez `registry` lub `sources` to błąd walidacji**, nie atrapa, która odmówi
   przy pierwszym użyciu.

### Weryfikacja SKUTKU

Uruchomione na działającej instalacji, nie tylko w testach:

- odmowa przy domyślnej konfiguracji repo (kod 1),
- ekran zgody z **realnym** rozmiarem z rejestru (0,99 GB dla `qwen2.5-coder:1.5b`, zgodne
  z „≈1 GB" w dokumentacji Ollamy) i odpowiedź „n" → nic nie pobrano,
- model spoza rejestru → komunikat odsyłający do `ollama create` (przypadek `husarz`),
- **ścieżka powodzenia pobierania** sprawdzona bez ściągania gigabajtów: poproszono silnik
  o model, który już ma w całości (`qwen2.5-coder:7b`) — strumień, parser i raportowanie
  postępu przeszły tę samą drogę co przy prawdziwym pobraniu (0,8 s, „100% (4.68 GB)"),
- błąd silnika (model nieznany rejestrowi) zwrócony jako komunikat, nie wyjątek,
- endpoint spoza allowlisty egress → odmowa przed wysłaniem czegokolwiek.

Kontrola nośności: **12 mutacji, 12 czerwonych** (zdjęta odmowa airgap, zignorowany
włącznik, pobieranie bez znajomości rozmiaru, ekran zgody bez sumy GB, ciche pominięcie
pozycji niepobieralnej, brak kontroli `bootstrap.sources`, błędna przestrzeń `library`,
każda odpowiedź brana za zgodę, brak terminala wywracający komendę, walidacja przepuszczająca
włączony bootstrap bez rejestru, `wykonaj()` ignorujące pobieralność).

Jedna mutacja ujawniła pułapkę w moim własnym teście: `model_copy(update=...)` **omija
walidację**, więc podstawienie profilu łańcuchem `"airgap"` zostawiało napis, a porównanie
`is Profile.AIRGAP` było fałszywe — test przechodziłby z niewłaściwego powodu. Ta sama
pułapka co przy `--probe-timeout`. Test podstawia teraz wartość enuma i sprawdza to asercją.

### Ograniczenia — wprost

1. **Nie weryfikujemy sum kontrolnych wag.** Robi to silnik przy pobieraniu (digesty warstw
   w manifeście). Dublowanie wymagałoby pobierania wag przez Husarza, czego decyzja
   z ADR-0025 zabrania.
2. **Modele powstające z Modelfile nie są pobieralne** — rejestr ich nie zna. Komenda mówi
   to wprost i odsyła do `ollama create`.
3. **Pobieranie nie ma limitu CAŁKOWITEGO czasu** (wagi legalnie schodzą kwadransami), tylko
   limit odczytu: 300 s bez ani jednego bajtu = połączenie uznane za zawieszone.
4. **Bootstrapu nie ma w konsoli WWW ani w API.** Ta sama decyzja co dla sondy głębokiej:
   operacja trwa i zużywa zasoby, więc jest operacją terminala.


## Etap 17k — limit tempa diagnozy przez API

Notatka weryfikacyjna. Domyka ograniczenie zapisane wprost w sekcji 17h jako nienaprawione.

**Dźwignia, którą to zamyka.** `GET /api/doctor` jest tani dla wywołującego (jedno żądanie),
a kosztowny dla instalacji: otwiera połączenie do KAŻDEGO endpointu z konfiguracji, z limitem
czasu na każdy. Rola z `diagnostics:read` mogła więc generować ruch wychodzący w tempie
ograniczonym wyłącznie własnym łączem — i to ruch kierowany do CUDZYCH silników, jeśli
operator wskazał zdalne endpointy.

**Limit sprawdzany PRZED sondowaniem.** To jest cała treść zabezpieczenia, nie szczegół
implementacyjny: 429 zwrócone po odpytaniu silników nie zmniejszyłoby ani jednego pakietu,
więc niczego by nie chroniło. Ma osobny test, który liczy zapytania sondy i sprawdza, że
żądanie ponad limit nie dołożyło ani jednego.

**Ogranicznik budowany RAZ, z konfiguracji startowej.** Gdyby powstawał przy każdej
przebudowie, `POST /api/config/runtime` zerowałby kubełek — wystarczyłoby przeplatać diagnozę
pustymi nadpisaniami, żeby limit przestał istnieć. Wywołujący `config:write` ma i tak szersze
uprawnienia, ale zabezpieczenie z tak łatwym obejściem jest gorsze niż jego brak, bo usypia.
Też ma test.

**Zamek na ograniczniku.** `RateLimiter` nie jest bezpieczny wątkowo, a FastAPI wykonuje
funkcje synchroniczne w puli wątków. Bez zamka dwa równoległe żądania mogłyby pobrać ten sam
token.

**Wartość domyślna dobrana pod człowieka**: 6/min to jedno wywołanie na dziesięć sekund —
swobodnie wystarcza operatorowi, który coś poprawił i klika „Sprawdź ponownie", a nie pozwala
robić z endpointu generatora ruchu. `None` wyłącza limit i jest świadomą REZYGNACJĄ
z zabezpieczenia (instalacja jednoosobowa na loopbacku), nie jego brakiem.

**Konsola odróżnia 429 od awarii.** Poprzedni wynik zostaje na ekranie, bo jest nadal
prawdziwy — diagnoza tylko odmówiła kolejnego przebiegu w tej minucie. Skasowanie listy
i czerwony błąd sugerowałyby, że narzędzie przestało działać.

Kontrola nośności: **6 mutacji, 6 czerwonych** (brak limitu; limit po sondowaniu; limit
z konfiguracji ignorowany; limitu nie da się wyłączyć; konsola traktuje 429 jak awarię;
limit pokazany kolorem błędu). Jedna mutacja ujawniła BRAK POKRYCIA — celowałem w test
obsługi 403, a gałąź 429 w konsoli nie miała żadnego. Dopisany. Sam ten test padł przy
pierwszym uruchomieniu, bo porównywał pozycję z PIERWSZYM wystąpieniem `<p class='err'>`,
które należy do zupełnie innej gałęzi (błąd sieci na górze funkcji) — zawężony do właściwego
bloku.

**Ograniczenie — wprost.** Limit jest GLOBALNY dla instalacji, nie per wywołujący. Przy
kilku operatorach jeden może wyczerpać pulę drugiemu. Limit per konto wymagałby wiązania
kubełka z `principal`, co ma sens dopiero przy instalacji wieloosobowej — zapisane w ROADMAP.

## Etap 17l — sprostowanie: limit tempa NIE był przesłanką do rozszerzenia kręgu ról

Notatka powstała po panelu oceniającym decyzję „kto ma widzieć diagnozę". Wniosek panelu
jest przeciwny do mojej skłonności i to jest jego wartość.

### Skąd pytanie

Rola `viewer` nie ma `diagnostics:read`. Uzasadnienie zapisane przy tej decyzji brzmiało:
**„podgląd nie wysyła pakietów"**. Po wprowadzeniu limitu tempa (Etap 17k) wyglądało to na
argument nieaktualny — a sam `viewer` widzi przecież dziennik audytu, więc granica sprawiała
wrażenie postawionej w złym miejscu.

### Co ustalił panel

Trzy niezależne stanowiska (rozszerzyć `viewer` / utworzyć rolę `noc` / zostawić), każde
oceniane przez trzy soczewki (bezpieczeństwo, operacyjna, spójność):

| Stanowisko | Średnia | Bezpieczne? |
|---|---:|---|
| zostaw jak jest | **7,3/10** | tak |
| nowa rola `noc` | 7,0/10 | tak |
| rozszerz `viewer` | 4,7/10 | **nie** |

**Decyzja: granica zostaje.** Ale uzasadnienie było niepełne i wymagało sprostowania
w czterech miejscach (`rbac.py`, `docs/API.md`, `docs/LAUNCHER.md`, ta notatka).

### Sprostowanie — powody są DWA, nie jeden

1. **Wolumen ruchu** — pierwotny argument. **Ten powód zniknął** wraz z limitem tempa.
   Sufit ruchu wychodzącego instalacji to `max_requests_per_minute` × liczba endpointów,
   **niezależnie od tego, ile ról ma uprawnienie**. Dopisanie roli podnosi go o zero pakietów.
   Argument o pakietach przestał więc rozróżniać role — i to jest właśnie to, co
   sprawiało wrażenie, że decyzja jest do zmiany.
2. **Ujawnienie aktualnej topologii** — powód, który **nie zmienił się wcale** i jest dziś
   jedynym uzasadnieniem granicy. Odpowiedź diagnozy niesie adresy i porty silników, ścieżki
   katalogów operatora oraz KATALOG silnika — czyli nazwy modeli spoza konfiguracji Husarza,
   na współdzielonym serwerze wnioskowania także cudzych. Dzieje się to na ścieżce
   **szczęśliwej**, nie tylko przy awarii. Sprawdzone uruchomieniem: przy wszystkich
   kontrolach OK odpowiedź nadal zawiera pełne adresy silników.

### Trzeci powód, który POWSTAŁ razem z limitem

Limit jest **globalny** dla instalacji. Konto podglądowe odpytujące co dziesięć sekund trzyma
kubełek na zerze, a operator klikający „Sprawdź ponownie" w trakcie awarii dostaje 429 — czyli
traci diagnozę dokładnie wtedy, gdy jest potrzebna. Limit usunął argument o **amplifikacji**
i stworzył argument o **wygłodzeniu**.

Stąd twardy warunek wstępny: **jakiekolwiek rozszerzenie `diagnostics:read` wymaga NAJPIERW
kubełka per `principal` z rezerwą dla `operator`/`admin`.** W ROADMAP przestaje to być
pozycją „do rozważenia" obok innych.

### Sprostowanie do brief-u panelu (moja pomyłka)

Twierdziłem, że `viewer` „widzi PEŁNY dziennik audytu". Nieprawda — `api/audit_view.py` działa
deny-by-default i przepuszcza wyłącznie wąską allowlistę (`tool.call` → `tool`/`action`/`ok`
i dwie inne akcje). Asymetria, na którą się powoływałem, jest więc mniejsza, niż napisałem.
Nadal istnieje (audyt to zapis rozliczalności), ale nie w takiej skali.

### Co zmieniono w tym kroku

| Zmiana | Po co |
|---|---|
| Sprostowane uzasadnienie w `rbac.py` i dwóch plikach docs | niepełny powód stał w czterech miejscach |
| `config/security.yaml` dostał sekcję `diagnostics:` | zabezpieczenie niewidoczne w plikach konfiguracji nie zostanie ani dostrojone, ani świadomie wyłączone |
| Wejście w zakładkę **nie odpala** sondowania | kliknięcie w nawigacji nie jest świadomym żądaniem wysłania pakietów; obniża zużycie wspólnej puli niezależnie od decyzji o rolach |
| Wynik niesie znacznik czasu | pokazujemy stan sprzed chwili — operator ma wiedzieć, sprzed której. Stary pomiar udający bieżący to ta sama klasa nieprawdy, co zaokrąglanie „nie wiem" do „w porządku" |
| `test_rola_viewer_NIE_widzi_diagnozy` liczy zapytania sondy | sam kod 403 jest deklaracją; gdyby bramka RBAC stała ZA sondowaniem, odmowa przychodziłaby po wygenerowaniu ruchu |

Nośność: **3 mutacje, 3 czerwone** (wejście w zakładkę znów odpytuje; wynik bez znacznika
czasu; bramka diagnozy przestawiona na `config:read`).

### Zapisane do rozważenia, nie zrobione

Zawężenie ŁADUNKU odpowiedzi API (nie CLI): pełne endpointy i ścieżki bezwzględne są
potrzebne operatorowi w terminalu, ale w odpowiedzi HTTP mogłyby być skracane. Obniżyłoby to
stawkę całej tej dyskusji i uczyniłoby przyszłą rolę monitoringu bezpieczniejszą z definicji.
Wymaga własnego testu skutku — w ROADMAP.
