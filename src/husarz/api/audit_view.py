"""Kontrolowana ekspozycja szczegółów audytu przez API (allowlista, deny-by-default).

Dziennik audytu zapisuje na dysku PEŁNY kontekst zdarzenia (``AuditEntry.detail``), w tym
argumenty wywołania narzędzia, rozmiary odpowiedzi i przypięty adres IP. To właściwe dla
niemodyfikowalnego pliku, do którego dostęp ma operator hosta — ale NIE dla odpowiedzi API,
którą czyta każdy z uprawnieniem ``audit:read``.

Dotąd `GET /api/audit` nie zwracał `detail` w ogóle. Skutek: konsola pokazywała wiersz
``tool.call`` bez nazwy narzędzia, czyli nie odpowiadała na podstawowe pytanie rozliczalności
— **które narzędzie zostało użyte i czy się powiodło**. Ten moduł otwiera dokładnie tyle,
ile trzeba, żeby na to pytanie odpowiedzieć, i ani pola więcej.

Zasady, na których stoi ta warstwa:

1. **Deny-by-default.** Akcja spoza :data:`_PUBLIC_DETAIL_KEYS` nie ujawnia niczego. Nowy typ
   wpisu audytu NIE zacznie wyciekać swojego payloadu przez przeoczenie — trzeba go tu dopisać
   świadomie, razem z testem.
2. **Allowlista kluczy, nie blocklista.** Wypisujemy pola dozwolone. Blocklista („wszystko
   oprócz ``args``") pękłaby przy pierwszym nowym polu z danymi wrażliwymi.
3. **Tylko skalary.** Wartość niebędąca ``str``/``int``/``bool``/``None`` jest odrzucana, nawet
   gdy klucz jest na allowliście. Zagnieżdżona struktura mogłaby przemycić treść pod
   dozwoloną nazwą.
4. **Twardy limit długości.** Łańcuchy są przycinane — identyfikator narzędzia to nie miejsce
   na wyciek przez rozdęte pole.

Świadomie NIE wystawiamy: ``args`` (treść od modelu/użytkownika — ścieżki, zapytania, potencjalnie
sekrety), ``bytes`` (rozmiar odpowiedzi — kanał boczny o treści), ``pinned_ip`` (szczegół topologii
sieci operatora). Te pola pozostają wyłącznie w dzienniku na dysku.
"""

from __future__ import annotations

from typing import Any

# Maksymalna długość łańcucha w wystawianym szczególe.
_MAX_VALUE_LEN = 64

# Allowlista: akcja audytu → zbiór kluczy `detail`, które wolno pokazać przez API.
# Każdy wpis to świadoma decyzja o ujawnieniu; brak akcji w mapie = nie ujawniamy nic.
_PUBLIC_DETAIL_KEYS: dict[str, frozenset[str]] = {
    # KTÓRE narzędzie, JAKA akcja, CZY się powiodło — sedno rozliczalności pętli narzędziowej.
    "tool.call": frozenset({"tool", "action", "ok"}),
    # Odmowa: dodatkowo POWÓD (np. "allowlist"), bo bez niego operator nie wie, co poprawić.
    "tool.deny": frozenset({"tool", "action", "reason"}),
    # Wyczerpanie budżetu iteracji — sam limit, żeby było wiadomo, o jaki próg chodzi.
    "toolloop.limit": frozenset({"max_iterations"}),
}

# Typy wartości uznawane za bezpieczne do wystawienia.
PublicValue = str | int | bool


def public_detail(action: str, detail: dict[str, Any]) -> dict[str, PublicValue]:
    """Zwraca podzbiór ``detail`` bezpieczny do wystawienia przez API.

    Args:
        action: nazwa akcji audytu (np. ``tool.call``).
        detail: pełny szczegół wpisu, taki jak w dzienniku na dysku.

    Returns:
        Słownik wyłącznie z kluczami dozwolonymi dla tej akcji i wartościami skalarnymi
        (łańcuchy przycięte). Pusty słownik, gdy akcja nie jest na allowliście — to jest
        stan DOMYŚLNY i celowy.
    """
    allowed = _PUBLIC_DETAIL_KEYS.get(action)
    if not allowed:
        return {}
    public: dict[str, PublicValue] = {}
    for key in sorted(allowed):
        if key not in detail:
            continue
        value = detail[key]
        # `bool` jest podklasą `int`, więc łapie go już sam `int`. Wymieniamy go jawnie dla
        # czytelności; przypisujemy BEZ konwersji, żeby `ok: False` nie zamieniło się w `0`
        # (pilnuje tego `test_bool_keeps_its_type_not_collapsed_to_int`).
        if isinstance(value, (bool, int)):
            public[key] = value
        elif isinstance(value, str):
            public[key] = value[:_MAX_VALUE_LEN]
        # Wszystko inne (dict, list, float, None, obiekty) — pomijamy bez wyjątku.
    return public
