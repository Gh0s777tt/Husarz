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


def test_up_passes_resolved_config_dir_to_app(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """REGRESJA z uruchomienia realnej aplikacji: `husarz up` bez jawnego --config startował
    z `config_dir=None`, więc `POST /api/config/runtime` odpowiadał „Nadpisania wymagają
    katalogu konfiguracji" — panel konfiguracji w konsoli był martwy, mimo że konfiguracja
    wczytała się z tego samego (domyślnego) katalogu."""
    import argparse

    import uvicorn

    import husarz.api
    from husarz.launcher import cli

    captured: dict[str, object] = {}

    def fake_create_app(config, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return object()

    # `create_app` i `uvicorn` są importowane LENIWIE wewnątrz `_cmd_up` — podmieniamy je
    # w modułach źródłowych. Bez podmiany `uvicorn.run` test zawisłby na serwerze.
    monkeypatch.setattr(husarz.api, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "models.yaml").write_text(
        "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        config=None,
        profile=None,
        host="127.0.0.1",
        port=8000,
        prompts="./prompts",
        allow_insecure=False,
    )
    assert cli._cmd_up(args) == 0
    assert captured.get("config_dir") is not None, "config_dir MUSI być rozwiązany, nie None"
