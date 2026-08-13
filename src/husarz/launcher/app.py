"""Punkt wejścia launchera desktopowego (``husarz-app``).

W odróżnieniu od ``husarz`` (wymaga podkomendy) ten launcher jest przyjazny
„podwójnemu kliknięciu": bez argumentów startuje serwer na loopbacku i OTWIERA
konsolę w przeglądarce. To cel pakowania PyInstaller (jeden plik do pobrania).

Gdy uruchomiony jako zamrożony plik (PyInstaller), domyślne katalogi ``config``/
``prompts`` wskazują na zasoby dołączone do binarki (``sys._MEIPASS``), więc launcher
działa out-of-the-box; operator może je nadpisać flagami ``--config``/``--prompts``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from husarz import __version__
from husarz.config.schema import Profile


def _bundled_dir(name: str) -> str | None:
    """Ścieżka do katalogu dołączonego do binarki PyInstaller (lub None, gdy nie zamrożone)."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    candidate = Path(base) / name
    return str(candidate) if candidate.is_dir() else None


def build_parser() -> argparse.ArgumentParser:
    """Parser launchera desktopowego (bez wymaganej podkomendy — przyjazny double-click)."""
    parser = argparse.ArgumentParser(
        prog="husarz-app",
        description="Husarz — launcher desktopowy (serwer + konsola w przeglądarce).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Adres nasłuchu (domyślnie loopback).")
    parser.add_argument("--port", default=8000, type=int, help="Port API/konsoli.")
    parser.add_argument("--profile", default=Profile.DEV.value, choices=[p.value for p in Profile])
    parser.add_argument("--config", default=None, help="Katalog konfiguracji.")
    parser.add_argument("--prompts", default=None, help="Katalog promptów agentów.")
    parser.add_argument("--no-open", action="store_true", help="Nie otwieraj przeglądarki.")
    parser.add_argument("--version", action="version", version=f"Husarz {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Uruchamia serwer + konsolę. Deleguje do ``husarz up`` (ta sama logika/bezpieczeństwo)."""
    args = build_parser().parse_args(argv)

    from husarz.launcher.cli import main as cli_main  # noqa: PLC0415

    config = args.config if args.config is not None else _bundled_dir("config")
    prompts = args.prompts if args.prompts is not None else (_bundled_dir("prompts") or "./prompts")

    up_argv: list[str] = [
        "up",
        "--profile",
        args.profile,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--prompts",
        prompts,
    ]
    if config is not None:
        up_argv += ["--config", config]
    if not args.no_open:
        up_argv.append("--open")
    return cli_main(up_argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
