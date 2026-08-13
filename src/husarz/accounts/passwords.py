"""Hashowanie haseł — ``scrypt`` z biblioteki standardowej (memory-hard).

Świadomie BEZ zależności zewnętrznych (argon2/bcrypt): ``hashlib.scrypt`` jest
wbudowany, memory-hard i wystarczająco silny dla samodzielnego hostingu. Hasła
NIGDY nie są trzymane w postaci jawnej — tylko jako ``scrypt$n$r$p$salt$hash``.
Weryfikacja w stałym czasie (``hmac.compare_digest``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

# Parametry scrypt: n (koszt CPU/pamięci), r (rozmiar bloku), p (równoległość).
# n=2**14 to rozsądny kompromis bezpieczeństwo/koszt dla logowania interaktywnego.
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Zwraca zakodowany hash hasła (``scrypt$n$r$p$salt_b64$hash_b64``).

    Argumenty:
        password: hasło w jawnej postaci (wyłącznie w pamięci procesu).
        salt: opcjonalna sól (do testów deterministycznych); domyślnie losowa.

    Wyjątki:
        ValueError: gdy hasło jest puste.
    """
    if not password:
        raise ValueError("Hasło nie może być puste.")
    salt = salt if salt is not None else os.urandom(_SALT_BYTES)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return (
        f"scrypt${_N}${_R}${_P}$"
        f"{base64.b64encode(salt).decode('ascii')}$"
        f"{base64.b64encode(derived).decode('ascii')}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Sprawdza hasło względem zakodowanego hasha. Fail-closed przy błędnym formacie."""
    try:
        algo, n_str, r_str, p_str, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_str),
            r=int(r_str),
            p=int(p_str),
            dklen=len(expected),
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return hmac.compare_digest(derived, expected)
