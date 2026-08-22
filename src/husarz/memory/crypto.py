"""Szyfrowanie at-rest pamięci (RAG) — polityka budowy szyfru na bazie prymitywu z ``core``.

Szyfrujemy CAŁY rekord (tekst + metadane + WEKTOR): inwersja embeddingu rekonstruuje
treść/PII, więc jawny wektor obok zaszyfrowanego tekstu byłby luką. ``AAD = namespace``
wiąże szyfrogram z kolekcją (anti-swap — rekordu nie da się przenieść między kolekcjami).

- ``IdentityCipher`` — brak szyfrowania; dozwolony WYŁĄCZNIE gdy ``encrypt_at_rest=false`` (dev).
- ``AesGcmCipher`` — AES-256-GCM (``cryptography``, opcjonalny extra ``husarz[memory]``),
  klucz z referencji do sekretu (DEK = SHA-256 sekretu → 32 B), losowy nonce 96-bit per rekord.

Fail-closed: ``encrypt_at_rest=true`` bez rozwiązywalnego klucza → błąd (NIGDY cichy plaintext).

**Gdzie mieszka co.** Same klasy szyfrów są prymitywem współdzielonym z magazynem sekretów
(:mod:`husarz.security.secret_store`), więc żyją w :mod:`husarz.core.crypto` — warstwie
niższej niż oba podsystemy. Tutaj zostaje wyłącznie POLITYKA pamięci: kiedy szyfrowanie jest
wymagane, skąd bierze się klucz i jak brzmi błąd, gdy go brak.
"""

from __future__ import annotations

from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.core.crypto import AesGcmCipher, Cipher, IdentityCipher, derive_key
from husarz.core.errors import CryptoError
from husarz.memory.errors import RagBackendError

__all__ = [
    "AesGcmCipher",
    "Cipher",
    "CryptoError",
    "IdentityCipher",
    "build_cipher",
    "derive_key",
]


def build_cipher(
    *, encrypt_at_rest: bool, key_ref: str | None, secrets: SecretsProvider | None = None
) -> Cipher:
    """Buduje szyfr wg polityki at-rest. Fail-closed: szyfrowanie bez klucza → błąd.

    DEK wyprowadzany z sekretu przez SHA-256 (dowolny sekret → 32 B). Sekret WYŁĄCZNIE jako
    referencja rozwiązywana przez ``SecretsProvider`` — nigdy w configu, nigdy w logach.

    Args:
        encrypt_at_rest: Czy pamięć ma być szyfrowana na dysku.
        key_ref: Referencja do sekretu z kluczem (``env:``/``file:``/``vault:``/``sops:``).
        secrets: Dostawca sekretów; brak = :class:`NullSecretsProvider` (nic nie rozwiąże).

    Returns:
        :class:`IdentityCipher` przy wyłączonym szyfrowaniu, inaczej :class:`AesGcmCipher`.

    Raises:
        RagBackendError: Gdy szyfrowanie włączone, a klucza brak, jest nierozwiązywalny albo
            brakuje biblioteki ``cryptography``.
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
    # Fail-closed PRZY BUDOWIE: konstruktor `AesGcmCipher` sam sprawdza dostępność backendu
    # kryptograficznego, więc magazyn nie powstanie bez niego (inaczej błąd wyszedłby
    # dopiero przy pierwszym `add`). Tłumaczymy tylko wyjątek na kontrakt pamięci.
    try:
        return AesGcmCipher(derive_key(material))
    except CryptoError as exc:
        raise RagBackendError(f"Nie można zbudować szyfru pamięci at-rest: {exc}") from exc
