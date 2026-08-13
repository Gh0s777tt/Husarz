"""Testy launchera desktopowego: otwieranie przeglądarki, flaga --open, delegacja husarz-app."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from husarz.launcher import app as launcher_app
from husarz.launcher import cli

pytestmark = pytest.mark.unit


def test_open_browser_async_calls_opener() -> None:
    called: list[str] = []
    cli._open_browser_async("http://127.0.0.1:8000/", opener=called.append, delay=0.0)
    for _ in range(100):  # wątek w tle — krótkie odpytywanie
        if called:
            break
        time.sleep(0.01)
    assert called == ["http://127.0.0.1:8000/"]


def test_up_with_open_schedules_browser(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser_async", lambda url, **kw: opened.append(url))
    prompts = repo_config_dir.parent / "prompts"
    args = cli.build_parser().parse_args(
        [
            "up",
            "--open",
            "--config",
            str(repo_config_dir),
            "--host",
            "127.0.0.1",
            "--port",
            "8123",
            "--prompts",
            str(prompts),
        ]
    )
    assert cli._cmd_up(args) == 0
    assert opened == ["http://127.0.0.1:8123/"]


def test_up_without_open_does_not_schedule(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser_async", lambda url, **kw: opened.append(url))
    prompts = repo_config_dir.parent / "prompts"
    args = cli.build_parser().parse_args(
        ["up", "--config", str(repo_config_dir), "--host", "127.0.0.1", "--prompts", str(prompts)]
    )
    cli._cmd_up(args)
    assert opened == []  # bez --open brak otwarcia


def test_app_main_delegates_to_up_open(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    rc = launcher_app.main(["--host", "127.0.0.1", "--port", "9000", "--config", "./config"])
    assert rc == 0
    argv = captured[0]
    assert argv[0] == "up"
    assert "--open" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "9000"


def test_app_main_no_open_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    launcher_app.main(["--no-open", "--config", "./config"])
    assert "--open" not in captured[0]


def test_app_parser_defaults() -> None:
    args = launcher_app.build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.no_open is False
