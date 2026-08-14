"""Pomocnicze funkcje sieciowe współdzielone przez konfigurację i router.

Wyodrębnione, by walidacja profilu ``airgap`` (schema) i bramka egress routera
używały tej samej definicji „endpointu lokalnego" (bez duplikacji logiki).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def endpoint_host(endpoint: str) -> str | None:
    """Zwraca host z endpointu (obsługuje adresy z i bez schematu)."""
    parsed = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
    return parsed.hostname


def is_local_endpoint(endpoint: str | None) -> bool:
    """Zwraca ``True``, gdy endpoint jest lokalny/prywatny (albo ``None`` = brak zdalnego).

    Za lokalne uznajemy: brak endpointu, ``localhost``, domeny ``.local``/``.internal``
    oraz adresy loopback i prywatne (RFC 1918 / ULA). Używane przez walidację
    profilu ``airgap`` oraz bramkę egress routera — ruch do takich hostów nie jest
    traktowany jako egress do WAN.
    """
    if endpoint is None:
        return True
    host = endpoint_host(endpoint)
    if not host:
        return False
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def is_loopback_endpoint(endpoint: str | None) -> bool:
    """Zwraca ``True`` TYLKO dla loopbacku (``localhost``/``*.localhost``/127.0.0.0/8/``::1``).

    Ściślejsze niż :func:`is_local_endpoint` (które dopuszcza LAN prywatny i ``.internal``).
    Używane przez startową bramkę airgap dla wtyczek MCP, by zgadzać się z runtime egress
    konektora (``husarz.plugins.client._endpoint_target`` przepuszcza dla hostów spoza
    allowlisty WYŁĄCZNIE loopback) — w profilu airgap dane wtyczki nie mogą opuścić TEGO hosta.

    Uwaga: dla ``*.localhost`` ta funkcja ufa samej NAZWIE (bramka startowa, bez DNS).
    Runtime jest ostrzejszy — ``_endpoint_target`` rozwiązuje taką nazwę i wymaga, by KAŻDY
    adres był loopbackiem (ADR-0020), więc konfiguracja przepuszczona tutaj i tak nie wyjdzie
    poza host.
    """
    if endpoint is None:
        return True
    host = endpoint_host(endpoint)
    if not host:
        return False
    normalized = host.strip("[]").lower()
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return ip.is_loopback
