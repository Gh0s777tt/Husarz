"""Zawężanie treści diagnozy na potrzeby odpowiedzi HTTP.

**Po co osobna warstwa, skoro treść jest jedna.** Interpretacja („co jest problemem, jak to
naprawić") musi mieszkać w jednym miejscu — module diagnozy — inaczej CLI i konsola rozjadą
się w ocenie. Ale ODBIORCA jest różny i to jest realna różnica, nie kosmetyka: w terminalu
siedzi operator maszyny, który i tak zna jej ścieżki, a odpowiedź HTTP dostaje każdy, kto ma
uprawnienie ``diagnostics:read``. Zawężamy więc PREZENTACJĘ, nie ocenę.

**Zakres jest węższy, niż zakładała ROADMAP, i mówimy to wprost.** Zapis mówił o „pełnych
endpointach i ścieżkach bezwzględnych". Pomiar na dostarczonej konfiguracji pokazał, że
ścieżek bezwzględnych diagnoza nie emituje wcale: pojawiają się WYŁĄCZNIE w kontroli katalogów
zapisywalnych i tylko wtedy, gdy operator skonfiguruje je bezwzględnie (domyślnie są względne
— ``./audit``, ``./data``). Ścieżki w rodzaju ``config/models.yaml`` zostają nietknięte:
są konwencją projektu, identyczną w każdej instalacji, więc nie mówią o operatorze nic.

Czego to NIE ukrywa — i nie może, bo diagnoza przestałaby być użyteczna: nazw modeli, nazw
agentów, tagów reguł routingu oraz HOSTA i PORTU endpointu. Bez hosta ustalenie „silnik nie
odpowiedział" nie mówi, który silnik. To jest właśnie ta topologia, przez którą panel
z Etapu 17l odrzucił rozszerzenie ``diagnostics:read`` — zawężenie ładunku obniża stawkę
tamtej dyskusji, ale jej nie zamyka.
"""

from __future__ import annotations

import re

#: Adres z częścią ścieżki: zostawiamy schemat, host i port, ucinamy resztę.
_URL_ZE_SCIEZKA = re.compile(r"\b(https?://[^\s/,)]+)(/[^\s,)]*)")

#: Ścieżka bezwzględna POSIX o co najmniej dwóch segmentach. Wyklucza dopasowanie po
#: dwukropku (żeby nie łapać reszty adresu) i po literze/cyfrze (żeby nie ciąć w środku słowa).
_SCIEZKA_POSIX = re.compile(r"(?<![\w:/])(/(?:[\w.@%+-]+/)+[\w.@%+-]+)")

#: Ścieżka bezwzględna Windows (``C:\dane\husarz\audit``).
_SCIEZKA_WINDOWS = re.compile(r"(?<![\w])([A-Za-z]:\\(?:[\w.@%+ -]+\\)*[\w.@%+ -]+)")


def _skroc_posix(dopasowanie: re.Match[str]) -> str:
    """Zostawia sam ostatni segment ścieżki POSIX."""
    return "…/" + dopasowanie.group(1).rsplit("/", 1)[-1]


def _skroc_windows(dopasowanie: re.Match[str]) -> str:
    """Zostawia sam ostatni segment ścieżki Windows."""
    return "…\\" + dopasowanie.group(1).rsplit("\\", 1)[-1]


def zawez_dla_http(tekst: str) -> str:
    """Skraca adresy i ścieżki bezwzględne w tekście przeznaczonym do odpowiedzi HTTP.

    Kolejność ma znaczenie: najpierw adresy (inaczej reguła ścieżek pocięłaby ich część
    ścieżkową na własną rękę i zostawiła adres w stanie mylącym), potem ścieżki.

    Args:
        tekst: Treść ustalenia albo rady, w postaci przeznaczonej dla terminala.

    Returns:
        Ta sama treść z adresami skróconymi do ``schemat://host:port`` i ścieżkami
        bezwzględnymi skróconymi do ``…/ostatni-segment``.
    """
    bez_sciezek_url = _URL_ZE_SCIEZKA.sub(r"\1", tekst)
    bez_posix = _SCIEZKA_POSIX.sub(_skroc_posix, bez_sciezek_url)
    return _SCIEZKA_WINDOWS.sub(_skroc_windows, bez_posix)
