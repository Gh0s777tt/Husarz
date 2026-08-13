# Wdrożenie (Etap 6)

Husarz wdraża się w trzech profilach — **dev**, **prod**, **airgap** — przez
Docker Compose lub Kubernetes. Wszystkie profile trzymają się modelu bezpieczeństwa:
**deny-all egress**, sekrety spoza repo, non-root, audyt niemodyfikowalny. Profil
`airgap` działa **bez dostępu do WAN**.

## Obrazy

| Obraz | Plik | Rola |
|-------|------|------|
| `husarz-api` | [`Dockerfile`](../Dockerfile) | Rdzeń: REST API + konsola (`husarz up`). Non-root, chudy runtime. |
| `husarz-sandbox` | [`docker/husarz-sandbox.Dockerfile`](../docker/husarz-sandbox.Dockerfile) | Sandbox narzędzi (uruchamiany z `--network none`, `--cap-drop ALL`). |

```bash
docker build -t husarz-api:latest .
docker build -f docker/husarz-sandbox.Dockerfile -t husarz-sandbox:latest .
```

Obrazy nie zawierają wag, sekretów ani danych (patrz [`.dockerignore`](../.dockerignore)).

## Docker Compose

### dev (loopback, bez uwierzytelniania)

Samowystarczalny plik w katalogu repo — API tylko na loopbacku hosta:

```bash
docker compose up --build
# konsola: http://127.0.0.1:8000/
```

Wewnątrz kontenera API nasłuchuje na `0.0.0.0`, ale port publikowany jest wyłącznie
na `127.0.0.1`; stąd świadome `--allow-insecure`. Bez tokenu, do pracy lokalnej.

### prod (TLS, token API, usługi danych)

```bash
cp deploy/compose/.env.example deploy/compose/.env   # uzupełnij BEZPIECZNIE
docker compose \
  -f deploy/compose/docker-compose.base.yml \
  -f deploy/compose/docker-compose.prod.yml up -d
```

- Proxy brzegowe **Caddy** kończy TLS dla `${HUSARZ_PUBLIC_HOST}` (domyślnie
  `husarzai.pl`) i jest **jedynym** komponentem z dostępem do WAN (ACME).
- API wymaga nagłówka `Authorization: Bearer <token>` (token z `HUSARZ_API_TOKEN`,
  referencja `security.auth.api_token_ref=env:HUSARZ_API_TOKEN`).
- Usługi danych (Postgres+pgvector, Redis, MinIO, Vault) są w sieci **wewnętrznej**
  (`internal: true`) — brak trasy do WAN.

Wskazanie własnej domeny: `HUSARZ_PUBLIC_HOST=twoja-domena.pl` w `.env`.

### airgap (bez WAN)

```bash
docker compose \
  -f deploy/compose/docker-compose.base.yml \
  -f deploy/compose/docker-compose.airgap.yml up -d
```

- **Brak proxy/ACME** i brak sieci brzegowej — żaden Pod/kontener nie dosięga WAN.
- Dostęp do API wyłącznie przez loopback hosta (`127.0.0.1:8000`).
- Profil `airgap` w configu wymusza (walidacja krzyżowa schematu): `egress=deny`,
  pustą allowlistę, brak sieci sandboxa, **lokalne endpointy modeli**. Obrazy muszą
  być obecne lokalnie (`pull_policy: never`).

## Kubernetes

Manifesty w [`deploy/k8s/`](../deploy/k8s), spięte przez Kustomize:

```bash
kubectl apply -k deploy/k8s
```

Zawartość i niezmienniki:

- **NetworkPolicy default-deny** (ingress+egress) na cały namespace; wąskie reguły
  zezwalające otwierają tylko: ingress do API z kontrolera nginx, DNS, oraz egress
  API→usługi danych. **Brak reguły do `0.0.0.0/0`** (deny-all egress).
- **Deployment** hardened: `runAsNonRoot`, `readOnlyRootFilesystem`,
  `allowPrivilegeEscalation: false`, `capabilities: drop [ALL]`, seccomp
  `RuntimeDefault`, bez montowania tokenu ServiceAccount; limity CPU/RAM; sondy
  liveness/readiness na `/api/health`.
- **Ingress** wymusza TLS (cert-manager) dla `husarzai.pl` — zmień host na własny.
  k8s Ingress wymaga literału hosta (brak interpolacji ENV jak w compose), więc
  domena występuje w `ingress.yaml` w dwóch miejscach (`tls.hosts` i `rules.host`) —
  zmieniając ją, **zmień oba** (albo podstaw przez Kustomize `replacements`).
- Namespace ma etykietę Pod Security `enforce: restricted`.

> Uwaga: w Compose profil prod/airgap **wymaga** `HUSARZ_API_TOKEN` oraz
> `HUSARZ_REDIS_PASSWORD` (i haseł Postgres/MinIO) — bez nich `docker compose`
> przerwie z czytelnym błędem. Baza automatycznie ustawia referencję
> `HUSARZ_SECURITY__AUTH__API_TOKEN_REF=env:HUSARZ_API_TOKEN`, więc launcher rozwiąże
> token i wystartuje na `0.0.0.0` bez `--allow-insecure`.

### Sekrety

Nigdy nie commituj prawdziwego `Secret`. [`secret.example.yaml`](../deploy/k8s/secret.example.yaml)
to szablon — dostarcz wartości przez **External Secrets Operator**, **Sealed Secrets**
albo **Vault Agent Injector**. `configmap.yaml` trzyma wyłącznie *referencję*
(`HUSARZ_SECURITY__AUTH__API_TOKEN_REF=env:HUSARZ_API_TOKEN`), nie sam token.

## CI/CD

- GitHub Actions ([`.github/workflows/ci.yaml`](../.github/workflows/ci.yaml)) i
  GitLab CI ([`.gitlab-ci.yml`](../.gitlab-ci.yml)): lint (ruff), format (black),
  typy (mypy `--strict`), testy (pytest), **gitleaks** (sekrety), **pip-audit** (SCA),
  **hadolint** + build obrazu.
- Niezmienniki wdrożeń są testowane bez klastra: [`tests/security/test_deploy_invariants.py`](../tests/security/test_deploy_invariants.py)
  parsuje compose/k8s i egzekwuje deny-all/non-root/loopback/brak-WAN.

Decyzje projektowe: [ADR-0008](adr/0008-deploy-profile-airgap.md).
