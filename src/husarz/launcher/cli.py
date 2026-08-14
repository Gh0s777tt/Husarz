"""Launcher CLI Husarza.

Podkomendy:
    husarz validate --config ./config   — wczytaj i zwaliduj konfigurację,
    husarz version                      — wypisz wersję,
    husarz up --profile dev             — uruchom API (FastAPI) + konsolę WWW.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
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


def _url_host(host: str) -> str:
    """Host do URL — literał IPv6 (np. ``::1``) wymaga nawiasów (RFC 3986)."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _open_browser_async(
    url: str, *, opener: Callable[[str], Any] | None = None, delay: float = 1.5
) -> None:
    """Otwiera przeglądarkę na konsoli po krótkiej zwłoce, w wątku w tle (UX launchera).

    Zwłoka daje serwerowi czas na nasłuch. Błąd otwarcia przeglądarki NIE może
    wywrócić serwera (headless/brak DISPLAY) — jest cicho ignorowany.
    """
    import contextlib  # noqa: PLC0415
    import threading  # noqa: PLC0415
    import time  # noqa: PLC0415
    import webbrowser  # noqa: PLC0415

    open_fn = opener if opener is not None else webbrowser.open

    def _run() -> None:
        time.sleep(delay)
        # Otwarcie przeglądarki jest best-effort (headless/brak DISPLAY) — nie wywraca serwera.
        with contextlib.suppress(Exception):
            open_fn(url)

    threading.Thread(target=_run, daemon=True).start()


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


def _accounts_enabled(config: HusarzConfig) -> bool:
    """Czy konta są aktywne (skonfigurowano magazyn, kompletny seed lub rejestrację)."""
    auth = config.security.auth
    seed = bool(auth.seed_admin_username and auth.seed_admin_password_ref)
    return bool(auth.accounts_path or seed or auth.allow_registration)


def _build_accounts(config: HusarzConfig) -> Any:
    """Buduje usługę kont z configu (lub None). Importy leniwe. Może rzucić ConfigError."""
    if not _accounts_enabled(config):
        return None
    from husarz.accounts.builder import build_account_service  # noqa: PLC0415
    from husarz.accounts.errors import AccountError  # noqa: PLC0415

    try:
        return build_account_service(config.security.auth, secrets=_SchemeSecrets())
    except AccountError as exc:
        raise ConfigError(str(exc)) from exc


def _build_git(config: HusarzConfig) -> Any:
    """Buduje usługę integracji Git z configu (lub None, gdy wyłączona). Import leniwy."""
    if not config.git.enabled:
        return None
    from husarz.git import build_git_service  # noqa: PLC0415

    return build_git_service(config.git, config.security, secrets=_SchemeSecrets())


def _build_plugins(config: HusarzConfig) -> Any:
    """Buduje usługę wtyczek z configu (lub None, gdy brak włączonych). Import leniwy."""
    from husarz.plugins import HttpxPluginTransport, build_plugin_service  # noqa: PLC0415

    return build_plugin_service(
        config.plugins,
        config.security,
        secrets=_SchemeSecrets(),
        transport=HttpxPluginTransport(),
    )


class _SchemeSecrets:
    """Dostawca sekretów rozwiązujący referencje po schemacie (env:/file:)."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca wartość sekretu dla referencji ``env:``/``file:`` albo ``None``."""
        from husarz.config.secrets import (  # noqa: PLC0415
            EnvSecretsProvider,
            FileSecretsProvider,
        )

        if ref.startswith("file:"):
            return FileSecretsProvider("./secrets").resolve(ref)
        return EnvSecretsProvider().resolve(ref)


def _cmd_up(args: argparse.Namespace) -> int:
    # Ładujemy konfigurację, wymuszając wybrany profil jako nadpisanie runtime.
    try:
        config = load_config(args.config, runtime_overrides={"platform": {"profile": args.profile}})
        api_token = _resolve_api_token(config)
        accounts = _build_accounts(config)
        git_service = _build_git(config)
        plugin_service = _build_plugins(config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # Uwierzytelnianie jest włączone, gdy jest token maszynowy ALBO usługa kont (sesje).
    auth_enabled = api_token is not None or accounts is not None

    # Fail-closed: nasłuch poza loopbackiem BEZ jakiegokolwiek uwierzytelniania = otwarte API.
    if not _is_loopback(args.host) and not auth_enabled:
        if not args.allow_insecure:
            print(
                f"Odmowa: nasłuch na '{args.host}' (poza loopbackiem) wymaga uwierzytelniania. "
                "Ustaw security.auth.api_token_ref (token maszynowy) lub włącz konta "
                "(accounts_path/seed_admin/allow_registration), nasłuchuj na 127.0.0.1, "
                "albo (świadomie) użyj --allow-insecure.",
                file=sys.stderr,
            )
            return 2
        print(
            f"OSTRZEŻENIE: nasłuch na '{args.host}' BEZ uwierzytelniania (--allow-insecure). "
            "Zabezpiecz dostęp na warstwie sieci (publikacja portu tylko na loopback, "
            "NetworkPolicy).",
            file=sys.stderr,
        )

    # Importy leniwe — 'validate'/'version' nie wymagają FastAPI/uvicorn.
    import uvicorn  # noqa: PLC0415

    from husarz.api import create_app  # noqa: PLC0415
    from husarz.router import ModelRouter  # noqa: PLC0415

    prompts = args.prompts

    def _router_factory(cfg: HusarzConfig) -> Any:
        # Router budowany z aktualnej konfiguracji — po nadpisaniu runtime router
        # i orkiestrator są przebudowywane (create_app), więc /api/orchestrate i
        # /api/chat używają NOWYCH ustawień (nie starych).
        return ModelRouter(cfg)

    # TrustedHost tylko dla loopbacku (obrona przed DNS-rebindingiem na localhost).
    trusted = ["localhost", "127.0.0.1"] if _is_loopback(args.host) else None

    app = create_app(
        config,
        config_dir=args.config,
        api_token=api_token,
        accounts=accounts,
        git_service=git_service,
        plugin_service=plugin_service,
        # Fabryka: przy nadpisaniu runtime serwis wtyczek (polityka konektorów: allow_call/
        # call_allowlist/enabled/egress) jest przebudowywany z NOWEGO configu — jak router.
        plugin_service_factory=_build_plugins,
        router_factory=_router_factory,
        trusted_hosts=trusted,
        prompts_dir=prompts,
        secrets=_SchemeSecrets(),  # przewleczenie sekretów: trwała pamięć RAG (klucz at-rest)
    )
    if api_token and accounts is not None:
        auth_note = "auth: token + konta"
    elif accounts is not None:
        auth_note = "auth: konta (logowanie)"
    elif api_token:
        auth_note = "auth: token maszynowy"
    else:
        auth_note = "auth: brak (loopback)"
    url = f"http://{_url_host(args.host)}:{args.port}/"
    print(f"Husarz API — profil {config.platform.profile.value} — {url} (konsola) — {auth_note}")
    # Launcher: otwórz konsolę w przeglądarce (tylko loopback — sensowne lokalnie).
    if getattr(args, "open", False) and _is_loopback(args.host):
        _open_browser_async(url)
    uvicorn.run(app, host=args.host, port=args.port)  # pragma: no cover - serwer blokujący
    return 0


def _cmd_useradd(args: argparse.Namespace) -> int:
    """Tworzy konto użytkownika (admin) — dla modelu „dostęp dla wybranych".

    Hasło pobierane jest ze zmiennej środowiskowej (``--password-env``), nie z
    argumentu (brak w historii powłoki). Wymaga trwałego magazynu (``accounts_path``).
    """
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    auth = config.security.auth
    if auth.accounts_path is None:
        print(
            "useradd wymaga trwałego magazynu — ustaw security.auth.accounts_path.",
            file=sys.stderr,
        )
        return 1
    password = os.environ.get(args.password_env, "")
    if not password.strip():
        print(f"Brak hasła: ustaw zmienną środowiskową {args.password_env}.", file=sys.stderr)
        return 1

    from husarz.accounts.builder import build_account_service  # noqa: PLC0415
    from husarz.accounts.errors import AccountError  # noqa: PLC0415

    try:
        service = build_account_service(auth, secrets=_SchemeSecrets())
        account = service.create_account(
            args.username, password.strip(), role=args.role, token_quota=args.quota
        )
    except (AccountError, ConfigError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    quota = account.token_quota if account.token_quota is not None else "brak"
    print(f"Utworzono konto '{account.username}' (rola: {account.role}, limit tokenów: {quota}).")
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
    p_up.add_argument(
        "--open",
        action="store_true",
        help="Otwórz konsolę w przeglądarce po starcie (UX launchera; tylko loopback).",
    )
    p_up.set_defaults(func=_cmd_up)

    p_useradd = sub.add_parser("useradd", help="Utwórz konto użytkownika (admin; hasło z ENV).")
    p_useradd.add_argument("--config", default=None, help="Katalog konfiguracji.")
    p_useradd.add_argument("--username", required=True, help="Nazwa użytkownika.")
    p_useradd.add_argument(
        "--role", default=None, help="Rola RBAC (domyślnie security.auth.default_user_role)."
    )
    p_useradd.add_argument(
        "--quota", type=int, default=None, help="Limit tokenów (domyślnie: z configu/bez limitu)."
    )
    p_useradd.add_argument(
        "--password-env",
        default="HUSARZ_NEW_USER_PASSWORD",
        help="Nazwa zmiennej środowiskowej z hasłem nowego konta.",
    )
    p_useradd.set_defaults(func=_cmd_useradd)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Punkt wejścia CLI. Zwraca kod wyjścia procesu."""
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
