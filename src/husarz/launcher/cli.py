"""Launcher CLI Husarza.

Etap 0 dostarcza podkomendę ``validate``, która wczytuje i waliduje konfigurację
oraz wypisuje zwięzłe podsumowanie. Podkomenda ``up`` (start profili dev/prod/airgap)
zostanie w pełni zaimplementowana w Etapie 5.

Uruchomienie po instalacji pakietu:
    husarz validate --config ./config
    husarz version
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from husarz import __version__
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError


def _summarize(config: HusarzConfig) -> str:
    """Buduje zwięzłe, czytelne podsumowanie wczytanej konfiguracji."""
    lines = [
        "Konfiguracja Husarza wczytana poprawnie.",
        f"  profil:            {config.platform.profile.value}",
        f"  log_level:         {config.platform.log_level.value}",
        f"  model domyślny:    {config.models.default}",
        f"  modele (rejestr):  {', '.join(sorted(config.models.registry)) or '—'}",
        f"  agenci:            {', '.join(sorted(config.agents)) or '—'}",
        f"  narzędzia:         {', '.join(sorted(config.tools)) or '—'}",
        f"  ROE (zlecenia):    {', '.join(sorted(config.roe)) or '—'}",
        f"  egress:            {config.security.egress.default_policy.value}",
        f"  sandbox:           {config.security.sandbox.engine.value} "
        f"(sieć: {'tak' if config.security.sandbox.network else 'nie'})",
    ]
    return "\n".join(lines)


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(_summarize(config))
    return 0


def _cmd_version(_: argparse.Namespace) -> int:
    print(f"Husarz {__version__}")
    return 0


def _cmd_up(args: argparse.Namespace) -> int:
    # Placeholder — pełny start usług (docker-compose/k8s) w Etapie 5.
    print(
        f"[Etap 5] Uruchamianie profilu '{args.profile}' nie jest jeszcze "
        f"zaimplementowane. Na razie użyj 'husarz validate'.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentów CLI."""
    parser = argparse.ArgumentParser(
        prog="husarz",
        description="Husarz — launcher suwerennej platformy wieloagentowej.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="Wczytaj i zwaliduj konfigurację.")
    p_validate.add_argument(
        "--config",
        default=None,
        help="Katalog konfiguracji (domyślnie ENV HUSARZ_CONFIG_DIR lub ./config).",
    )
    p_validate.set_defaults(func=_cmd_validate)

    p_version = sub.add_parser("version", help="Wypisz wersję.")
    p_version.set_defaults(func=_cmd_version)

    p_up = sub.add_parser("up", help="[Etap 5] Uruchom platformę w danym profilu.")
    p_up.add_argument("--profile", default="dev", choices=["dev", "prod", "airgap"])
    p_up.set_defaults(func=_cmd_up)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia CLI. Zwraca kod wyjścia procesu."""
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
