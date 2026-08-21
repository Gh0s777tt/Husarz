"""Manifesty k8s — spójność po ZBUDOWANIU przez kustomize (Etap 6).

`tests/security/test_deploy_invariants.py` parsuje surowe pliki. To nie wystarcza, bo
kustomize je PRZEKSZTAŁCA (namespace, etykiety, selektory), a na klastrze liczy się wynik
tego przekształcenia. Klasa wad wychwytywana tutaj — selektor, który nie trafia w pod;
Ingress wskazujący nieistniejącą usługę; `targetPort` bez odpowiadającego portu kontenera —
przechodzi parsowanie surowego YAML-a i wywala się dopiero przy `kubectl apply`.

Testy używają `kubectl kustomize`, żeby NIE odtwarzać semantyki kustomize po swojemu —
własna reimplementacja mogłaby się rozjechać i dawać fałszywe poczucie bezpieczeństwa.
Bez `kubectl` są pomijane z czytelnym powodem.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.integration


def _kubectl() -> str | None:
    return shutil.which("kubectl")


wymaga_kubectl = pytest.mark.skipif(_kubectl() is None, reason="wymaga kubectl (buduje kustomize)")


def _zbuduj() -> tuple[list[dict[str, Any]], str]:
    """Buduje overlay i zwraca (zasoby, ostrzeżenia na stderr)."""
    kubectl = _kubectl()
    assert kubectl is not None
    wynik = subprocess.run(  # noqa: S603 - pełna ścieżka z `which`, argumenty stałe
        [kubectl, "kustomize", "deploy/k8s"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert wynik.returncode == 0, wynik.stderr
    zasoby = [d for d in yaml.safe_load_all(wynik.stdout) if d]
    return zasoby, wynik.stderr


def _jeden(zasoby: list[dict[str, Any]], rodzaj: str) -> dict[str, Any]:
    pasujace = [z for z in zasoby if z.get("kind") == rodzaj]
    assert len(pasujace) == 1, f"oczekiwano dokładnie jednego {rodzaj}, jest {len(pasujace)}"
    return pasujace[0]


@wymaga_kubectl
def test_overlay_builds_without_deprecation_warnings() -> None:
    """Przestarzałe pole dziś ostrzega, jutro przestaje działać — nie chcemy go w repo.

    Konkretnie chodziło o `commonLabels`, które NIE TYLKO jest przestarzałe, ale też
    wstrzykuje etykiety do SELEKTORÓW. Selektor Deploymentu jest niemodyfikowalny po
    utworzeniu, więc każda przyszła zmiana tego pola zablokowałaby aktualizację
    istniejącego wdrożenia (`field is immutable`).
    """
    _, ostrzezenia = _zbuduj()
    assert "deprecated" not in ostrzezenia.lower(), ostrzezenia.strip()


@wymaga_kubectl
def test_all_selectors_match_pod_labels() -> None:
    """Selektor, który nie trafia w pod, daje usługę bez endpointów i politykę bez skutku."""
    zasoby, _ = _zbuduj()
    etykiety = _jeden(zasoby, "Deployment")["spec"]["template"]["metadata"]["labels"]

    def trafia(selektor: dict[str, str]) -> bool:
        return all(etykiety.get(k) == v for k, v in selektor.items())

    assert trafia(_jeden(zasoby, "Deployment")["spec"]["selector"]["matchLabels"])
    assert trafia(_jeden(zasoby, "Service")["spec"]["selector"])
    for polityka in [z for z in zasoby if z["kind"] == "NetworkPolicy"]:
        selektor = polityka["spec"].get("podSelector", {}).get("matchLabels") or {}
        # Pusty selektor obejmuje WSZYSTKIE pody — tak działa `default-deny-all`.
        assert not selektor or trafia(selektor), polityka["metadata"]["name"]


@wymaga_kubectl
def test_ingress_points_at_existing_service_and_port() -> None:
    zasoby, _ = _zbuduj()
    usluga = _jeden(zasoby, "Service")
    nazwy_portow = {p.get("name") for p in usluga["spec"]["ports"]}
    numery_portow = {p["port"] for p in usluga["spec"]["ports"]}
    for regula in _jeden(zasoby, "Ingress")["spec"]["rules"]:
        for sciezka in regula["http"]["paths"]:
            backend = sciezka["backend"]["service"]
            assert backend["name"] == usluga["metadata"]["name"]
            port = backend["port"]
            assert port.get("name") in nazwy_portow or port.get("number") in numery_portow


@wymaga_kubectl
def test_service_target_port_matches_container_port() -> None:
    """`targetPort` bez odpowiadającego portu kontenera daje usługę, która nic nie serwuje."""
    zasoby, _ = _zbuduj()
    kontener = _jeden(zasoby, "Deployment")["spec"]["template"]["spec"]["containers"][0]
    porty_kontenera = {p["containerPort"] for p in kontener["ports"]}
    nazwy_kontenera = {p.get("name") for p in kontener["ports"]}
    for port in _jeden(zasoby, "Service")["spec"]["ports"]:
        cel = port.get("targetPort", port["port"])
        assert cel in porty_kontenera or cel in nazwy_kontenera, cel


@wymaga_kubectl
def test_deny_all_network_policy_is_present_and_covers_everything() -> None:
    """Niezmiennik nadrzędny: deny-all obejmuje WSZYSTKIE pody i oba kierunki ruchu."""
    zasoby, _ = _zbuduj()
    deny = [z for z in zasoby if z["kind"] == "NetworkPolicy" and "deny" in z["metadata"]["name"]]
    assert deny, "brak polityki deny-all"
    polityka = deny[0]["spec"]
    assert not (polityka.get("podSelector") or {}), "deny-all musi obejmować wszystkie pody"
    assert set(polityka.get("policyTypes") or []) >= {"Ingress", "Egress"}
