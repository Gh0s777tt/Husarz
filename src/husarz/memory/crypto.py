"""Szyfrowanie at-rest pamięci (RAG) — wstrzykiwalny ``Cipher`` (seal/unseal).

Szyfrujemy CAŁY rekord (tekst + metadane + WEKTOR): inwersja embeddingu rekonstruuje
treść/PII, więc jawny wektor obok zaszyfrowanego tekstu byłby luką. ``AAD = namespace``
wiąże szyfrogram z kolekcją (anti-swap — rekordu nie da się przenieść między kolekcjami).

- ``IdentityCipher`` — brak szyfrowania; dozwolony WYŁĄCZNIE gdy ``encrypt_at_rest=false`` (dev).
- ``AesGcmCipher`` — AES-256-GCM (``cryptography``, opcjonalny extra ``husarz[memory]``),
  klucz z referencji do sekretu (DEK = SHA-256 sekretu → 32 B), losowy nonce 96-bit per rekord.

Fail-closed: ``encrypt_at_rest=true`` bez rozwiązywalnego klucza → błąd (NIGDY cichy plaintext).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol, runtime_checkable

from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.memory.errors import RagBackendError

_NONCE_BYTES = 12


@runtime_checkable
class Cipher(Protocol):
    """Koperta at-rest: ``seal`` szyfruje, ``unseal`` deszyfruje; ``aad`` wiąże z namespace.

    ``blind_id`` wyprowadza NIEODWRACALNY (bez klucza) identyfikator wiersza z ``item_id`` —
    dzięki temu jawna kolumna klucza w magazynie nie zdradza odcisku treści (patrz ADR-0018).
    """

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes: ...

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes: ...

    def blind_id(self, item_id: str, *, namespace: str) -> str: ...


class IdentityCipher:
    """Brak szyfrowania (dev). Dozwolony tylko gdy ``encrypt_at_rest=false``."""

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes:
        return plaintext

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes:
        return sealed

    def blind_id(self, item_id: str, *, namespace: str) -> str:
        # Dev bez szyfrowania — brak sekretu do zaślepienia; zwracamy surowy id (parytet z RAM-em).
        return item_id


class AesGcmCipher:
    """AES-256-GCM. Sealed = ``nonce(12B) || ciphertext+tag``. ``aad`` uwierzytelnia namespace."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise RagBackendError("Klucz AES-256 musi mieć 32 bajty.")
        self._key = key

    def blind_id(self, item_id: str, *, namespace: str) -> str:
        """Zaślepiony klucz wiersza = HMAC-SHA256(DEK, namespace || 0x00 || item_id) w hex.

        Deterministyczny (zachowuje dedup i ``UNIQUE(namespace,id)``), namespace'owany
        (ten sam tekst w różnych kolekcjach → różny klucz — brak korelacji między kolekcjami),
        a bez DEK nieodwracalny — jawna kolumna ``id`` nie jest odciskiem treści.
        """
        msg = namespace.encode("utf-8") + b"\x00" + item_id.encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def seal(self, plaintext: bytes, *, aad: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        nonce = os.urandom(_NONCE_BYTES)
        return nonce + AESGCM(self._key).encrypt(nonce, plaintext, aad)

    def unseal(self, sealed: bytes, *, aad: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag  # noqa: PLC0415
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: PLC0415

        if len(sealed) < _NONCE_BYTES:
            raise RagBackendError("Uszkodzony rekord pamięci (za krótki szyfrogram).")
        nonce, blob = sealed[:_NONCE_BYTES], sealed[_NONCE_BYTES:]
        try:
            return AESGCM(self._key).decrypt(nonce, blob, aad)
        except InvalidTag as exc:
            # Zły klucz albo AAD (np. rekord z innej kolekcji) — nie ujawniamy nic więcej.
            raise RagBackendError(
                "Nie udało się odszyfrować rekordu pamięci (klucz/kolekcja)."
            ) from exc


def build_cipher(
    *, encrypt_at_rest: bool, key_ref: str | None, secrets: SecretsProvider | None = None
) -> Cipher:
    """Buduje szyfr wg polityki at-rest. Fail-closed: szyfrowanie bez klucza → błąd.

    DEK wyprowadzany z sekretu przez SHA-256 (dowolny sekret → 32 B). Sekret WYŁĄCZNIE jako
    referencja rozwiązywana przez ``SecretsProvider`` — nigdy w configu, nigdy w logach.
    """
    if not encrypt_at_rest:
        return IdentityCipher()
    if not key_ref:
        raise RagBackendError(
            "Szyfrowanie at-rest włączone, ale brak encryption_key_ref (referencji do klucza)."
        )
    resolver = secrets if secrets is not None else NullSecretsProvider()
    material = resolver.resolve(key_ref)
    if not material or not material.strip():
        raise RagBackendError(
            f"Nie udało się rozwiązać encryption_key_ref ('{key_ref}') — "
            "pamięć at-rest fail-closed."
        )
    # Fail-closed PRZY BUDOWIE: sprawdź, że backend kryptograficzny jest dostępny, zanim
    # magazyn stanie się używalny (inaczej ImportError wyszedłby dopiero przy pierwszym add).
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401, PLC0415
    except ImportError as exc:
        raise RagBackendError(
            "Szyfrowanie at-rest wymaga biblioteki 'cryptography' — zainstaluj extra "
            "husarz[memory] (pip install -e '.[dev,memory]')."
        ) from exc
    return AesGcmCipher(hashlib.sha256(material.strip().encode("utf-8")).digest())
