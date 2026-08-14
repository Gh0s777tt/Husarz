"""Modele danych wtyczek (runtime, niezależne od backendu)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RemoteTool:
    """Narzędzie udostępniane przez zdalny serwer MCP (wynik ``tools/list``).

    Treść pochodzi od NIEZAUFANEGO serwera — pola są znormalizowane do ``str``
    i prezentowane w konsoli wyłącznie z escapowaniem (bez interpretacji).
    """

    name: str
    description: str = ""


@dataclass(slots=True, frozen=True)
class RemoteCallResult:
    """Wynik wywołania zdalnego narzędzia (``tools/call``) znormalizowany do tekstu.

    Treść pochodzi od NIEZAUFANEGO serwera MCP — ``text`` to sklejone bloki ``type=text``
    (bloki binarne/``resource`` są POMIJANE, nigdy nie dereferencjonowane). ``is_error``
    odzwierciedla aplikacyjne ``isError`` odpowiedzi (błąd zdalnego narzędzia), odrębne od
    błędu protokołu JSON-RPC (ten mapuje się na wyjątek ``PluginError``).
    """

    text: str = ""
    is_error: bool = False
