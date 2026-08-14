"""Narzędzie web — pobieranie treści WYŁĄCZNIE z dozwolonych domen.

Trzy warstwy obrony, w tej kolejności:

1. **allowlista domen narzędzia** (L1, per-narzędzie w ``config/tools``),
2. **globalna polityka egress** (``check_endpoint_allowed`` z routera — ta sama definicja
   „lokalny/dozwolony", co reszta systemu),
3. **anty-SSRF z pinowaniem IP** (``husarz.ssrf``) — nazwa rozwiązywana DOKŁADNIE RAZ, każdy
   adres sprawdzany, połączenie idzie do przypiętego literału IP (domknięcie okna TOCTOU
   DNS-rebindingu). Loopback jest dla tej ścieżki ZABRONIONY (także przez nazwę).

Fetcher (HTTP) jest wstrzykiwalny, więc testy nie wykonują połączeń sieciowych.

Kod wrażliwy (sieć) — patrz ``docs/BEZPIECZENSTWO.md`` i ADR-0020.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from husarz.config.net import endpoint_host
from husarz.config.schema import EgressConfig, EgressPolicy
from husarz.router.egress import EgressError, check_endpoint_allowed
from husarz.ssrf import HostResolver, PinnedTarget, build_pinned_target, default_resolve
from husarz.tools.base import ToolResult
from husarz.tools.errors import FetchError

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_TIMEOUT = 20
# Maksymalny rozmiar JEDNEJ iteracji odczytu — patrz komentarz przy `iter_bytes`.
_READ_CHUNK_BYTES = 64 * 1024


@runtime_checkable
class Fetcher(Protocol):
    """Warstwa HTTP: zwraca ``(status, treść)``. Testy wstrzykują własną implementację.

    Przyjmuje ``PinnedTarget`` (a NIE goły URL) celowo: pin jest częścią kontraktu, więc
    implementacja transportu nie może go przypadkiem pominąć i rozwiązać nazwy ponownie.
    """

    def __call__(
        self, target: PinnedTarget, *, timeout: int, max_bytes: int
    ) -> tuple[int, str]: ...


class HttpxFetcher:
    """Produkcyjny fetcher oparty o httpx (import leniwy).

    Łączy się z ``target.connect_url`` (literał IP dla nazw domenowych), ale nagłówek ``Host``
    i SNI/weryfikacja certyfikatu idą po ORYGINALNEJ nazwie — połączenie po IP nie degraduje
    TLS. ``follow_redirects=False`` (przekierowanie omijałoby walidację i pin), ``verify=True``
    jawnie. Ciało czytane strumieniowo z twardym sufitem ``max_bytes`` oraz bezwzględnym
    deadline'em wall-clock (ochrona przed OOM i „slow-drip", gdzie per-read timeout httpx
    resetuje się przy każdym chunku).

    Awarie sieci (DNS/TCP/TLS/timeout) są opakowywane w ``FetchError`` z GENERYCZNYM
    komunikatem — surowy wyjątek ``httpx`` nie przechodzi przez pętlę agenta i nie wynosi
    URL-a ani wnętrzności biblioteki do audytu/API (parytet z ``PluginTransportError``).
    """

    def __call__(self, target: PinnedTarget, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        try:
            import httpx  # noqa: PLC0415 - import leniwy
        except ImportError as exc:  # pragma: no cover - httpx deklarowane w pyproject
            raise FetchError("Pakiet 'httpx' nie jest zainstalowany.") from exc

        headers: dict[str, str] = {}
        if target.host_header is not None:
            headers["Host"] = target.host_header
        extensions: dict[str, Any] = {}
        if target.sni_hostname is not None:
            # httpcore używa tego jako ``server_hostname`` w start_tls → certyfikat jest
            # weryfikowany wobec NAZWY, mimo że łączymy się z literałem IP.
            extensions["sni_hostname"] = target.sni_hostname

        buffer = bytearray()
        deadline = time.monotonic() + timeout
        # ``httpx.Client`` (a nie ``httpx.stream``), bo tylko klient przyjmuje ``extensions``,
        # którymi przekazujemy ``sni_hostname`` do warstwy TLS.
        try:
            with (
                httpx.Client(
                    timeout=timeout, follow_redirects=False, verify=True, trust_env=False
                ) as client,
                client.stream(
                    "GET", target.connect_url, headers=headers, extensions=extensions
                ) as response,
            ):
                # `chunk_size` ogranicza JEDNĄ iterację: bez niego httpx oddaje cały
                # zdekompresowany blok naraz, więc odpowiedź gzip mogłaby przekroczyć
                # `max_bytes` o rzędy wielkości ZANIM sprawdzimy warunek (zip-bomba).
                for chunk in response.iter_bytes(chunk_size=_READ_CHUNK_BYTES):
                    buffer += chunk
                    if len(buffer) >= max_bytes:
                        break
                    if time.monotonic() > deadline:
                        raise FetchError("Przekroczono całkowity limit czasu odpowiedzi serwera.")
                status = response.status_code
        except httpx.HTTPError as exc:
            # Komunikat GENERYCZNY — bez URL/wnętrzności httpx (nie wyciekają do audytu/API).
            raise FetchError("Błąd transportu HTTP narzędzia web.") from exc
        return status, bytes(buffer[:max_bytes]).decode("utf-8", "replace")


class WebTool:
    """Pobieranie stron z domen dozwolonych (allowlista narzędzia + egress + pin IP)."""

    name = "web"

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        domain_allowlist: list[str],
        egress: EgressConfig,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: int = DEFAULT_TIMEOUT,
        resolve: HostResolver | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._domains = list(domain_allowlist)
        self._egress = egress
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._resolve: HostResolver = resolve if resolve is not None else default_resolve

    def _domain_allowed(self, host: str) -> bool:
        return any(host == domain or host.endswith(f".{domain}") for domain in self._domains)

    def _enforce_allowlist_without_local_shortcut(self, host: str) -> None:
        """Egzekwuje allowlistę egress BEZ skrótu „endpoint lokalny jest zawsze wolny".

        ``check_endpoint_allowed`` przepuszcza bez warunków hosty uznane za lokalne, w tym
        nazwy z sufiksem ``.local``/``.internal`` — po samej NAZWIE, bez patrzenia na adres.
        Dla routera modeli to poprawne (lokalny vLLM/Ollama), ale dla narzędzia ``web``
        (sterowanego przez model) tworzyłoby furtkę: nazwa ``cokolwiek.internal`` na
        allowliście narzędzia omijałaby politykę egress — także w profilu ``airgap``.
        Narzędzie ``web`` i tak nie ma prawa sięgać sieci lokalnej (blokada adresów
        wewnętrznych w warstwie 3), więc skrót jest tu wyłącznie stratą.

        Raises:
            EgressError: polityka ``deny`` i host spoza ``security.egress.allowlist``.
        """
        if self._egress.default_policy is EgressPolicy.ALLOW:
            return
        if any(host == domain or host.endswith(f".{domain}") for domain in self._egress.allowlist):
            return
        raise EgressError(
            f"Egress zabroniony dla hosta '{host}' (polityka deny-all). "
            f"Dodaj domenę do security.egress.allowlist, aby zezwolić."
        )

    def fetch(self, url: str) -> ToolResult:
        """Pobiera ``url`` po przejściu wszystkich trzech warstw obrony.

        Args:
            url: adres do pobrania (sterowany przez model — traktowany jako niezaufany).

        Returns:
            ``ToolResult`` z treścią (``ok=True``) albo z powodem odmowy/błędem HTTP.
            Narzędzie NIE rzuca: odmowa egress/SSRF oraz awaria transportu (``FetchError``)
            degradują do ``ok=False``, więc pętla agenta/orkiestracja nie pada.
        """
        if urlsplit(url).scheme not in ("http", "https"):
            return ToolResult(
                self.name, ok=False, error="Narzędzie web obsługuje wyłącznie http(s)://."
            )
        host = endpoint_host(url)
        if host is None:
            return ToolResult(self.name, ok=False, error=f"Nie rozpoznano hosta w URL: {url!r}.")
        if not self._domain_allowed(host):
            return ToolResult(
                self.name, ok=False, error=f"Domena '{host}' spoza allowlisty narzędzia web."
            )
        # Globalna polityka egress (deny-all) — druga warstwa.
        try:
            check_endpoint_allowed(url, self._egress)
            self._enforce_allowlist_without_local_shortcut(host)
        except EgressError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))
        # Trzecia warstwa: anty-SSRF + pin IP. Loopback niedozwolony (także jako nazwa),
        # adresy wewnętrzne/metadanych blokowane niezależnie od allowlist powyżej.
        try:
            target = build_pinned_target(url, allow_loopback=False, resolve=self._resolve)
        except EgressError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))

        try:
            status, text = self._fetcher(target, timeout=self._timeout, max_bytes=self._max_bytes)
        except FetchError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))
        metadata: dict[str, Any] = {"status": status, "host": host}
        if target.pinned_ip is not None:
            metadata["pinned_ip"] = target.pinned_ip
        return ToolResult(
            self.name,
            ok=200 <= status < 300,
            output=text,
            error="" if 200 <= status < 300 else f"HTTP {status}",
            metadata=metadata,
        )
