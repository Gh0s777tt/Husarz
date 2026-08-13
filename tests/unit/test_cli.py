"""Testy launchera CLI (podkomendy validate/version)."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.launcher.cli import main

pytestmark = pytest.mark.unit


def test_validate_ok_on_repo_config(repo_config_dir: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["validate", "--config", str(repo_config_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "wczytana poprawnie" in out
    assert "puszkarz" in out


def test_validate_fails_on_broken_config(write_config, capsys: pytest.CaptureFixture) -> None:
    config_dir = write_config(
        {"models.yaml": "default: ghost\nregistry:\n  m1:\n    backend: mock\n    model: x\n"}
    )
    rc = main(["validate", "--config", str(config_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "niepoprawna" in err.lower() or "ghost" in err


def test_version(capsys: pytest.CaptureFixture) -> None:
    rc = main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Husarz" in out
