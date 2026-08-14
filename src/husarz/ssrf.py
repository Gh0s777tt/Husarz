"""Wspólna warstwa anty-SSRF i pinowania IP dla WSZYSTKICH ścieżek wychodzących.

Używa jej KAŻDA ścieżka, którą Husarz wychodzi na sieć. Różnią się WYŁĄCZNIE dwiema flagami
polityki — ``allow_loopback`` (cel na tej maszynie) i ``allow_lan`` (prywatna sieć operatora):

=========================  ================  ===========
Ścieżka                    allow_loopback    allow_lan
=========================  ================  ===========
narzędzie ``web``          nie               nie
konektor MCP (wtyczki)     tak               nie
integracje Git             nie               tak
embedder pamięci (RAG)     tak               tak
router modeli              tak               tak
=========================  ================  ===========

Polaryzacja wynika z tego, CZYJA infrastruktura jest celem: ``web`` sterowane jest przez model
(najostrzejsze), konektor MCP celuje w usługę na tej maszynie, Git w serwer operatora (własny
albo publiczny), a embedder i router — w modele operatora (z założenia lokalne). NIEZALEŻNIE
od flag zablokowane pozostają metadane chmury (link-local), CGNAT, zakresy zarezerwowane
i tunele osadzające IPv4.

Domyka okno TOCTOU DNS-rebindingu: nazwę rozwiązujemy DOKŁADNIE RAZ, sprawdzamy KAŻDY
zwrócony adres wobec blokady (prywatne/link-local/metadane/zarezerwowane), PRZYPINAMY jeden
adres i zwracamy ``PinnedTarget``. Transport łączy się z tym literałem IP (bez ponownego DNS),
a nagłówek ``Host`` i weryfikacja certyfikatu TLS (SNI) idą po ORYGINALNEJ nazwie — połączenie
po IP nie degraduje TLS.

Fail-closed: pusta lista adresów albo JAKIKOLWIEK adres zablokowany (także w mieszanych
A/AAAA) → ``EgressError``. Nie wybieramy po cichu „czystego" adresu z zatrutej odpowiedzi DNS.

Ten moduł NIE importuje ``httpx`` (jest czysty i w pełni testowalny offline) — składanie URL to
wyłącznie stdlib, a resolver jest wstrzykiwalny.

Kod wrażliwy (sieć) — patrz ``docs/BEZPIECZENSTWO.md``. Czy da się go usunąć? Nie: bez pinowania
walidacja adresu i połączenie używają DWÓCH niezależnych rozwiązań DNS, więc atakujący
kontrolujący strefę może podmienić rekord pomiędzy nimi. Pinowanie ZAWĘŻA powierzchnię ataku
(usuwa drugie rozwiązanie), więc jest konieczne i nie ma bezpieczniejszego zamiennika w warstwie
aplikacji. Pełne wymuszenie sieciowe (NetworkPolicy/sandbox) pozostaje warstwą niezależną.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

from husarz.router.egress import EgressError

# Adres IP (v4/v6) oraz wstrzykiwalny resolver hosta → lista adresów (str).
# Wstrzykiwalny resolver = testy działają bez DNS i bez sieci.
IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
HostResolver = Callable[[str], list[str]]

# Sieci blokowane JAWNIE — właściwości `ipaddress` ich NIE pokrywają (albo pokrywają dopiero
# w nowszym stdlib, a `requires-python = ">=3.11"` dopuszcza 3.11.0). Lista jest częścią bramki
# anty-SSRF: każda pozycja to realna droga do infrastruktury wewnętrznej lub metadanych.
_EXTRA_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT/RFC 6598 — metadane Alibaba, pule węzłów k8s
    ipaddress.ip_network("192.0.0.0/24"),  # IETF protocol assignments
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1
    ipaddress.ip_network("198.18.0.0/15"),  # benchmark (prywatny dopiero od 3.11.9)
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("240.0.0.0/4"),  # zarezerwowane klasy E
    ipaddress.ip_network("fec0::/10"),  # IPv6 site-local (deprecated, wciąż spotykane w LAN)
    ipaddress.ip_network("2002::/16"),  # 6to4 — osadza IPv4 (2002:a9fe:a9fe:: → 169.254.169.254)
    ipaddress.ip_network("2001::/32"),  # Teredo — jw., tunel do dowolnego IPv4
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 well-known — osadza IPv4
    ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use
)

# Sieci LAN operatora (RFC 1918 + ULA). Dopuszczane WYŁĄCZNIE dla ścieżek, które jawnie
# o to proszą (``allow_lan``) — dziś tylko integracje Git, gdzie samodzielnie hostowany
# GitLab pod adresem prywatnym jest legalnym scenariuszem suwerenności. Lista jest WĄSKA
# celowo: ``ipaddress.is_private`` obejmuje także loopback, link-local (metadane chmury)
# i zakresy testowe, więc „przepuść prywatne" nie może być realizowane tą właściwością.
_LAN_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),  # ULA (fc00::/8 + fd00::/8)
)


def _in_networks(
    ip: IpAddress, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
) -> bool:
    """True, gdy adres należy do którejkolwiek sieci z listy (porównanie tylko w tej rodzinie)."""
    return any(ip in network for network in networks if ip.version == network.version)


def parse_ip_literal(host: str) -> IpAddress | None:
    """Parsuje host jako literał IP; rozwija IPv4-mapped IPv6. Nazwa domenowa → ``None``.

    Rozwinięcie ``::ffff:a.b.c.d`` domyka bypass, w którym ``::ffff:169.254.169.254``
    udawałby adres „nie-link-local" (endpoint metadanych chmury).

    Args:
        host: host z URL (dopuszczalne nawiasy IPv6, np. ``[::1]``).

    Returns:
        Obiekt adresu IP albo ``None``, gdy host nie jest literałem.
    """
    stripped = host.strip("[]")
    try:
        ip: IpAddress = ipaddress.ip_address(stripped)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_blocked_address(ip: IpAddress, *, allow_loopback: bool, allow_lan: bool = False) -> bool:
    """True dla adresu wewnętrznego/zarezerwowanego (SSRF).

    Blokuje prywatne (RFC 1918/ULA), link-local (169.254 — metadane chmury), zarezerwowane,
    multicast, ``0.0.0.0`` oraz jawną listę sieci, których stdlib nie klasyfikuje jako
    prywatne (:data:`_EXTRA_BLOCKED_NETWORKS` — m.in. CGNAT 100.64.0.0/10, IPv6 site-local
    ``fec0::/10`` i tunele osadzające IPv4: 6to4/Teredo/NAT64).

    Dwie osie luzowania, każda włączana JAWNIE przez wołającego:

    - ``allow_loopback`` — 127.0.0.0/8 i ``::1``. Konektor MCP: tak (lokalny serwer wtyczki
      to główny przypadek); ``web`` i Git: nie.
    - ``allow_lan`` — WYŁĄCZNIE :data:`_LAN_NETWORKS` (RFC 1918 + ULA). Git: tak (samodzielnie
      hostowany GitLab w sieci operatora); ``web`` i konektor MCP: nie. Ta oś NIE odblokowuje
      loopbacku ani link-local — endpoint metadanych chmury pozostaje twardo zablokowany.

    Args:
        ip: sparsowany adres.
        allow_loopback: czy 127.0.0.0/8 oraz ``::1`` są dozwolone.
        allow_lan: czy prywatna sieć operatora (RFC 1918/ULA) jest dozwolona.

    Returns:
        ``True``, gdy połączenie z tym adresem należy odrzucić.
    """
    if ip.is_loopback:
        return not allow_loopback
    if allow_lan and _in_networks(ip, _LAN_NETWORKS):
        return False
    if ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return True
    return _in_networks(ip, _EXTRA_BLOCKED_NETWORKS)


def is_loopback_host(host: str) -> bool:
    """True TYLKO dla nazwy ``localhost`` i literałów loopback (127.0.0.0/8, ``::1``).

    Świadomie **nie** ufamy sufiksowi ``*.localhost``: RFC 6761 jedynie ZALECA mapowanie takich
    nazw na loopback, a ``getaddrinfo`` przez glibc (bez systemd-resolved) potrafi wysłać je do
    zwykłego DNS — czyli potencjalnie do strefy atakującego. Nazwy ``*.localhost`` przechodzą
    więc normalną ścieżką z rozwiązaniem i weryfikacją adresów (patrz :func:`is_loopback_name`).

    Args:
        host: host z URL.

    Returns:
        ``True``, gdy host jednoznacznie oznacza TĘ maszynę BEZ odpytywania DNS.
    """
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    ip = parse_ip_literal(normalized)
    return ip is not None and ip.is_loopback


def is_loopback_name(host: str) -> bool:
    """True dla nazw ``*.localhost`` — kandydatów na loopback WYMAGAJĄCYCH weryfikacji DNS.

    Args:
        host: host z URL.

    Returns:
        ``True``, gdy nazwa deklaruje loopback sufiksem, ale trzeba to jeszcze sprawdzić.
    """
    return host.strip("[]").lower().endswith(".localhost")


def safe_port(parts: SplitResult, url: str) -> int | None:
    """Zwraca port z URL, zamieniając błąd stdlib na ``EgressError`` (kontrakt „nie rzucamy").

    ``SplitResult.port`` rzuca ``ValueError`` dla portu spoza 0–65535 albo nieliczbowego
    (np. ``https://example.com:99999/x`` — URL sterowany przez model). Bez tej zamiany
    wyjątek uciekłby poza bramki narzędzia i wywrócił pętlę agenta zamiast dać ``ok=False``.

    Args:
        parts: wynik ``urlsplit`` dla ``url``.
        url: oryginalny URL (do komunikatu).

    Returns:
        Numer portu albo ``None``, gdy URL portu nie podaje.

    Raises:
        EgressError: port poza zakresem lub nieliczbowy.
    """
    try:
        return parts.port
    except ValueError as exc:
        raise EgressError(f"URL z niepoprawnym portem: {url!r}.") from exc


def default_resolve(host: str) -> list[str]:
    """Rozwiązuje host do adresów IP (rekordy A i AAAA) przez stdlib.

    Args:
        host: nazwa domenowa.

    Returns:
        Lista adresów jako łańcuchy; PUSTA lista przy błędzie rozwiązania (wołający
        traktuje ją fail-closed, czyli jako odmowę).

    Note:
        Łapiemy także ``UnicodeError``: ``getaddrinfo`` koduje nazwę kodekiem ``idna`` PRZED
        zapytaniem DNS i dla etykiety dłuższej niż 63 znaki rzuca ``UnicodeEncodeError``
        (podklasa ``ValueError``, NIE ``OSError``). Bez tego wyjątek uciekłby poza bramkę
        i wywrócił pętlę agenta zamiast dać odmowę — czyli fail-open na wyjątek.
        DNS nie ma tu własnego limitu czasu (stdlib go nie udostępnia); to samo dotyczyło
        rozwiązania wykonywanego wcześniej wewnątrz ``httpx`` — patrz ADR-0020.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return []
    return [str(info[4][0]) for info in infos]


@dataclass(frozen=True, slots=True)
class PinnedTarget:
    """Cel połączenia z PRZYPIĘTYM adresem IP (anty-rebinding) albo połączenie wprost.

    Attributes:
        connect_url: URL, z którym transport ma się połączyć. Dla nazwy domenowej host jest
            podmieniony na literał IP (IPv6 w nawiasach); dla loopbacku/literału = oryginał.
        host_header: oryginalne ``host[:port]`` (BEZ userinfo) do nadpisania nagłówka ``Host``;
            ``None`` = brak nadpisania (połączenie wprost).
        sni_hostname: oryginalna nazwa dla ``https`` — SNI oraz weryfikacja certyfikatu po
            nazwie, nie po IP; ``None`` dla ``http`` lub połączenia wprost.
        pinned_ip: przypięty adres (do audytu) albo ``None``, gdy nie było DNS ani pinu.
    """

    connect_url: str
    host_header: str | None
    sni_hostname: str | None
    pinned_ip: str | None

    @classmethod
    def direct(cls, url: str) -> PinnedTarget:
        """Połączenie wprost (loopback albo literał IP) — bez DNS i bez nadpisań Host/SNI.

        Args:
            url: oryginalny URL.

        Returns:
            Cel bez pinowania (host w URL jest już adresem docelowym).
        """
        return cls(connect_url=url, host_header=None, sni_hostname=None, pinned_ip=None)


def resolve_and_pin(
    host: str, *, allow_loopback: bool, resolve: HostResolver, allow_lan: bool = False
) -> str:
    """Rozwiązuje nazwę RAZ, sprawdza KAŻDY adres i przypina pierwszy. Fail-closed.

    Pin (``addresses[0]``) jest zwracany DOPIERO po przejściu wszystkich adresów — mieszane
    A/AAAA z choćby jednym adresem wewnętrznym kończą się odmową (NIE „odfiltruj i weź czysty",
    bo zatruta odpowiedź DNS nie staje się wiarygodna przez to, że zawiera też poprawny adres).

    Komunikat odmowy CELOWO nie zawiera rozwiązanego adresu: trafia on do modelu (wynik
    narzędzia), więc podanie „10.0.0.7" zamieniłoby bramkę w kanał rozpoznania sieci
    wewnętrznej (model sterowany prompt-injection mógłby skanować przez komunikaty błędów).

    Args:
        host: nazwa domenowa (nie literał).
        allow_loopback: czy adresy loopback są dopuszczalne dla tej ścieżki.
        resolve: resolver hosta (wstrzykiwalny).
        allow_lan: czy prywatna sieć operatora (RFC 1918/ULA) jest dopuszczalna.

    Returns:
        Przypięty adres IP jako łańcuch.

    Raises:
        EgressError: brak rozwiązania (pusta lista) albo jakikolwiek adres zablokowany.
    """
    addresses = resolve(host)
    if not addresses:
        raise EgressError(f"Nie udało się rozwiązać hosta '{host}' (fail-closed).")
    for addr in addresses:
        ip = parse_ip_literal(addr)
        if ip is None or is_blocked_address(ip, allow_loopback=allow_loopback, allow_lan=allow_lan):
            raise EgressError(
                f"Host '{host}' rozwiązuje się na adres wewnętrzny/metadanych — "
                f"SSRF/DNS-rebinding."
            )
    return addresses[0]


def resolve_loopback_name(host: str, *, resolve: HostResolver) -> str:
    """Rozwiązuje nazwę ``*.localhost`` i WYMAGA, by KAŻDY adres był loopbackiem.

    Args:
        host: nazwa z sufiksem ``.localhost``.
        resolve: resolver hosta (wstrzykiwalny).

    Returns:
        Przypięty adres loopback.

    Raises:
        EgressError: brak rozwiązania albo jakikolwiek adres spoza loopbacku (nazwa
            udaje lokalną, a wskazuje gdzie indziej — omijałaby https i allowlistę egress).
    """
    addresses = resolve(host)
    if not addresses:
        raise EgressError(f"Nie udało się rozwiązać hosta '{host}' (fail-closed).")
    for addr in addresses:
        ip = parse_ip_literal(addr)
        if ip is None or not ip.is_loopback:
            raise EgressError(
                f"Host '{host}' udaje loopback, ale nie rozwiązuje się na adres lokalny."
            )
    return addresses[0]


def pin_fields(url: str, pinned_ip: str) -> PinnedTarget:
    """Buduje ``PinnedTarget`` dla ``url`` z przypiętym IP: połączenie po IP, Host/SNI po nazwie.

    Args:
        url: oryginalny URL (z nazwą domenową).
        pinned_ip: adres zwrócony przez :func:`resolve_and_pin`.

    Returns:
        Cel, w którym ``connect_url`` ma host podmieniony na ``pinned_ip`` (IPv6 nawiasowany),
        z zachowanym schematem/portem/ścieżką/zapytaniem i BEZ userinfo.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = safe_port(parts, url)
    ip_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    netloc = f"{ip_host}:{port}" if port is not None else ip_host
    connect_url = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    host_header = f"{host}:{port}" if port is not None else host
    sni_hostname = host if parts.scheme == "https" else None
    return PinnedTarget(connect_url, host_header, sni_hostname, pinned_ip)


def build_pinned_target(
    url: str, *, allow_loopback: bool, resolve: HostResolver, allow_lan: bool = False
) -> PinnedTarget:
    """Kompozyt dla ścieżki wychodzącej: klasyfikuje host i zwraca cel połączenia.

    Kolejność: literał publiczny → wprost; literał wewnętrzny → odmowa; ``localhost`` → wprost
    (gdy ``allow_loopback``) albo odmowa; ``*.localhost`` → rozwiąż i wymagaj loopbacku;
    pozostała nazwa domenowa → rozwiąż raz i przypnij.

    Adresy ROZWIĄZANEJ nazwy są klasyfikowane z ``allow_loopback=False`` niezależnie od
    argumentu: publiczna nazwa NIE MOŻE rozwiązać się na loopback (hardening przeciw zatrutemu
    DNS i przeciw wyciekowi tokenu do usługi lokalnej). Intencjonalny loopback idzie ścieżką
    literału albo ``localhost`` — nie rozwiązaniem nazwy. ``allow_lan`` jest natomiast
    przepuszczane do rozwiązanych adresów: samodzielnie hostowany serwer (Git) bywa
    adresowany nazwą wskazującą na sieć operatora.

    Args:
        url: pełny URL docelowy.
        allow_loopback: czy ta ścieżka dopuszcza cel loopback (``web``: nie, wtyczka MCP: tak).
        resolve: resolver hosta (wstrzykiwalny).
        allow_lan: czy ta ścieżka dopuszcza prywatną sieć operatora (Git: tak).

    Returns:
        ``PinnedTarget`` gotowy dla transportu.

    Raises:
        EgressError: URL bez hosta, host wewnętrzny/zarezerwowany, niedozwolony loopback,
            nierozwiązywalna nazwa albo nazwa wskazująca adres wewnętrzny.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if host is None:
        raise EgressError(f"URL bez hosta: {url!r}.")
    safe_port(parts, url)  # odrzuć niepoprawny port ZANIM cokolwiek pójdzie do resolvera
    literal = parse_ip_literal(host)
    if literal is not None:
        if is_blocked_address(literal, allow_loopback=allow_loopback, allow_lan=allow_lan):
            raise EgressError(f"Host '{host}' to adres wewnętrzny/zarezerwowany (SSRF).")
        return PinnedTarget.direct(url)
    if is_loopback_host(host):
        if not allow_loopback:
            raise EgressError(f"Host '{host}' to loopback (niedozwolony dla tej ścieżki).")
        return PinnedTarget.direct(url)
    if is_loopback_name(host):
        # `*.localhost` deklaruje loopback, ale nie jest gwarancją — sprawdzamy i przypinamy.
        if not allow_loopback:
            raise EgressError(f"Host '{host}' to loopback (niedozwolony dla tej ścieżki).")
        return pin_fields(url, resolve_loopback_name(host, resolve=resolve))
    pinned = resolve_and_pin(host, allow_loopback=False, resolve=resolve, allow_lan=allow_lan)
    return pin_fields(url, pinned)
