"""Launcher CLI Husarza.

Podkomendy:
    husarz validate --config ./config   — wczytaj i zwaliduj konfigurację,
    husarz version                      — wypisz wersję,
    husarz up --profile dev             — uruchom API (FastAPI) + konsolę WWW.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Any

from husarz import __version__
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError
from husarz.config.schema import Profile


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


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK_HOSTS


def _resolve_api_token(config: HusarzConfig) -> str | None:
    """Rozwiązuje token API z referencji do sekretu (``security.auth.api_token_ref``).

    Zwraca ``None``, gdy referencji brak (uwierzytelnianie wyłączone). Gdy referencja
    jest ustawiona, ale nie da się jej rozwiązać, zgłasza ``ConfigError`` (fail-closed —
    nie startujemy z „skonfigurowanym, lecz nieaktywnym" tokenem).
    """
    ref = config.security.auth.api_token_ref
    if not ref:
        return None
    if ref.startswith(("vault:", "sops:")):
        raise ConfigError(
            f"api_token_ref='{ref}': dostawcy vault/sops wymagają jawnej konstrukcji — "
            "użyj referencji 'env:NAZWA' lub 'file:nazwa' dla launchera."
        )
    # Import leniwy — 'validate'/'version' nie potrzebują dostawców sekretów.
    from husarz.config.secrets import (  # noqa: PLC0415
        EnvSecretsProvider,
        FileSecretsProvider,
    )

    provider = FileSecretsProvider("./secrets") if ref.startswith("file:") else EnvSecretsProvider()
    token = provider.resolve(ref)
    if not token or not token.strip():
        raise ConfigError(f"Nie udało się rozwiązać tokenu API (api_token_ref='{ref}').")
    return token.strip()


def _cmd_up(args: argparse.Namespace) -> int:
    # Ładujemy konfigurację, wymuszając wybrany profil jako nadpisanie runtime.
    try:
        config = load_config(args.config, runtime_overrides={"platform": {"profile": args.profile}})
        api_token = _resolve_api_token(config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Fail-closed: nasłuch poza loopbackiem BEZ tokenu = otwarte control-plane API.
    if not _is_loopback(args.host) and api_token is None:
        if not args.allow_insecure:
            print(
                f"Odmowa: nasłuch na '{args.host}' (poza loopbackiem) wymaga tokenu API. "
                "Ustaw security.auth.api_token_ref na referencję sekretu (env:/file:), "
                "nasłuchuj na 127.0.0.1, albo (świadomie) użyj --allow-insecure.",
                file=sys.stderr,
            )
            return 2
        # Świadoma zgoda operatora (np. kontener za publikacją tylko na loopback hosta).
        print(
            f"OSTRZEŻENIE: nasłuch na '{args.host}' BEZ tokenu API (--allow-insecure). "
            "Zabezpiecz dostęp na warstwie sieci (publikacja portu tylko na loopback, "
            "NetworkPolicy).",
            file=sys.stderr,
        )

    # Importy leniwe — 'validate'/'version' nie wymagają FastAPI/uvicorn.
    import uvicorn  # noqa: PLC0415

    from husarz.api import create_app  # noqa: PLC0415
    from husarz.orchestrator import build_orchestrator  # noqa: PLC0415
    from husarz.router import ModelRouter  # noqa: PLC0415

    prompts = args.prompts

    def _orchestrator_factory(cfg: HusarzConfig) -> Any:
        # Router budowany z aktualnej konfiguracji — po nadpisaniu runtime orkiestrator
        # jest przebudowywany, więc /api/orchestrate używa NOWYCH ustawień (nie starych).
        return build_orchestrator(cfg, ModelRouter(cfg), prompts_dir=prompts)

    # TrustedHost tylko dla loopbacku (obrona przed DNS-rebindingiem na localhost).
    trusted = ["localhost", "127.0.0.1"] if _is_loopback(args.host) else None

    app = create_app(
        config,
        config_dir=args.config,
        api_token=api_token,
        orchestrator_factory=_orchestrator_factory,
        trusted_hosts=trusted,
        prompts_dir=prompts,
    )
    auth_note = "token API: wymagany" if api_token else "token API: brak (loopback)"
    print(
        f"Husarz API — profil {config.platform.profile.value} — "
        f"http://{args.host}:{args.port} (konsola: /) — {auth_note}"
    )
    uvicorn.run(app, host=args.host, port=args.port)  # pragma: no cover - serwer blokujący
    return 0


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

    p_up = sub.add_parser("up", help="Uruchom API + konsolę WWW w danym profilu.")
    p_up.add_argument("--config", default=None, help="Katalog konfiguracji.")
    # Źródło prawdy o profilach to enum Profile — bez duplikowania listy w CLI.
    p_up.add_argument("--profile", default=Profile.DEV.value, choices=[p.value for p in Profile])
    p_up.add_argument("--host", default="127.0.0.1", help="Adres nasłuchu (domyślnie loopback).")
    p_up.add_argument("--port", default=8000, type=int, help="Port API.")
    p_up.add_argument("--prompts", default="./prompts", help="Katalog promptów agentów.")
    p_up.add_argument(
        "--allow-insecure",
        action="store_true",
        help="Zezwól na nasłuch poza loopbackiem BEZ tokenu API (świadomie; zabezpiecz "
        "dostęp na warstwie sieci). Domyślnie taki nasłuch jest odrzucany.",
    )
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
