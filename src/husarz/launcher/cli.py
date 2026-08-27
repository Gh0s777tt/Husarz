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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from husarz import __version__
from husarz.config import HusarzConfig, load_config
from husarz.config.errors import ConfigError
from husarz.config.loader import resolve_config_dir
from husarz.config.schema import Profile
from husarz.launcher.diagnostics import format_port_conflicts, port_conflicts

if TYPE_CHECKING:  # import tylko dla adnotacji — start CLI nie wciąga warstwy security
    from husarz.security.secret_store import EncryptedFileSecretStore


def _roe_signature_status(config: HusarzConfig) -> str:
    """Opisuje EFEKTYWNY stan weryfikacji podpisu ROE (widoczny przy każdym `validate`).

    Bez tego wyłączona weryfikacja była niewidoczna poza lekturą YAML-a — a to degradacja
    jedynego prymitywu autoryzującego aktywne działania Puszkarza, więc operator ma ją
    widzieć od razu, wraz z informacją, czy w ogóle istnieje zlecenie ze zgodą.
    """
    roe = config.security.roe
    consented = sorted(name for name, entry in config.roe.items() if entry.consent)
    if not roe.verify_signature:
        suffix = f" — UWAGA: aktywne zlecenia: {', '.join(consented)}" if consented else ""
        return f"WYŁĄCZONA (weryfikacja pominięta){suffix}"
    if not roe.key_ref:
        return f"włączona ({roe.algorithm}), ale BRAK key_ref — weryfikacja odmówi"
    return f"włączona ({roe.algorithm}, klucz: {roe.key_ref})"


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
        f"  podpis ROE:        {_roe_signature_status(config)}",
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


def _cmd_eval(args: argparse.Namespace) -> int:
    """Uruchamia zestawy ewaluacyjne i wypisuje raport.

    Kod wyjścia 0 = wszystko zdane, 1 = choć jeden przypadek niezdany (albo błąd konfiguracji).
    Dzięki temu polecenie nadaje się wprost na bramkę w CI — nie woła modelu ani sieci.
    """
    import tempfile  # noqa: PLC0415 - potrzebne tylko tutaj

    from husarz.eval import run_set  # noqa: PLC0415 - import leniwy jak reszta CLI

    config_dir = resolve_config_dir(args.config, os.environ)
    try:
        config = load_config(config_dir)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    wybrane = {k: v for k, v in config.evals.items() if args.set is None or k == args.set}
    if args.set is not None and not wybrane:
        dostepne = ", ".join(sorted(config.evals)) or "(brak)"
        print(f"Nie ma zestawu '{args.set}'. Dostępne: {dostepne}", file=sys.stderr)
        return 1
    if not wybrane:
        # Zielona bramka bez ani jednego pomiaru to najgorszy możliwy sygnał: CI melduje
        # sukces, choć nic nie sprawdzono (brak podkatalogu evals/ w obrazie, zły wolumen).
        print(
            "Brak zestawów ewaluacyjnych w config/evals/ — nie ma czego mierzyć. "
            "Użyj --allow-empty, jeśli to zamierzone.",
            file=sys.stderr,
        )
        return 0 if args.allow_empty else 1

    prompts = Path(args.prompts)
    ok = True
    wykonane = 0
    # Narzędzia z przypadków `tool_policy` piszą do workspace'u — dajemy im katalog
    # tymczasowy, żeby ewaluacja nie dotykała roboczego katalogu operatora.
    with tempfile.TemporaryDirectory(prefix="husarz-eval-") as tmp:
        for name in sorted(wybrane):
            result = run_set(config, wybrane[name], prompts_dir=prompts, workspace=Path(tmp))
            wykonane += len(result.results)
            status = "OK" if result.ok else "BŁĄD"
            print(
                f"[{status}] zestaw '{result.name}': {result.passed} zdanych, {result.failed} nie"
            )
            for case in result.results:
                if case.passed:
                    print(f"    ✓ {case.name} ({case.kind})")
                else:
                    print(f"    ✗ {case.name} ({case.kind}) — {case.detail}")
            ok = ok and result.ok
    if wykonane == 0 and not args.allow_empty:
        print("Zestawy istnieją, ale nie zawierają ani jednego przypadku.", file=sys.stderr)
        return 1
    return 0 if ok else 1


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


def _build_git(config: HusarzConfig, store: Any = None) -> Any:
    """Buduje usługę integracji Git z configu (lub None, gdy wyłączona). Import leniwy.

    ``store`` (opcjonalny) to DOTYCHCZASOWY magazyn połączeń — przekazywany przy przebudowie
    po nadpisaniu konfiguracji w runtime, żeby świeża polityka egress nie kasowała połączeń
    dodanych przez API (patrz ``git_service_factory`` w ``create_app``).
    """
    if not config.git.enabled:
        return None
    from husarz.git import build_git_service  # noqa: PLC0415

    return build_git_service(
        config.git,
        config.security,
        # Bieżąca wartość, nie startowa: serwis jest przebudowywany po nadpisaniu runtime,
        # więc wyłączenie magazynu w panelu odcina też ODCZYT istniejących tokenów.
        secrets=_SchemeSecrets(magazyn_dostepny=config.security.secret_store.enabled),
        store=store,
    )


def _build_plugins(config: HusarzConfig) -> Any:
    """Buduje usługę wtyczek z configu (lub None, gdy brak włączonych). Import leniwy."""
    from husarz.plugins import HttpxPluginTransport, build_plugin_service  # noqa: PLC0415

    return build_plugin_service(
        config.plugins,
        config.security,
        secrets=_SchemeSecrets(magazyn_dostepny=config.security.secret_store.enabled),
        transport=HttpxPluginTransport(),
    )


# Jedyna instancja zapisywalnego magazynu sekretów w procesie. Zmienna modułowa jest tu
# świadomym wyborem, a nie skrótem: magazyn trzyma stan w pamięci (wczytane wpisy), więc
# DWIE instancje wskazujące ten sam plik rozjechałyby się przy pierwszym zapisie —
# instancja API zapisałaby wpis, którego instancja serwisu Gita by nie widziała, a kolejny
# zapis tej drugiej skasowałby go z pliku. `_SchemeSecrets` jest konstruowany bezargumentowo
# w kilku miejscach (serwis Gita, wtyczki, konta, pamięć) i częściowo w fabrykach wołanych
# później przez API, więc przewleczenie parametru oznaczałoby przekazywanie go przez pięć
# warstw. Ustawiane raz, w `_cmd_up` (korzeń kompozycji launchera).
_SEKRETY: EncryptedFileSecretStore | None = None


def _zbuduj_magazyn_sekretow(config: HusarzConfig) -> EncryptedFileSecretStore | None:
    """Buduje magazyn sekretów wg konfiguracji; ``None``, gdy wyłączony.

    Args:
        config: Wczytana konfiguracja.

    Returns:
        Magazyn albo ``None``, gdy ``security.secret_store.enabled`` jest wyłączone.

    Raises:
        ConfigError: Gdy magazyn jest włączony, ale klucza głównego nie da się rozwiązać.
            Fail-closed: wolimy nie wystartować, niż wystartować z kreatorem, który przy
            pierwszym użyciu odmówiłby zapisu tokenu.
    """
    ustawienia = config.security.secret_store
    if not ustawienia.enabled:
        return None
    from husarz.security.secret_store import (  # noqa: PLC0415
        SecretStoreError,
        build_secret_store,
    )

    sciezka = ustawienia.path or (config.platform.data_dir / "secrets" / "store.json")
    try:
        return build_secret_store(
            path=sciezka,
            key_ref=ustawienia.key_ref,
            # Domyślne `magazyn_dostepny=True` jest tu bez znaczenia: klucz główny ma
            # schemat ZEWNĘTRZNY (walidacja zabrania `husarz:`), więc bramka go nie dotyka.
            secrets=_SchemeSecrets(),
        )
    except SecretStoreError as exc:
        raise ConfigError(f"Magazyn sekretów: {exc}") from exc


class _SchemeSecrets:
    """Dostawca sekretów rozwiązujący referencje po schemacie (env:/file:/husarz:).

    ``magazyn_dostepny`` decyduje, czy referencje ``husarz:`` w ogóle są rozwiązywane.
    Domyślnie tak; wołający, który zna BIEŻĄCĄ konfigurację (fabryki serwisów przebudowywane
    po nadpisaniu runtime), przekazuje ``config.security.secret_store.enabled``.

    **Dlaczego wyłączenie zamyka także ODCZYT.** ``security.secret_store.enabled = false`` to
    kill-switch, a nie tylko zakaz zapisu. Operator wyłączający magazyn — zwykle w reakcji na
    incydent — oczekuje, że przestanie on wydawać materiał, a nie że zablokuje wyłącznie nowe
    wpisy, podczas gdy dotychczasowe tokeny nadal uwierzytelniają połączenia. Skutek jest
    GŁOŚNY: operacja Gita kończy się czytelnym „nie udało się rozwiązać tokenu", nie cichą
    degradacją. Ponowne włączenie działa natychmiast, bez restartu, więc pomyłka jest tania.

    Args:
        magazyn_dostepny: Czy referencje ``husarz:`` mają być rozwiązywane.
    """

    def __init__(self, *, magazyn_dostepny: bool = True) -> None:
        self._magazyn_dostepny = magazyn_dostepny

    def resolve(self, ref: str) -> str | None:
        """Zwraca wartość sekretu dla referencji ``env:``/``file:``/``husarz:`` albo ``None``."""
        from husarz.config.secrets import (  # noqa: PLC0415
            EnvSecretsProvider,
            FileSecretsProvider,
        )

        if ref.startswith("husarz:"):
            # Brak magazynu ALBO wyłączony w bieżącej konfiguracji — referencja jest
            # nierozwiązywalna i wołający dostaje None, dokładnie jak przy braku zmiennej
            # środowiskowej.
            if _SEKRETY is None or not self._magazyn_dostepny:
                return None
            return _SEKRETY.resolve(ref)
        if ref.startswith("file:"):
            return FileSecretsProvider("./secrets").resolve(ref)
        return EnvSecretsProvider().resolve(ref)


def _dodatnie_sekundy(wartosc: str) -> int:
    """Waliduje limit czasu podany w CLI: liczba całkowita >= 1.

    Bez tego `--probe-timeout 0` (albo wartość ujemna) trafiał do `model_copy`, które
    OMIJA walidację schematu (`ge=1`), i sonda przerywała każde żądanie natychmiast —
    diagnozując sprawny silnik jako awarię. Narzędzie pomiarowe musi odmówić pomiaru,
    którego nie da się wykonać, zamiast zmyślać wynik.

    Args:
        wartosc: Surowy argument z wiersza poleceń.

    Returns:
        Liczba sekund.

    Raises:
        argparse.ArgumentTypeError: Gdy wartość nie jest liczbą całkowitą >= 1.
    """
    try:
        sekundy = int(wartosc)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{wartosc}' nie jest liczbą całkowitą sekund.") from None
    if sekundy < 1:
        raise argparse.ArgumentTypeError(f"limit musi wynosić co najmniej 1 s (podano {sekundy}).")
    return sekundy


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnoza instalacji: co jest nie tak i co z tym zrobić.

    Kod wyjścia 1 przy problemie BLOKUJĄCYM — dzięki temu komenda nadaje się do skryptu
    startowego. Stan NIEZNANY nie jest błędem, ale też NIE jest sukcesem: kończy się kodem 0
    z jawnym komunikatem, że części kontroli nie dało się wykonać.

    Flaga ``--probe`` włącza sondę GŁĘBOKĄ: każdy model potwierdzony w katalogu dostaje
    prawdziwe żądanie uzupełnienia. To jedyna kontrola SKUTKU — reszta sprawdza deklarację
    („silnik wymienia ten model"). Jest opcjonalna, bo ma skutki uboczne: pierwsze żądanie
    wczytuje wagi do pamięci i potrafi trwać minuty.

    Args:
        args: Argumenty CLI (``--config``, ``--host``, ``--port``, ``--probe``,
            ``--probe-timeout``).

    Returns:
        Kod wyjścia procesu.
    """
    from husarz.launcher.doctor import (  # noqa: PLC0415
        SondaSystemowa,
        Stan,
        Waga,
        sformatuj,
        zdiagnozuj,
    )

    try:
        config_dir = resolve_config_dir(args.config, os.environ)
        config = load_config(config_dir)
    except ConfigError as exc:
        # Sama konfiguracja jest pierwszą kontrolą — bez niej nie ma czego diagnozować.
        print(f"[!!] konfiguracja: {exc}", file=sys.stderr, flush=True)
        print(
            "     → popraw pliki w katalogu konfiguracji i uruchom `husarz doctor` ponownie.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    # Dostawca sekretów jest potrzebny WYŁĄCZNIE sondzie głębokiej (rozwiązanie
    # `api_key_ref` modelu). Kontrola katalogu go nie używa.
    #
    # `magazyn_dostepny` przekazujemy JAWNIE, tak jak robi to `_build_git`. Domyślne `True`
    # obchodziłoby kill-switch `security.secret_store.enabled`: operator, który wyłączył
    # magazyn po incydencie, oczekuje, że przestanie on wydawać materiał — także diagnozie.
    if args.probe:
        global _SEKRETY  # noqa: PLW0603 - korzeń kompozycji, uzasadnienie przy deklaracji
        try:
            _SEKRETY = _zbuduj_magazyn_sekretow(config)
        except ConfigError as exc:
            # Magazyn niedostępny NIE MOŻE przerwać diagnozy — narzędzie ma diagnozować,
            # nie odmawiać. Mówimy o tym wprost; referencje `husarz:` zgłoszą się same
            # jako `brak-sekretu`.
            print(f"[??] magazyn sekretów niedostępny: {exc}", file=sys.stderr, flush=True)
    sonda = SondaSystemowa(
        config,
        timeout_zapytania=args.probe_timeout,
        secrets=_SchemeSecrets(magazyn_dostepny=config.security.secret_store.enabled),
        postep=lambda mid: print(f"  … pytam model '{mid}'", flush=True),
    )
    if args.probe:
        print(
            f"Sonda głęboka włączona — każdy model potwierdzony w katalogu dostanie realne "
            f"żądanie (limit CO NAJMNIEJ {args.probe_timeout} s; model z wyższym "
            f"`request_timeout_seconds` dostanie tyle, ile ma w konfiguracji). "
            f"Pierwsze żądanie wczytuje wagi i może potrwać.",
            flush=True,
        )
    ustalenia = zdiagnozuj(
        config,
        sonda=sonda,
        host=args.host,
        port=args.port,
        # Opt-in STRUKTURALNY: bez tego obiektu diagnoza nie ma czym zapytać modelu.
        sonda_gleboka=sonda if args.probe else None,
    )
    for linia in sformatuj(ustalenia):
        print(linia, flush=True)
    blokujace = [u for u in ustalenia if u.stan is Stan.PROBLEM and u.waga is Waga.BLOKUJACA]
    return 1 if blokujace else 0


def _cmd_up(args: argparse.Namespace) -> int:
    # Ładujemy konfigurację, wymuszając wybrany profil jako nadpisanie runtime.
    try:
        # Profil nadpisujemy TYLKO gdy operator podał go jawnie. Domyślne wstrzykiwanie
        # 'dev' po cichu degradowało konfigurację z `profile: prod` w pliku — a profil
        # kotwiczy całą bazową linię bezpieczeństwa (sandbox, audyt, szyfrowanie, podpis ROE).
        overrides = {"platform": {"profile": args.profile}} if args.profile else None
        # ROZWIĄZUJEMY katalog configu i przekazujemy go dalej. Bez tego `husarz up` bez
        # jawnego --config startował z `config_dir=None`, więc `POST /api/config/runtime`
        # odpowiadał „Nadpisania wymagają katalogu konfiguracji" — panel konfiguracji
        # w konsoli był martwy, mimo że konfiguracja wczytała się z tego samego katalogu.
        config_dir = resolve_config_dir(args.config, os.environ)
        config = load_config(config_dir, runtime_overrides=overrides)
        # Magazyn sekretów PRZED serwisami: to on rozwiązuje referencje `husarz:`,
        # którymi posługują się połączenia Git dodane przez kreator w konsoli.
        global _SEKRETY  # noqa: PLW0603 - korzeń kompozycji, uzasadnienie przy deklaracji
        _SEKRETY = _zbuduj_magazyn_sekretow(config)
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
        #
        # `secrets` przekazujemy JAWNIE. Bez tego `ModelRouter` podstawiał
        # `NullSecretsProvider`, więc KAŻDY model z `api_key_ref` (model za bramą API,
        # zdalny vLLM z tokenem) był w produkcji nieużywalny: `build_client` zgłaszał
        # „Nie udało się rozwiązać sekretu klucza API" i żądanie nie wychodziło. Wada
        # istniała, zanim powstała sonda głęboka — ujawniła ją dopiero ona, bo sonda
        # rozwiązywała klucz i meldowała OK dla drogi, której router nie potrafił przejść.
        # Dostarczona konfiguracja nie używa `api_key_ref`, więc nic tego nie wywoływało.
        return ModelRouter(
            cfg, secrets=_SchemeSecrets(magazyn_dostepny=cfg.security.secret_store.enabled)
        )

    # TrustedHost tylko dla loopbacku (obrona przed DNS-rebindingiem na localhost).
    trusted = ["localhost", "127.0.0.1"] if _is_loopback(args.host) else None

    app = create_app(
        config,
        config_dir=config_dir,
        api_token=api_token,
        accounts=accounts,
        git_service=git_service,
        # Fabryka: po `POST /api/config/runtime` serwis Git jest przebudowywany z NOWĄ
        # polityką egress, ale z tym samym magazynem połączeń (bez utraty danych).
        # Magazyn przychodzi OD API (serwis aktualny), a nie z domknięcia na `git_service`
        # z chwili startu — to domknięcie gubiło połączenia, gdy Git był przy starcie
        # wyłączony i włączono go dopiero nadpisaniem runtime.
        git_service_factory=lambda cfg, store: _build_git(cfg, store=store),
        plugin_service=plugin_service,
        # Fabryka: przy nadpisaniu runtime serwis wtyczek (polityka konektorów: allow_call/
        # call_allowlist/enabled/egress) jest przebudowywany z NOWEGO configu — jak router.
        plugin_service_factory=_build_plugins,
        router_factory=_router_factory,
        trusted_hosts=trusted,
        prompts_dir=prompts,
        secrets=_SchemeSecrets(),  # przewleczenie sekretów: trwała pamięć RAG (klucz at-rest)
        secret_store=_SEKRETY,  # kreator połączeń: zapis tokenu pod referencją `husarz:`
        # REALNY adres nasłuchu — `GET /api/doctor` wykrywa dzięki temu model celujący
        # w port zajęty przez samego Husarza. Bez przekazania tu wartości z `args` panel
        # sprawdzałby port domyślny i przy `--port 9000` przeoczyłby kolizję (albo zmyślił).
        listen_host=args.host,
        listen_port=args.port,
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
    # `flush=True` jest tu istotne: przy przekierowaniu wyjścia do pliku (usługa systemowa,
    # `nohup`, kontener) stdout jest buforowany blokowo, a uvicorn loguje na stderr — bez
    # wymuszenia bufor nie schodzi do dysku i CAŁY komunikat startowy wraz z ostrzeżeniami
    # znika z logów, mimo że w terminalu jest widoczny.
    print(
        f"Husarz API — profil {config.platform.profile.value} — {url} (konsola) — {auth_note}",
        flush=True,
    )
    # Kontrola startowa: czy któryś model nie celuje w port, który właśnie zajmujemy.
    # Oba domyślne porty to 8000 (launcher i vLLM w dostarczonym configu), więc bez
    # ostrzeżenia żądanie do takiego modelu wraca do własnego API. Ostrzegamy, nie blokujemy.
    for line in format_port_conflicts(
        port_conflicts(config, host=args.host, port=args.port), port=args.port
    ):
        print(line, flush=True)

    # Diagnoza przy starcie: bez niej operator, któremu brakuje silnika modelu, dostawał
    # w czacie gołe `502 Backend modelu zawiódł`, a w logu startowym NIC. Pokazujemy tylko
    # ustalenia wymagające uwagi — pełną listę daje `husarz doctor`.
    from husarz.launcher.doctor import (  # noqa: PLC0415
        SondaSystemowa,
        Stan,
        sformatuj,
        zdiagnozuj,
    )

    warte_uwagi = [
        u
        for u in zdiagnozuj(config, sonda=SondaSystemowa(config), host=args.host, port=args.port)
        if u.stan is not Stan.OK
    ]
    if warte_uwagi:
        print("\nDiagnoza startowa (pełna: `husarz doctor`):", flush=True)
        for linia in sformatuj(warte_uwagi):
            print(linia, flush=True)
        print("", flush=True)
    # Launcher: otwórz konsolę w przeglądarce (tylko loopback — sensowne lokalnie).
    if getattr(args, "open", False) and _is_loopback(args.host):
        _open_browser_async(url)
    uvicorn.run(app, host=args.host, port=args.port)  # pragma: no cover - serwer blokujący
    return 0


def _resolve_secret_ref(ref: str | None, label: str) -> str:
    """Rozwiązuje referencję sekretu do wartości (env:/file:). Fail-closed przy braku.

    Raises:
        ConfigError: brak referencji, nieobsługiwany schemat albo nierozwiązywalny sekret.
    """
    if not ref:
        raise ConfigError(f"Brak {label} w konfiguracji (referencja do klucza jest wymagana).")
    if ref.startswith(("vault:", "sops:")):
        raise ConfigError(
            f"{label}='{ref}': dostawcy vault/sops wymagają jawnej konstrukcji — "
            "użyj 'env:NAZWA' lub 'file:nazwa' dla CLI."
        )
    from husarz.config.secrets import (  # noqa: PLC0415
        EnvSecretsProvider,
        FileSecretsProvider,
    )

    provider = FileSecretsProvider("./secrets") if ref.startswith("file:") else EnvSecretsProvider()
    value = provider.resolve(ref)
    if not value or not value.strip():
        raise ConfigError(f"Nie udało się rozwiązać sekretu ({label}='{ref}').")
    return value.strip()


def _load_roe(args: argparse.Namespace) -> Any:
    """Wczytuje konfigurację i zwraca wskazane zlecenie ROE (albo podnosi ``ConfigError``)."""
    config = load_config(args.config)
    roe = config.roe.get(args.engagement)
    if roe is None:
        known = ", ".join(sorted(config.roe)) or "(brak zleceń w config/roe/)"
        raise ConfigError(f"Nieznane zlecenie ROE: '{args.engagement}'. Dostępne: {known}.")
    return config, roe


def _cmd_roe_sign(args: argparse.Namespace) -> int:
    """Podpisuje ROE i wypisuje wartość do wklejenia w pole ``signature``.

    Klucz PRYWATNY (ed25519) podaje operator plikiem — Husarz nigdy go nie przechowuje.
    Dla ``hmac-sha256`` używamy sekretu wskazanego przez ``security.roe.key_ref``.
    """
    from husarz.security import ALGORITHM_ED25519, RoeSignatureError  # noqa: PLC0415
    from husarz.security.roe_signature import sign_ed25519, sign_hmac  # noqa: PLC0415

    try:
        config, roe = _load_roe(args)
        algorithm = args.algorithm or config.security.roe.algorithm
        if algorithm != config.security.roe.algorithm:
            # Podpis innym algorytmem niż skonfigurowany zostanie odrzucony przez
            # downgrade-guard w runtime — lepiej powiedzieć to teraz niż dać operatorowi
            # „poprawny" podpis, który nigdy nie przejdzie weryfikacji.
            raise ConfigError(
                f"--algorithm={algorithm} nie zgadza się z security.roe.algorithm="
                f"{config.security.roe.algorithm}. Runtime odrzuci taki podpis "
                f"(downgrade-guard). Zmień config albo pomiń --algorithm."
            )
        if algorithm == ALGORITHM_ED25519:
            if not args.private_key_file:
                raise ConfigError(
                    "Podpis ed25519 wymaga --private-key-file (klucz prywatny PEM). "
                    "Husarz weryfikuje kluczem publicznym i nigdy nie przechowuje prywatnego."
                )
            pem = Path(args.private_key_file).read_text(encoding="utf-8")
            signature = sign_ed25519(roe, pem)
        else:
            key = _resolve_secret_ref(config.security.roe.key_ref, "security.roe.key_ref")
            signature = sign_hmac(roe, key)
    except (ConfigError, RoeSignatureError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Nie udało się odczytać klucza prywatnego: {exc}", file=sys.stderr)
        return 1
    print(f"# Wklej do config/roe/{args.engagement}.yaml (pole 'signature'):")
    print(f"signature: {signature}")
    if any(key.startswith("HUSARZ_ROE__") for key in os.environ):
        # Podpis obejmuje EFEKTYWNĄ treść zlecenia (plik + ENV), a nie sam plik. Jeśli
        # runtime wystartuje bez tych zmiennych, treść będzie inna i podpis nie przejdzie.
        print(
            "# UWAGA: w środowisku są nadpisania HUSARZ_ROE__* — podpis obejmuje treść "
            "EFEKTYWNĄ (plik + ENV).\n"
            "# Runtime musi widzieć te same nadpisania, inaczej weryfikacja odmówi.",
            file=sys.stderr,
        )
    return 0


def _cmd_roe_verify(args: argparse.Namespace) -> int:
    """Weryfikuje podpis wskazanego ROE i raportuje wynik (kod 0 = ważny)."""
    from husarz.security import RoeSignatureError  # noqa: PLC0415
    from husarz.security.roe_signature import verify  # noqa: PLC0415

    try:
        config, roe = _load_roe(args)
        key = _resolve_secret_ref(config.security.roe.key_ref, "security.roe.key_ref")
        ok = verify(roe, algorithm=config.security.roe.algorithm, key=key)
    except (ConfigError, RoeSignatureError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if ok:
        print(f"Podpis ROE '{args.engagement}' jest WAŻNY ({config.security.roe.algorithm}).")
        return 0
    print(
        f"Podpis ROE '{args.engagement}' NIE przeszedł weryfikacji — zlecenie pozostanie "
        f"nieaktywne. Sprawdź, czy treść nie zmieniła się po podpisaniu.",
        file=sys.stderr,
    )
    return 2


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

    p_eval = sub.add_parser(
        "eval", help="Uruchom zestawy ewaluacyjne (deterministyczne, bez modelu i sieci)."
    )
    p_eval.add_argument(
        "--config",
        default=None,
        help="Katalog konfiguracji (domyślnie ENV HUSARZ_CONFIG_DIR lub ./config).",
    )
    p_eval.add_argument("--prompts", default="./prompts", help="Katalog promptów agentów.")
    p_eval.add_argument("--set", default=None, help="Nazwa jednego zestawu (domyślnie: wszystkie).")
    p_eval.add_argument(
        "--allow-empty",
        action="store_true",
        help="Nie traktuj braku przypadków jako błędu (domyślnie: brak pomiarów = kod 1).",
    )
    p_eval.set_defaults(func=_cmd_eval)

    p_doctor = sub.add_parser(
        "doctor", help="Zdiagnozuj instalację: co jest nie tak i co z tym zrobić."
    )
    p_doctor.add_argument("--config", default=None, help="Katalog konfiguracji.")
    p_doctor.add_argument(
        "--host", default="127.0.0.1", help="Adres nasłuchu (do wykrycia kolizji portu)."
    )
    p_doctor.add_argument("--port", type=int, default=8000, help="Port jw.")
    p_doctor.add_argument(
        "--probe",
        action="store_true",
        help=(
            "Zadaj modelom PRAWDZIWE pytanie zamiast sprawdzać sam katalog. Kontrola skutku, "
            "nie deklaracji — ale wczytuje wagi do pamięci i potrafi trwać minuty."
        ),
    )
    p_doctor.add_argument(
        "--probe-timeout",
        type=_dodatnie_sekundy,
        default=60,
        help=(
            "Sekundy na odpowiedź modelu w sondzie głębokiej (domyślnie 60). Pierwsze żądanie "
            "wczytuje wagi, więc bywa o rząd wielkości wolniejsze od kolejnych."
        ),
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_version = sub.add_parser("version", help="Wypisz wersję.")
    p_version.set_defaults(func=_cmd_version)

    p_up = sub.add_parser("up", help="Uruchom API + konsolę WWW w danym profilu.")
    p_up.add_argument("--config", default=None, help="Katalog konfiguracji.")
    # Źródło prawdy o profilach to enum Profile — bez duplikowania listy w CLI.
    p_up.add_argument(
        "--profile",
        default=None,
        choices=[p.value for p in Profile],
        help="Nadpisz profil z konfiguracji (bez tej flagi obowiązuje profil z pliku/ENV).",
    )
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

    p_roe = sub.add_parser("roe", help="Operacje na zleceniach ROE (podpis, weryfikacja).")
    roe_sub = p_roe.add_subparsers(dest="roe_command", required=True)

    p_roe_sign = roe_sub.add_parser("sign", help="Podpisz ROE i wypisz wartość 'signature'.")
    p_roe_sign.add_argument("--config", default=None, help="Katalog konfiguracji.")
    p_roe_sign.add_argument("--engagement", required=True, help="engagement_id zlecenia.")
    p_roe_sign.add_argument(
        "--algorithm",
        default=None,
        choices=["hmac-sha256", "ed25519"],
        help="Algorytm podpisu (domyślnie z security.roe.algorithm).",
    )
    p_roe_sign.add_argument(
        "--private-key-file",
        default=None,
        help="Plik z kluczem PRYWATNYM Ed25519 (PEM) — wymagany dla ed25519.",
    )
    p_roe_sign.set_defaults(func=_cmd_roe_sign)

    p_roe_verify = roe_sub.add_parser("verify", help="Zweryfikuj podpis ROE (kod 0 = ważny).")
    p_roe_verify.add_argument("--config", default=None, help="Katalog konfiguracji.")
    p_roe_verify.add_argument("--engagement", required=True, help="engagement_id zlecenia.")
    p_roe_verify.set_defaults(func=_cmd_roe_verify)

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
