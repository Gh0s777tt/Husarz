"""Realne wykonanie sandboxa i weryfikator `tests` — wymaga Dockera (Etap 6 / Etap 16).

Do tej pory izolacja sandboxa była sprawdzana WYŁĄCZNIE po `argv` (`build_docker_argv`).
To dobre testy jednostkowe, ale nie odpowiadają na pytanie, czy Docker faktycznie egzekwuje
to, o co prosimy — flaga w wierszu poleceń a zachowanie silnika to dwie różne rzeczy.

Te testy uruchamiają PRAWDZIWY kontener i sprawdzają skutki. Bez Dockera albo bez obrazu
`husarz-sandbox` są pomijane — nigdy nie udają sukcesu.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.evals import SuiteCase
from husarz.config.schema import HusarzConfig
from husarz.eval import run_case
from husarz.tools.sandbox import DockerSandboxExecutor, spec_from_config

pytestmark = pytest.mark.integration


def _docker_gotowy() -> bool:
    """Czy Docker działa i obraz sandboxa istnieje."""
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        gotowy = subprocess.run(  # noqa: S603 - pełna ścieżka z `which`, argumenty stałe
            [docker, "image", "inspect", "husarz-sandbox:latest"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return gotowy.returncode == 0


wymaga_dockera = pytest.mark.skipif(
    not _docker_gotowy(),
    reason="wymaga Dockera i obrazu husarz-sandbox:latest "
    "(docker build -f docker/husarz-sandbox.Dockerfile -t husarz-sandbox:latest .)",
)


def _config_bez_gvisor(repo_config_dir: Path) -> HusarzConfig:
    """Konfiguracja z wyłączonym gVisor — Docker Desktop nie ma `runsc`.

    Świadomie zmieniamy TYLKO `runtime_class`: wszystkie pozostałe niezmienniki (brak sieci,
    non-root, read-only rootfs, limity) zostają, bo to właśnie one są przedmiotem testu.
    """
    config = load_config(repo_config_dir)
    sandbox = config.security.sandbox.model_copy(update={"runtime_class": None})
    return config.model_copy(
        update={"security": config.security.model_copy(update={"sandbox": sandbox})}
    )


# --- Niezmienniki egzekwowane przez SILNIK, nie przez argv ------------------


@wymaga_dockera
def test_sandbox_runs_and_is_non_root(repo_config_dir: Path, tmp_path: Path) -> None:
    config = _config_bez_gvisor(repo_config_dir)
    spec = spec_from_config(["id"], config.security.sandbox, workspace_host_path=str(tmp_path))
    result = DockerSandboxExecutor().run(spec)
    assert result.exit_code == 0
    assert "uid=1000" in result.stdout, result.stdout


@wymaga_dockera
def test_sandbox_has_no_network_in_practice(repo_config_dir: Path, tmp_path: Path) -> None:
    """NAJWAŻNIEJSZY niezmiennik: `--network none` egzekwowane przez silnik, nie deklarowane.

    Adres 1.1.1.1 jest celowo literałem IP — gdyby test używał nazwy, mierzyłby DNS, a nie
    odcięcie sieci.
    """
    config = _config_bez_gvisor(repo_config_dir)
    skrypt = "import urllib.request; urllib.request.urlopen('https://1.1.1.1', timeout=4)"
    spec = spec_from_config(
        ["python", "-c", skrypt], config.security.sandbox, workspace_host_path=str(tmp_path)
    )
    result = DockerSandboxExecutor().run(spec)
    assert result.exit_code != 0, "sandbox NIE MOŻE mieć dostępu do sieci"


@wymaga_dockera
def test_sandbox_rootfs_is_read_only(repo_config_dir: Path, tmp_path: Path) -> None:
    config = _config_bez_gvisor(repo_config_dir)
    spec = spec_from_config(
        ["sh", "-c", "touch /probny"], config.security.sandbox, workspace_host_path=str(tmp_path)
    )
    result = DockerSandboxExecutor().run(spec)
    assert result.exit_code != 0
    assert "read-only" in (result.stderr + result.stdout).lower()


@wymaga_dockera
def test_tmp_stays_writable_despite_read_only_rootfs(repo_config_dir: Path, tmp_path: Path) -> None:
    """Read-only rootfs nie może uniemożliwić pracy — `/tmp` jest tmpfs-em w kontenerze."""
    config = _config_bez_gvisor(repo_config_dir)
    spec = spec_from_config(
        ["sh", "-c", "touch /tmp/ok && echo dziala"],
        config.security.sandbox,
        workspace_host_path=str(tmp_path),
    )
    assert DockerSandboxExecutor().run(spec).exit_code == 0


@wymaga_dockera
def test_workspace_is_mounted(repo_config_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "plik.txt").write_text("zawartosc", encoding="utf-8")
    config = _config_bez_gvisor(repo_config_dir)
    spec = spec_from_config(
        ["cat", "/workspace/plik.txt"],
        config.security.sandbox,
        workspace_host_path=str(tmp_path),
    )
    result = DockerSandboxExecutor().run(spec)
    assert result.exit_code == 0 and "zawartosc" in result.stdout


# --- Weryfikator `tests` (Etap 16) -----------------------------------------


@wymaga_dockera
def test_exit_code_verifier_passes_on_green_suite(repo_config_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    case = SuiteCase(name="zielony", kind="tests", workspace=str(tmp_path), expect_exit_code=0)
    assert run_case(_config_bez_gvisor(repo_config_dir), case, agents={}, workspace=tmp_path).passed


@wymaga_dockera
def test_exit_code_verifier_detects_red_suite(repo_config_dir: Path, tmp_path: Path) -> None:
    """Test nośności: zielony zestaw NIE MOŻE spełnić oczekiwania kodu 1."""
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    case = SuiteCase(name="zly", kind="tests", workspace=str(tmp_path), expect_exit_code=1)
    result = run_case(_config_bez_gvisor(repo_config_dir), case, agents={}, workspace=tmp_path)
    assert result.passed is False and "otrzymano 0" in result.detail


@wymaga_dockera
def test_failing_suite_reports_exit_code_one(repo_config_dir: Path, tmp_path: Path) -> None:
    (tmp_path / "test_zly.py").write_text("def test_zly():\n    assert False\n", encoding="utf-8")
    case = SuiteCase(name="czerwony", kind="tests", workspace=str(tmp_path), expect_exit_code=1)
    assert run_case(_config_bez_gvisor(repo_config_dir), case, agents={}, workspace=tmp_path).passed


def test_missing_workspace_fails_without_docker(repo_config_dir: Path, tmp_path: Path) -> None:
    """Brak katalogu wykrywamy PRZED sandboxem — ten przypadek działa też bez Dockera."""
    case = SuiteCase(name="brak", kind="tests", workspace=str(tmp_path / "nie-ma"))
    result = run_case(_config_bez_gvisor(repo_config_dir), case, agents={}, workspace=tmp_path)
    assert result.passed is False and "brak katalogu" in result.detail
