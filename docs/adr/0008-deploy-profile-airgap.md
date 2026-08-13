# ADR-0008: Wdrożenie i profile (dev/prod/airgap)

- Status: przyjęty
- Data: 2026-08-13
- Etap: 6

## Kontekst

Etap 6 dostarcza materiały wdrożeniowe: obrazy kontenerów, Docker Compose (trzy
profile) i manifesty Kubernetes z NetworkPolicy deny-all, plus pełne CI. Wymóg
nadrzędny: **suwerenność danych** — profil `airgap` musi działać bez WAN, a
domyślną postawą sieci jest **deny-all egress**. Wszystko testowalne bez klastra.

## Decyzja

### Dwa obrazy, oba non-root

`husarz-api` (wieloetapowy build, chudy runtime, UID 1000, healthcheck na
`/api/health`) oraz `husarz-sandbox` (minimalny obraz narzędzi uruchamiany z
`--network none`, `--cap-drop ALL`, `--read-only`). `.dockerignore` wyklucza wagi,
sekrety i dane z kontekstu builda.

### Profile jako nakładki Compose

Baza (`deploy/compose/docker-compose.base.yml`) definiuje usługi w sieci
**wewnętrznej** (`internal: true`). Profile to nakładki:
- **dev** — samowystarczalny `docker-compose.yaml` w repo; API tylko na loopbacku
  hosta, `--allow-insecure` (świadomie, bo publikacja portu jest ograniczona do
  `127.0.0.1`).
- **prod** — dodaje proxy Caddy (jedyny komponent z siecią brzegową/WAN dla ACME);
  API wymaga tokenu, bez publikacji portu na hosta.
- **airgap** — brak proxy i sieci brzegowej; dostęp tylko przez loopback; obrazy
  lokalne (`pull_policy: never`).

### Uwierzytelnianie a konteneryzacja: `--allow-insecure`

Kontener musi nasłuchiwać na `0.0.0.0`, co koliduje z fail-closed launchera (odmowa
nasłuchu poza loopbackiem bez tokenu). Zamiast osłabiać domyślną regułę dodaliśmy
**jawny opt-out** `--allow-insecure` (z ostrzeżeniem). Prod/airgap go NIE używają —
dostarczają token; wektor otwarcia jest zatem świadomą, widoczną decyzją, a nie
domyślnym zachowaniem.

### Kubernetes: deny-all + hardening

`default-deny-all` (ingress+egress) na cały namespace; wąskie reguły zezwalające
otwierają wyłącznie ingress z kontrolera, DNS i egress API→dane. **Brak reguły do
`0.0.0.0/0`**. Deployment: `runAsNonRoot`, `readOnlyRootFilesystem`, drop ALL caps,
seccomp `RuntimeDefault`, bez tokenu SA. Sekrety wyłącznie przez managera sekretów
(referencja w ConfigMap, nie wartość).

### CI pełne + testy niezmienników wdrożeń

GitHub i GitLab uruchamiają lint/typy/testy, gitleaks, pip-audit (SCA), hadolint i
build obrazu. Dodatkowo `tests/security/test_deploy_invariants.py` parsuje pliki
compose/k8s i egzekwuje niezmienniki (deny-all, non-root, loopback, brak WAN w
airgapie, placeholdery w szablonach sekretów) — bez uruchamiania Dockera/klastra.

## Konsekwencje

- (+) Trzy profile o jasnej, testowanej postawie bezpieczeństwa; airgap bez WAN.
- (+) Regres w manifestach (np. otwarcie egressu, root, ekspozycja API) łamie testy.
- (+) Sekrety nigdy w repo/obrazach; referencje w configu/ConfigMap.
- (−) Realne uruchomienie (klaster z CNI wspierającym NetworkPolicy, gVisor dla
  sandboxa, Vault z unseal) wymaga środowiska docelowego — tu weryfikujemy statycznie.
- (−) `husarz-sandbox` i pełny stos danych (pgvector/RAG) czekają na wpięcie realnego
  wykonania narzędzi i backendu RAG (pozostałości Etapu 3).

## Alternatywy odrzucone

- **Osłabienie fail-closed dla kontenerów**: cichy otwarty bind — odrzucone na rzecz
  jawnego `--allow-insecure`.
- **Sekrety w ConfigMap/obrazie**: łamie zasadę „sekrety spoza repo" — odrzucone.
- **Jeden plik compose z `profiles:`**: nie pozwala przełączać `internal` sieci per
  profil — wybrano nakładki base+override.
