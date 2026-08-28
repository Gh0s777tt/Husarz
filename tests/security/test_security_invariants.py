"""Testy bezpieczeństwa Etapu 0 — niezmienniki egzekwowane na przykładowej konfiguracji.

Te testy pilnują twardych wymagań bezpieczeństwa jeszcze zanim powstaną
komponenty runtime (sandbox, ROE-gate w Etapie 3-4). Chronią przed regresją
w domyślnej konfiguracji dostarczanej z repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config

pytestmark = pytest.mark.security


def test_default_egress_is_deny(repo_config_dir: Path) -> None:
    """Domyślna polityka egress to DENY-ALL (suwerenność danych)."""
    config = load_config(repo_config_dir)
    assert config.security.egress.default_policy.value == "deny"


def test_no_telemetry(repo_config_dir: Path) -> None:
    """Zero telemetrii w domyślnej konfiguracji."""
    config = load_config(repo_config_dir)
    assert config.platform.telemetry_enabled is False


def test_sandbox_has_no_network_by_default(repo_config_dir: Path) -> None:
    """Sandbox narzędzi domyślnie bez sieci."""
    config = load_config(repo_config_dir)
    assert config.security.sandbox.network is False


def test_kontener_dostaje_DOKLADNIE_JEDEN_montaz_i_jest_nim_workspace(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Konfinacja do katalogu roboczego sprawdzana SKUTKIEM, nie wartością z YAML-a.

    Poprzednia wersja tego testu asercjonowała `sandbox.workspace_only is True` — czyli
    odczytywała pole konfiguracji, którego NIC nie czytało. Zielony test przy martwym polu
    to najgorszy możliwy układ: utrwalał przekonanie, że ograniczenie jest sprawdzone.
    Pole usunięto; kontrola musi patrzeć na to, co naprawdę dostaje kontener.

    Asercja na LICZBIE montaży, nie na obecności jednego — inaczej przeszłaby także po
    dodaniu kolejnego bind-mounta hosta, czyli po realnym osłabieniu izolacji.
    """
    from husarz.tools.sandbox import SandboxSpec, build_docker_argv

    config = load_config(repo_config_dir)
    spec = SandboxSpec(
        image="obraz:testowy",
        workspace_host_path=tmp_path,
        command=["ls"],
        network=config.security.sandbox.network,
    )

    argv = build_docker_argv(spec)

    montaze = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert len(montaze) == 1, f"kontener dostaje {len(montaze)} montaży zamiast jednego: {montaze}"
    assert montaze[0].startswith(f"{tmp_path}:"), montaze[0]


def test_audit_log_enabled_and_immutable(repo_config_dir: Path) -> None:
    """Audit log włączony i oznaczony jako niemodyfikowalny."""
    config = load_config(repo_config_dir)
    assert config.security.audit.enabled is True
    assert config.security.audit.immutable is True


def test_encryption_at_rest_enabled(repo_config_dir: Path) -> None:
    """Szyfrowanie at-rest włączone."""
    config = load_config(repo_config_dir)
    assert config.security.encryption.at_rest is True


def test_puszkarz_requires_roe(repo_config_dir: Path) -> None:
    """Puszkarz ma twardo wymagane ROE (roe_required=True)."""
    config = load_config(repo_config_dir)
    assert config.agents["puszkarz"].roe_required is True


def test_example_roe_is_inactive(repo_config_dir: Path) -> None:
    """Przykładowe ROE jest nieaktywne (brak zgody/podpisu) — nie autoryzuje akcji."""
    config = load_config(repo_config_dir)
    roe = config.roe["example-zlecenie"]
    assert roe.is_active is False
    assert roe.dry_run_default is True


def test_web_tool_declares_egress_with_allowlist(repo_config_dir: Path) -> None:
    """Narzędzie web deklaruje egress, ale z jawną, niepustą allowlistą domen."""
    config = load_config(repo_config_dir)
    web = config.tools["web"]
    assert web.requires_egress is True
    assert web.allowlist  # niepusta
