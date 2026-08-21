"""Obraz `husarz-api` — niezmienniki hardeningu na realnym kontenerze (Etap 6).

Manifesty i Dockerfile deklarowały non-root, brak zapisu do rootfs i fail-closed przy
nasłuchu poza loopbackiem. Testy sprawdzały to wyłącznie przez PARSOWANIE plików wdrożeniowych
(`tests/security/test_deploy_invariants.py`) — czyli deklarację, nie skutek.

Te testy uruchamiają zbudowany obraz. Bez Dockera albo bez obrazu są pomijane z czytelnym
powodem; nigdy nie udają sukcesu.

Obraz buduje operator (nie test — build trwa minuty i wymaga sieci):

    docker build -t husarz-api:ci .
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

_OBRAZ = "husarz-api:ci"


def _docker() -> str | None:
    return shutil.which("docker")


def _obraz_gotowy() -> bool:
    docker = _docker()
    if docker is None:
        return False
    try:
        wynik = subprocess.run(  # noqa: S603 - pełna ścieżka z `which`, argumenty stałe
            [docker, "image", "inspect", _OBRAZ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return wynik.returncode == 0


wymaga_obrazu = pytest.mark.skipif(
    not _obraz_gotowy(), reason=f"wymaga obrazu {_OBRAZ} (docker build -t {_OBRAZ} .)"
)


def _uruchom(*args: str, entrypoint: str | None = None) -> subprocess.CompletedProcess[str]:
    """Uruchamia jednorazowy kontener i zwraca wynik (bez sieci, automatyczne sprzątanie)."""
    docker = _docker()
    assert docker is not None
    cmd = [docker, "run", "--rm", "--network", "none"]
    if entrypoint is not None:
        cmd += ["--entrypoint", entrypoint]
    cmd += [_OBRAZ, *args]
    return subprocess.run(  # noqa: S603 - pełna ścieżka z `which`, argumenty kontrolowane
        cmd, capture_output=True, text=True, timeout=180, check=False
    )


@wymaga_obrazu
def test_image_runs_as_non_root() -> None:
    """Deklarowane w Dockerfile — tu sprawdzamy, że silnik faktycznie tak uruchamia proces."""
    wynik = _uruchom("-c", "id", entrypoint="sh")
    assert wynik.returncode == 0, wynik.stderr
    assert "uid=1000" in wynik.stdout, wynik.stdout
    assert "uid=0(root)" not in wynik.stdout


@wymaga_obrazu
def test_image_rootfs_is_not_writable_by_app_user() -> None:
    wynik = _uruchom("-c", "touch /probny", entrypoint="sh")
    assert wynik.returncode != 0
    assert "denied" in (wynik.stderr + wynik.stdout).lower()


@wymaga_obrazu
def test_config_validates_inside_container() -> None:
    """Obraz niesie działającą konfigurację — inaczej `up` padłby dopiero u użytkownika."""
    wynik = _uruchom("validate")
    assert wynik.returncode == 0, wynik.stderr
    assert "wczytana poprawnie" in wynik.stdout


@wymaga_obrazu
def test_binding_beyond_loopback_without_auth_is_refused() -> None:
    """NAJWAŻNIEJSZY niezmiennik: fail-closed launchera działa też w kontenerze.

    Obraz domyślnie nasłuchuje na 0.0.0.0 (tak działa konteneryzacja), więc bez
    uwierzytelniania MUSI odmówić startu — inaczej `docker run` wystawiłby nieuwierzytelnione
    API na sieć kontenera przy pierwszym uruchomieniu.
    """
    # noqa S104: to argument testu SPRAWDZAJĄCEGO odmowę takiego nasłuchu — sedno przypadku.
    wynik = _uruchom("up", "--host", "0.0.0.0", "--port", "8000")  # noqa: S104
    assert wynik.returncode != 0
    assert "wymaga uwierzytelniania" in (wynik.stdout + wynik.stderr)
