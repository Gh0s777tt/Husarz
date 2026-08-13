# deploy/ — wdrożenia (Etap 6)

Katalog na materiały wdrożeniowe. Wypełniany w **Etapie 6**.

- `compose/` — pliki docker-compose per profil (dev/prod/airgap).
- `k8s/` — manifesty Kubernetes z **NetworkPolicy deny-all** (domyślny brak egress).

Zasady wdrożenia zgodne z modelem bezpieczeństwa: deny-all egress, sekrety z
Vault/SOPS, mTLS między usługami, szyfrowanie at-rest. Szczegóły:
[../SECURITY.md](../SECURITY.md), [../docs/BEZPIECZENSTWO.md](../docs/BEZPIECZENSTWO.md).
