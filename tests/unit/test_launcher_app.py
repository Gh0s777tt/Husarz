"""Testy launchera desktopowego: otwieranie przeglądarki, flaga --open, delegacja husarz-app."""

from __future__ import annotations

import sys
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


def test_up_open_non_loopback_does_not_open(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Obietnica „tylko loopback": --open na 0.0.0.0 NIE otwiera przeglądarki.
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    opened: list[str] = []
    monkeypatch.setattr(cli, "_open_browser_async", lambda url, **kw: opened.append(url))
    prompts = repo_config_dir.parent / "prompts"
    args = cli.build_parser().parse_args(
        [
            "up",
            "--open",
            "--host",
            "0.0.0.0",  # noqa: S104 - literał celowo testowany (brak otwarcia poza loopbackiem)
            "--allow-insecure",
            "--config",
            str(repo_config_dir),
            "--prompts",
            str(prompts),
        ]
    )
    assert cli._cmd_up(args) == 0
    assert opened == []  # brak otwarcia poza loopbackiem


def test_open_browser_async_suppresses_opener_error() -> None:
    # Błąd openera (headless) nie może propagować/wywrócić serwera.
    def boom(_url: str) -> None:
        raise RuntimeError("brak DISPLAY")

    cli._open_browser_async("http://127.0.0.1:8000/", opener=boom, delay=0.0)
    time.sleep(0.1)  # daemon wykonał się i zdławił wyjątek — brak crashu


def test_url_host_wraps_ipv6() -> None:
    assert cli._url_host("::1") == "[::1]"
    assert cli._url_host("127.0.0.1") == "127.0.0.1"
    assert cli._url_host("[::1]") == "[::1]"


def test_app_main_forwards_profile_and_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    launcher_app.main(["--profile", "prod", "--prompts", "./px", "--config", "./config"])
    argv = captured[0]
    assert argv[argv.index("--profile") + 1] == "prod"
    assert argv[argv.index("--prompts") + 1] == "./px"


def test_app_main_without_config_omits_config_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bez --config i bez frozen (_MEIPASS) → 'up' bez --config, prompts ./prompts.
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv: captured.append(list(argv)) or 0)
    launcher_app.main([])
    argv = captured[0]
    assert "--config" not in argv
    assert argv[argv.index("--prompts") + 1] == "./prompts"


def test_audit_error_returns_503(repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Niezapisywalny audyt (np. read-only CWD binarki) → czytelne 503, nie surowe 500.
    from fastapi.testclient import TestClient

    from husarz.api import create_app
    from husarz.config import load_config
    from husarz.router import ChatResponse
    from husarz.security import AuditLog
    from husarz.security.errors import AuditError

    class _OkRouter:
        def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
            return ChatResponse(model="x", content="ok")

    def _boom(self: AuditLog, *a: object, **k: object) -> None:
        raise AuditError("read-only")

    monkeypatch.setattr(AuditLog, "record", _boom)
    config = load_config(repo_config_dir)
    client = TestClient(create_app(config, audit=AuditLog(), router=_OkRouter()))
    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert resp.status_code == 503
