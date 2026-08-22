"""Prymityw szyfrowania at-rest — ``Cipher`` (seal/unseal/blind_id) w najniższej warstwie.

**Po co ten moduł istnieje w ``core``.** Szyfr symetryczny jest potrzebny DWÓM niezależnym
podsystemom warstwy 3, z których żaden nie może być zależnością drugiego:

- :mod:`husarz.memory` — szyfrowanie rekordów pamięci długoterminowej (RAG),
- :mod:`husarz.security.secret_store` — zapisywalny magazyn sekretów (tokeny z kreatora połączeń).

Gdyby prymityw został tam, gdzie powstał (``husarz.memory.crypto``), magazyn sekretów
musiałby importować pakiet RAG — zależność odwrotna do intuicji i pułapka przy każdym
kolejnym konsumencie. Definicje mieszkają więc poniżej wszystkiego, a ``husarz.memory.crypto``
je re-eksportuje, dzięki czemu dotychczasowe importy działają bez zmian, a ``isinstance``
i ``except`` widzą dokładnie te same klasy.

**Model kryptograficzny.** AES-256-GCM (AEAD): szyfrogram niesie tag uwierzytelniający, więc
podmiana bajtów jest wykrywalna, a nie cicho tolerowana. ``AAD`` wiąże szyfrogram z jego
kontekstem (u pamięci: nazwa kolekcji; u sekretów: nazwa wpisu) — rekordu nie da się
przenieść w inne miejsce bez unieważnienia tagu (anti-swap). Nonce 96-bitowy losowany per
operacja; przy losowym nonce i kluczu 256-bit ryzyko kolizji jest pomijalne dla skali
tego projektu (rzędu tysięcy rekordów), a klucz nie jest współdzielony między instalacjami.

**Zależność opcjonalna.** ``cryptography`` NIE jest zależnością rdzenia (rdzeń ma pięć
zależności runtime). Import odbywa się wewnątrz funkcji, a brak biblioteki daje czytelny
komunikat po polsku zamiast ``ImportError`` w losowym miejscu.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol, runtime_checkable

from husarz.core.errors import CryptoError

_NONCE_BYTES = 12
_KEY_BYTES = 32


@runtime_checkable
class Cipher(Protocol):
    """Koperta at-rest: ``seal`` szyfruje, ``unseal`` deszyfruje; ``aad`` wiąże z kontekstem.

    ``blind_id`` wyprowadza NIEODWRACALNY (bez klucza) identyfikator wiersza z ``item_id`` —
    dzięki temu jawna kolumna klucza w magazynie nie zdradza odcisku treści (patrz ADR-0018).
    """

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes: ...

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes: ...

    def blind_id(self, item_id: str, *, namespace: str) -> str: ...


class IdentityCipher:
    """Brak szyfrowania (dev). Dozwolony tylko gdy ``encrypt_at_rest=false``."""

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes:
        """Zwraca tekst jawny bez zmian — świadomy no-op trybu deweloperskiego."""
        return plaintext

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes:
        """Zwraca zawartość bez zmian — odpowiednik :meth:`seal`."""
        return sealed

    def blind_id(self, item_id: str, *, namespace: str) -> str:
        """Zwraca identyfikator bez zaślepienia (brak klucza, więc brak HMAC-a)."""
        return item_id


class AesGcmCipher:
    """AES-256-GCM. Sealed = ``nonce(12B) || ciphertext+tag``. ``aad`` uwierzytelnia kontekst.

    Args:
        key: Klucz 32-bajtowy (AES-256). Krótszy/dłuższy → :class:`CryptoError`.

    Raises:
        CryptoError: Gdy klucz ma niewłaściwą długość.
    """

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise CryptoError("Klucz AES-256 musi mieć 32 bajty.")
        self._key = key

    def blind_id(self, item_id: str, *, namespace: str) -> str:
        """Zaślepiony klucz wiersza = HMAC-SHA256(DEK, namespace || 0x00 || item_id) w hex.

        Deterministyczny (zachowuje dedup i ``UNIQUE(namespace,id)``), namespace'owany
        (ten sam tekst w różnych kolekcjach → różny klucz — brak korelacji między kolekcjami),
        a bez DEK nieodwracalny — jawna kolumna ``id`` nie jest odciskiem treści.

        Args:
            item_id: Identyfikator jawny (np. treść wpisu albo jego klucz).
            namespace: Kontekst rozdzielający przestrzenie identyfikatorów.

        Returns:
            Skrót w zapisie szesnastkowym (64 znaki).
        """
        msg = namespace.encode("utf-8") + b"\x00" + item_id.encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes:
        """Szyfruje i uwierzytelnia dane wraz z ``aad``.

        Args:
            plaintext: Dane do zaszyfrowania.
            aad: Kontekst uwierzytelniany, ale NIE szyfrowany (np. nazwa kolekcji/wpisu).

        Returns:
            ``nonce(12B) || szyfrogram+tag``.

        Raises:
            CryptoError: Gdy brakuje biblioteki ``cryptography``.
        """
        aesgcm = _load_aesgcm()
        nonce = os.urandom(_NONCE_BYTES)
        sealed: bytes = aesgcm(self._key).encrypt(nonce, plaintext, aad)
        return nonce + sealed

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes:
        """Odszyfrowuje i weryfikuje tag; ``aad`` musi zgadzać się z użytym przy ``seal``.

        Args:
            sealed: Wynik :meth:`seal`.
            aad: Ten sam kontekst, co przy szyfrowaniu.

        Returns:
            Tekst jawny.

        Raises:
            CryptoError: Gdy szyfrogram jest za krótki, uszkodzony, zaszyfrowany innym
                kluczem albo pochodzi z innego kontekstu (``aad``). Komunikat celowo NIE
                rozróżnia tych przypadków — rozróżnienie byłoby wyrocznią dla atakującego.
        """
        # Kolejność jest ISTOTNA: najpierw _load_aesgcm(), bo to ono tłumaczy brak
        # biblioteki na CryptoError. Import InvalidTag przed nim dałby goły ImportError.
        aesgcm = _load_aesgcm()
        from cryptography.exceptions import InvalidTag  # noqa: PLC0415

        if len(sealed) < _NONCE_BYTES:
            raise CryptoError("Uszkodzony szyfrogram (za krótki).")
        nonce, blob = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        try:
            plaintext: bytes = aesgcm(self._key).decrypt(nonce, blob, aad)
        except InvalidTag as exc:
            # Zły klucz albo AAD (np. rekord z innego kontekstu) — nie ujawniamy nic więcej.
            raise CryptoError("Nie udało się odszyfrować danych (klucz/kontekst).") from exc
        return plaintext


def _load_aesgcm() -> type:
    """Importuje ``AESGCM`` leniwie; brak extra → czytelny :class:`CryptoError`.

    Returns:
        Klasę ``cryptography.hazmat.primitives.ciphers.aead.AESGCM``.

    Raises:
        CryptoError: Gdy biblioteka ``cryptography`` nie jest zainstalowana.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - zależy od zainstalowanych extras
        raise CryptoError(
            "Szyfrowanie wymaga biblioteki 'cryptography' — zainstaluj extra "
            "husarz[memory] (pip install -e '.[dev,memory]')."
        ) from exc
    return AESGCM


def derive_key(material: str) -> bytes:
    """Wyprowadza 32-bajtowy DEK z dowolnego sekretu tekstowego (SHA-256).

    Świadomie NIE jest to KDF z solą i rozciąganiem (PBKDF2/scrypt): materiał wejściowy
    pochodzi z dostawcy sekretów (Vault/SOPS/plik/ENV), więc jest losowym kluczem, a nie
    hasłem wybranym przez człowieka. Rozciąganie chroni przed atakiem słownikowym na
    NISKOENTROPIJNE hasła i nie wnosi tu nic poza kosztem. Gdyby kiedyś dopuścić hasło
    operatora jako źródło, ta funkcja MUSI zostać zastąpiona przez scrypt/Argon2.

    Args:
        material: Materiał sekretu (zostanie przycięty z białych znaków).

    Returns:
        32 bajty klucza.

    Raises:
        CryptoError: Gdy materiał jest pusty.
    """
    cleaned = material.strip()
    if not cleaned:
        raise CryptoError("Pusty materiał klucza — nie można wyprowadzić DEK.")
    return hashlib.sha256(cleaned.encode("utf-8")).digest()
