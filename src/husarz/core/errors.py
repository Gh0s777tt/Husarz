"""Wyjątki najniższej warstwy — wspólne dla modułów, które nie mogą zależeć od routera.

**Po co ten moduł istnieje.** `husarz.ssrf` (pinowanie IP, ADR-0020) jest warstwą NIŻSZĄ niż
router modeli: korzystają z niego narzędzia, wtyczki MCP, klient Gita i embedder pamięci.
Mimo to zgłaszał `EgressError` importowany z `husarz.router.egress`, a import podmodułu
pociąga w Pythonie import pakietu nadrzędnego — czyli `husarz/router/__init__.py`, który
importuje `husarz.router.client`, który importuje z powrotem `husarz.ssrf`.

Powstawał cykl:

    husarz.ssrf → husarz.router.egress → husarz.router.__init__
                → husarz.router.client → husarz.ssrf (częściowo zainicjalizowany)

Działało to wyłącznie dlatego, że w praktyce router bywał importowany PIERWSZY. Każdy nowy
moduł sięgający do `husarz.ssrf` przed routerem wywracał się na `ImportError` — zdarzyło się
to czterokrotnie (diagnostyka launchera, warstwa ewaluacji, skrypty diagnostyczne).

Rozwiązaniem jest właściwe warstwowanie, a nie kolejność importów: definicje mieszkają tu,
poniżej wszystkiego, a `husarz.router.errors` i `husarz.router.egress` je re-eksportują.
Dzięki temu WSZYSTKIE dotychczasowe importy (``from husarz.router.egress import EgressError``)
działają bez zmian, a `isinstance`/`except` widzą dokładnie te same klasy.
"""

from __future__ import annotations


class RouterError(Exception):
    """Bazowy wyjątek routera modeli.

    Mieszka w warstwie ``core``, bo dziedziczy z niego :class:`EgressError`, zgłaszany przez
    moduły niższe niż router. Publicznie dostępny jako ``husarz.router.errors.RouterError``.
    """


class EgressError(RouterError):
    """Połączenie wychodzące zablokowane przez politykę egress.

    Dziedziczenie po :class:`RouterError` jest ISTOTNE: ``husarz.api.app`` łapie ``RouterError``
    i mapuje go na odpowiedź HTTP, więc blokada egress musi się w to łapać. Zmiana bazy
    zmieniłaby kod odpowiedzi API.
    """
