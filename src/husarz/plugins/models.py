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
