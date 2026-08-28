"""Weryfikacja podpisów Ed25519 — wspólna dla ROE i aktualizacji.

**Dlaczego moduł wspólny, a nie druga kopia.** Weryfikacja podpisu jest bramką
bezpieczeństwa. Dwie kopie takiego kodu rozjeżdżają się przy pierwszej poprawce jednej
z nich, a rozjazd w bramce znaczy, że jedna droga przyjmuje to, co druga odrzuca. Kod
powstał dla ROE (``husarz.security.roe_signature``); przy dokładaniu aktualizacji został
wydzielony, a ROE korzysta odtąd z tego samego miejsca.

**Trzy formaty klucza publicznego — i to nie jest wygoda.** Dokumentacja projektu podaje
operatorowi polecenie ``ssh-keygen -t ed25519``, a ono wytwarza klucz w formacie OpenSSH
(``ssh-ed25519 AAAA…``). Pierwotny kod czytał wyłącznie PEM i surowy base64, więc klucz
wygenerowany zgodnie z własną instrukcją projektu byłby ODRZUCONY — z komunikatem
sugerującym, że klucz jest zły. Obsługujemy więc wszystkie trzy postacie.

Kod wrażliwy (kryptografia): KAŻDY błąd — zły format, uszkodzony base64, nieprawidłowy
podpis — kończy się odmową, nigdy „przepuść, bo nie wiem". Zależność ``cryptography``
importujemy leniwie: rdzeń Husarza jej nie wymaga (extra ``husarz[roe]``).
"""

from __future__ import annotations

import base64
import binascii
import struct
from typing import Any

from husarz.security.errors import SecurityError

#: Etykieta typu klucza w formacie OpenSSH.
_TYP_OPENSSH = b"ssh-ed25519"
#: Ed25519 ma klucz publiczny długości dokładnie 32 bajtów.
_DLUGOSC_KLUCZA = 32


class Ed25519Error(SecurityError):
    """Nie da się wczytać klucza publicznego albo zweryfikować podpisu."""


def _rozbierz_openssh(material: str) -> bytes | None:
    """Wyłuskuje surowe 32 bajty klucza z postaci OpenSSH. ``None`` = to nie ten format.

    Format: ``ssh-ed25519 <base64> [komentarz]``, gdzie base64 koduje sekwencję pól
    poprzedzonych 4-bajtową długością (big-endian): najpierw etykieta typu, potem klucz.
    Sprawdzamy etykietę WEWNĄTRZ blobu, a nie tylko przedrostek tekstowy — inaczej klucz
    innego algorytmu podpisany etykietą ``ssh-ed25519`` przeszedłby jako Ed25519.

    Args:
        material: Zawartość pliku ``.pub`` albo pojedyncza linia klucza.

    Returns:
        32 bajty klucza publicznego albo ``None``, gdy to nie jest format OpenSSH.

    Raises:
        Ed25519Error: Gdy postać wygląda na OpenSSH, ale jest uszkodzona.
    """
    czesci = material.split()
    if len(czesci) < 2 or czesci[0] != "ssh-ed25519":
        return None
    try:
        blob = base64.b64decode(czesci[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise Ed25519Error("Klucz OpenSSH ma uszkodzone kodowanie base64.") from exc

    pozycja = 0
    pola: list[bytes] = []
    while pozycja + 4 <= len(blob):
        (dlugosc,) = struct.unpack(">I", blob[pozycja : pozycja + 4])
        pozycja += 4
        if dlugosc > len(blob) - pozycja:
            raise Ed25519Error("Klucz OpenSSH jest obcięty (zadeklarowana długość pola).")
        pola.append(blob[pozycja : pozycja + dlugosc])
        pozycja += dlugosc
    if len(pola) != 2 or pola[0] != _TYP_OPENSSH:
        raise Ed25519Error("Klucz OpenSSH nie jest kluczem Ed25519.")
    if len(pola[1]) != _DLUGOSC_KLUCZA:
        raise Ed25519Error(
            f"Klucz Ed25519 musi mieć {_DLUGOSC_KLUCZA} bajtów, a ma {len(pola[1])}."
        )
    return pola[1]


def wczytaj_klucz_publiczny(material: str) -> Any:
    """Wczytuje klucz publiczny Ed25519 z PEM, OpenSSH albo base64 surowych 32 bajtów.

    Args:
        material: Materiał klucza (już rozwiązany z referencji).

    Returns:
        Obiekt klucza publicznego biblioteki ``cryptography``.

    Raises:
        Ed25519Error: Brak pakietu ``cryptography`` albo nieczytelny klucz.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
            load_pem_public_key,
        )
    except ImportError as exc:  # pragma: no cover - zależność opcjonalna
        raise Ed25519Error(
            "Weryfikacja ed25519 wymaga pakietu 'cryptography' (extra: husarz[roe])."
        ) from exc

    tekst = material.strip()
    if not tekst:
        raise Ed25519Error("Materiał klucza publicznego jest pusty.")

    if "BEGIN PUBLIC KEY" in tekst:
        try:
            wczytany = load_pem_public_key(tekst.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise Ed25519Error("Klucz publiczny (PEM) jest nieczytelny.") from exc
        if not isinstance(wczytany, Ed25519PublicKey):
            raise Ed25519Error("Klucz publiczny (PEM) nie jest kluczem Ed25519.")
        return wczytany

    surowe = _rozbierz_openssh(tekst)
    if surowe is not None:
        return Ed25519PublicKey.from_public_bytes(surowe)

    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(tekst, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise Ed25519Error(
            "Klucz publiczny musi być PEM, postacią OpenSSH (`ssh-ed25519 AAAA…`) "
            "albo base64 32 surowych bajtów Ed25519."
        ) from exc


def zweryfikuj(payload: bytes, podpis: bytes, material_klucza: str) -> bool:
    """Sprawdza podpis Ed25519 nad ``payload``.

    Args:
        payload: Dokładnie te bajty, które zostały podpisane.
        podpis: Surowe bajty podpisu.
        material_klucza: Klucz publiczny w dowolnej z obsługiwanych postaci.

    Returns:
        ``True`` WYŁĄCZNIE przy poprawnym podpisie.

    Raises:
        Ed25519Error: Gdy klucza nie da się wczytać. Zły PODPIS nie jest wyjątkiem —
            jest odpowiedzią ``False``, bo to normalny wynik weryfikacji, a nie awaria.
    """
    klucz = wczytaj_klucz_publiczny(material_klucza)
    try:
        klucz.verify(podpis, payload)
    except Exception:  # noqa: BLE001 - InvalidSignature i pochodne: każdy błąd = odmowa
        return False
    return True
