"""Niezmienniki bezpieczeństwa artefaktów wdrożeniowych (Etap 6).

Parsujemy pliki compose/k8s i egzekwujemy twarde wymagania modelu bezpieczeństwa
BEZ uruchamiania klastra/Dockera: deny-all egress (airgap bez WAN), NetworkPolicy
default-deny, non-root + read-only rootfs, brak ekspozycji API poza loopbackiem,
brak prawdziwych sekretów w szablonach. Regres w manifestach = błąd testu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from husarz.config import load_config
from husarz.launcher.cli import _cmd_up, _resolve_api_token, build_parser

pytestmark = pytest.mark.security

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "deploy" / "compose"
_K8S = _ROOT / "deploy" / "k8s"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_all(path: Path) -> list[dict[str, Any]]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


def _api_ports(compose: dict[str, Any]) -> list[str]:
    return list(compose.get("services", {}).get("api", {}).get("ports", []) or [])


# --- Compose: ekspozycja API i izolacja sieci -------------------------------


def test_dev_compose_publishes_api_only_on_loopback() -> None:
    compose = _load(_ROOT / "docker-compose.yaml")
    ports = _api_ports(compose)
    assert ports, "dev api powinno publikować port"
    for entry in ports:
        assert str(entry).startswith("127.0.0.1:"), f"api wystawione poza loopback: {entry}"


def test_published_ports_are_not_paired_with_internal_only_network() -> None:
    """Docker CICHO wyłącza publikowanie portów dla sieci `internal: true`.

    Ta sprzeczność była w dostarczonym profilu dev: `ports: 127.0.0.1:8000:8000` obok sieci
    `internal: true`. `docker compose up` kończyło się kontenerem `healthy`, do którego NIE
    DAŁO SIĘ wejść z hosta — Docker raportował „8000/tcp" zamiast „127.0.0.1:8000->8000/tcp",
    a deklaracja `ports` była martwa. Poprzednia wersja tego testu asertowała OBIE wartości
    naraz, nie zauważając, że się wykluczają: statyczna asercja przechodziła, a rzecz nie
    działała.

    W profilu prod sprzeczności nie ma, bo ruch mostkuje Caddy w sieci `husarz_edge`
    (nie-internal), a samo API pozostaje wewnętrzne i portów nie publikuje.
    """
    _sprawdz_brak_sprzecznosci(_load(_ROOT / "docker-compose.yaml"))


def test_overlay_profiles_have_no_port_contradiction() -> None:
    """Ta sama pułapka była w nakładce `airgap` — i pierwsza wersja testu jej NIE łapała,
    bo sprawdzała wyłącznie główny plik. Nakładki scalamy z base, bo `internal` bywa
    nadpisywane właśnie tam."""
    base = _load(_COMPOSE / "docker-compose.base.yml")
    for nakladka in ("docker-compose.prod.yml", "docker-compose.airgap.yml"):
        scalone = _scal(base, _load(_COMPOSE / nakladka))
        _sprawdz_brak_sprzecznosci(scalone, zrodlo=nakladka)


def _scal(base: dict, nakladka: dict) -> dict:
    """Płytkie scalenie sekcji `services`/`networks` — tak jak robi to compose."""
    wynik = {k: dict(v) if isinstance(v, dict) else v for k, v in base.items()}
    for sekcja in ("services", "networks"):
        scalona = dict(wynik.get(sekcja) or {})
        for nazwa, spec in (nakladka.get(sekcja) or {}).items():
            biezace = dict(scalona.get(nazwa) or {})
            biezace.update(spec or {})
            scalona[nazwa] = biezace
        wynik[sekcja] = scalona
    return wynik


def _sprawdz_brak_sprzecznosci(compose: dict, zrodlo: str = "docker-compose.yaml") -> None:
    """Usługa publikująca porty nie może być WYŁĄCZNIE w sieci `internal`."""
    siec_wewnetrzna = {
        nazwa
        for nazwa, spec in (compose.get("networks") or {}).items()
        if isinstance(spec, dict) and spec.get("internal") is True
    }
    for nazwa_uslugi, usluga in (compose.get("services") or {}).items():
        if not usluga.get("ports"):
            continue
        sieci = set(usluga.get("networks") or [])
        assert not (sieci and sieci <= siec_wewnetrzna), (
            f"{zrodlo}: usługa '{nazwa_uslugi}' publikuje porty, ale jest WYŁĄCZNIE w sieci "
            f"internal ({sorted(sieci)}) — Docker po cichu wyłączy publikowanie i kontener "
            "będzie nieosiągalny z hosta"
        )


def test_dev_compose_uses_allow_insecure_without_token() -> None:
    # Dev nie ma tokenu → świadome --allow-insecure (a nie ciche otwarcie).
    cmd = _load(_ROOT / "docker-compose.yaml")["services"]["api"]["command"]
    assert "--allow-insecure" in cmd


def test_base_internal_network_and_token_required() -> None:
    base = _load(_COMPOSE / "docker-compose.base.yml")
    assert base["networks"]["husarz_internal"]["internal"] is True
    env = base["services"]["api"]["environment"]
    # Token API wstrzykiwany przez env (referencja w configu) — wymagany (":?").
    assert "HUSARZ_API_TOKEN" in env
    assert ":?" in str(env["HUSARZ_API_TOKEN"]), "token musi być wymagany (fail przy braku)"


def test_prod_api_requires_token_no_insecure() -> None:
    prod = _load(_COMPOSE / "docker-compose.prod.yml")
    api_cmd = prod["services"]["api"]["command"]
    assert "--allow-insecure" not in api_cmd, "prod nie może obchodzić uwierzytelniania"
    assert "--profile" in api_cmd and "prod" in api_cmd
    # Do WAN (ACME) dopuszczone jest WYŁĄCZNIE proxy — sieć brzegowa nie jest internal.
    assert "husarz_edge" in prod["networks"]
    assert prod["networks"]["husarz_edge"].get("internal", False) is False
    # API nie dołącza do sieci brzegowej (pozostaje wewnętrzne).
    assert "husarz_edge" not in (prod["services"]["api"].get("networks", []) or [])
    assert "husarz_edge" in prod["services"]["proxy"]["networks"]


def test_airgap_api_loopback_only_and_no_wan() -> None:
    airgap = _load(_COMPOSE / "docker-compose.airgap.yml")
    for entry in _api_ports(airgap):
        assert str(entry).startswith("127.0.0.1:"), f"airgap api poza loopback: {entry}"
    api_cmd = airgap["services"]["api"]["command"]
    assert "airgap" in api_cmd
    assert "--allow-insecure" not in api_cmd
    # Airgap NIE definiuje proxy brzegowego (żadnego dostępu do WAN).
    assert "proxy" not in airgap.get("services", {})
    # Nie dodaje sieci brzegowej.
    assert "husarz_edge" not in (airgap.get("networks", {}) or {})


# --- Kubernetes: hardening Poda i polityki sieciowe -------------------------


def test_k8s_deployment_hardened_non_root_readonly() -> None:
    dep = _load(_K8S / "deployment.yaml")
    spec = dep["spec"]["template"]["spec"]
    assert spec["securityContext"]["runAsNonRoot"] is True
    container = spec["containers"][0]
    sc = container["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"]["drop"] == ["ALL"]
    # Bez uruchamiania z rootem i bez montowania tokenu SA (mniejsza powierzchnia).
    assert spec.get("automountServiceAccountToken") is False


def test_k8s_default_deny_all_present() -> None:
    deny = _load(_K8S / "networkpolicy-default-deny.yaml")
    assert deny["kind"] == "NetworkPolicy"
    assert deny["spec"]["podSelector"] == {}  # dotyczy wszystkich Podów
    assert set(deny["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    # Brak reguł ingress/egress = pełny deny (żadnej z tych sekcji).
    assert "ingress" not in deny["spec"]
    assert "egress" not in deny["spec"]


def test_k8s_allow_policies_do_not_open_wan() -> None:
    docs = _load_all(_K8S / "networkpolicy-allow.yaml")
    assert docs, "oczekiwano reguł zezwalających"
    for doc in docs:
        for rule in doc["spec"].get("egress", []):
            for to in rule.get("to", []):
                block = to.get("ipBlock", {})
                # Żadna reguła nie może otwierać egressu do całego internetu.
                assert block.get("cidr") not in ("0.0.0.0/0", "::/0"), "egress do WAN zabroniony"


def test_k8s_ingress_uses_tls() -> None:
    ing = _load(_K8S / "ingress.yaml")
    assert ing["spec"]["tls"], "ingress musi wymuszać TLS"


# --- Sekrety: szablony bez prawdziwych wartości -----------------------------


def test_secret_example_has_only_placeholders() -> None:
    secret = _load(_K8S / "secret.example.yaml")
    for key, value in secret.get("stringData", {}).items():
        assert value == "CHANGE_ME", f"sekret {key} musi być placeholderem, nie wartością"


# --- Poprawki z przeglądu Etapu 6 -------------------------------------------


@pytest.mark.parametrize(
    "path",
    [_ROOT / "docker-compose.yaml", _COMPOSE / "docker-compose.base.yml"],
)
def test_compose_api_container_hardened(path: Path) -> None:
    # Kontener API musi lustrzać hardening k8s (non-root, rootfs RO, brak eskalacji).
    api = _load(path)["services"]["api"]
    assert api["read_only"] is True
    assert api["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in api["security_opt"]
    assert api["user"] == "1000:1000"


def test_base_compose_sets_api_token_ref() -> None:
    # Blocker z przeglądu: bez referencji launcher fail-closed nie wystartuje prod/airgap.
    env = _load(_COMPOSE / "docker-compose.base.yml")["services"]["api"]["environment"]
    assert env.get("HUSARZ_SECURITY__AUTH__API_TOKEN_REF") == "env:HUSARZ_API_TOKEN"


def test_prod_effective_config_resolves_token_and_starts(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # E2E: tak jak compose wstrzykuje ENV — ref + wartość — konfiguracja rozwiązuje
    # token, a launcher NIE odmawia nasłuchu 0.0.0.0 (blocker naprawiony).
    monkeypatch.setenv("HUSARZ_SECURITY__AUTH__API_TOKEN_REF", "env:HUSARZ_API_TOKEN")
    monkeypatch.setenv("HUSARZ_API_TOKEN", "prod-secret-123")
    config = load_config(repo_config_dir)
    assert config.security.auth.api_token_ref == "env:HUSARZ_API_TOKEN"
    assert _resolve_api_token(config) == "prod-secret-123"

    import uvicorn

    served: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app))
    prompts = repo_config_dir.parent / "prompts"
    args = build_parser().parse_args(
        [
            "up",
            "--config",
            str(repo_config_dir),
            "--host",
            "0.0.0.0",  # noqa: S104 - profil prod nasłuchuje szeroko, chroniony tokenem
            "--profile",
            "prod",
            "--prompts",
            str(prompts),
        ]
    )
    assert _cmd_up(args) == 0  # z tokenem: brak odmowy (kod 2)
    assert "app" in served


def test_redis_requires_password() -> None:
    cmd = _load(_COMPOSE / "docker-compose.base.yml")["services"]["redis"]["command"]
    assert "--requirepass" in cmd


def test_compose_images_are_pinned_not_latest() -> None:
    services = _load(_COMPOSE / "docker-compose.base.yml")["services"]
    for name, svc in services.items():
        image = svc.get("image")
        if image is not None:
            assert not image.endswith(":latest"), f"{name}: obraz nieprzypięty ({image})"


def test_airgap_pins_pull_policy_never_on_all_services() -> None:
    services = _load(_COMPOSE / "docker-compose.airgap.yml")["services"]
    for name, svc in services.items():
        assert svc.get("pull_policy") == "never", f"{name}: brak pull_policy: never w airgapie"


def test_k8s_namespace_enforces_restricted_pod_security() -> None:
    ns = _load(_K8S / "namespace.yaml")
    assert ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"] == "restricted"


def test_k8s_probes_target_health_endpoint() -> None:
    container = _load(_K8S / "deployment.yaml")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/api/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/api/health"


def test_k8s_deployment_has_no_allow_insecure() -> None:
    # Profil prod w k8s nigdy nie może obchodzić uwierzytelniania.
    container = _load(_K8S / "deployment.yaml")["spec"]["template"]["spec"]["containers"][0]
    assert "--allow-insecure" not in container["args"]


def test_compose_image_tag_matches_project_version() -> None:
    """Domyślny tag obrazu w compose MUSI odpowiadać wersji projektu.

    Plik przypinał `husarz-api:0.1.0`, gdy projekt był już w 0.14.0 — compose buduje obraz
    i nadaje mu tę etykietę, więc wdrożony artefakt kłamał o wersji. Bez tego testu rozjazd
    wraca przy każdym wydaniu, bo nikt nie pamięta o pliku wdrożeniowym.
    """
    from husarz import __version__

    tresc = (_COMPOSE / "docker-compose.base.yml").read_text(encoding="utf-8")
    assert (
        f"husarz-api:${{HUSARZ_IMAGE_TAG:-{__version__}}}" in tresc
    ), f"domyślny tag obrazu w docker-compose.base.yml nie zgadza się z wersją {__version__}"
