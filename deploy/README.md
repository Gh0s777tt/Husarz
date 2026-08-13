# deploy/ — wdrożenia (Etap 6)

Materiały wdrożeniowe Husarza. Pełny przewodnik: [../docs/DEPLOY.md](../docs/DEPLOY.md).

- `compose/` — nakładki Docker Compose dla profili **prod**/**airgap** (baza +
  override). Profil **dev** to samowystarczalny `../docker-compose.yaml` w repo.
- `k8s/` — manifesty Kubernetes (Kustomize) z **NetworkPolicy deny-all**, hardened
  Deployment (non-root, read-only rootfs), Ingress TLS i szablonem sekretów.

Zasady zgodne z modelem bezpieczeństwa: deny-all egress, sekrety z Vault/SOPS/
External Secrets (nie w repo), mTLS/TLS, szyfrowanie at-rest, non-root. Niezmienniki
są testowane statycznie: [../tests/security/test_deploy_invariants.py](../tests/security/test_deploy_invariants.py).
Szczegóły: [../SECURITY.md](../SECURITY.md), [../docs/BEZPIECZENSTWO.md](../docs/BEZPIECZENSTWO.md).
