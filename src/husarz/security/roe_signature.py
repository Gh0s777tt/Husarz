"""Kryptograficzny podpis ROE — integralność dokumentu autoryzującego Puszkarza.

ROE (*Rules of Engagement*) to jedyny artefakt, który uprawnia Puszkarza do aktywnych
działań wobec KONKRETNYCH celów. Do Etapu 4+ bramka sprawdzała wyłącznie, czy pole
``signature`` jest niepustym łańcuchem — czyli dopisanie ``signature: "abc"`` do pliku
YAML wystarczało, by ROE uznać za ważne. Ten moduł zamienia to na realną weryfikację.

Co dokładnie jest podpisywane
-----------------------------
Podpis obejmuje **kanoniczną postać treści autoryzacyjnej**, nie bajty pliku:
identyfikator zlecenia, właściciela, osobę zatwierdzającą, zakres (CIDR/domeny/wyłączenia),
okno czasowe (znormalizowane do UTC), listy technik, ``consent`` i ``dry_run_default``.
Samo pole ``signature`` jest z payloadu wyłączone (inaczej nie dałoby się go policzyć).

Podpisujemy TREŚĆ, a nie plik, z dwóch powodów:

1. formatowanie YAML, komentarze i kolejność kluczy nie mogą unieważniać podpisu,
2. — ważniejsze — Husarz scala konfigurację z wielu warstw (plik → ENV → nadpisania
   runtime z panelu). Podpisanie bajtów pliku NIE wykryłoby poszerzenia zakresu przez
   ``POST /api/config/runtime``. Kanoniczny payload liczony jest z **efektywnego**
   ``RoeConfig``, więc każda taka zmiana unieważnia podpis.

Separacja domen: payload jest prefiksowany etykietą wersji (``husarz-roe-v1``), żeby ten
sam klucz użyty gdzie indziej nie dał się przenieść do kontekstu ROE.

Algorytmy
---------
- ``hmac-sha256`` — symetryczny, wyłącznie stdlib. Prosty; klucz musi być na maszynie,
  więc chroni przed modyfikacją pliku przez kogoś BEZ dostępu do sekretu.
- ``ed25519`` — asymetryczny (lazy import ``cryptography``, extra ``husarz[roe]``).
  Mocniejszy model: klucz PRYWATNY zostaje u zatwierdzającego, Husarz trzyma wyłącznie
  klucz publiczny. Zalecany dla realnych zleceń.

Kod wrażliwy (kryptografia): porównanie HMAC jest stałoczasowe (``compare_digest``),
a KAŻDY błąd (zły format, brak klucza, nieznany algorytm, uszkodzony base64) kończy się
odmową — nigdy „przepuść, bo nie umiem sprawdzić". Patrz ``docs/BEZPIECZENSTWO.md``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from typing import Any

from husarz.config.schema import RoeConfig
from husarz.security.errors import SecurityError

# Etykieta separacji domen — zmiana formatu payloadu MUSI podnieść wersję (stare podpisy
# przestaną pasować, co jest zachowaniem pożądanym: fail-closed przy zmianie semantyki).
_DOMAIN_TAG = b"husarz-roe-v1"
# Pola NIE wchodzące do payloadu: sam podpis (liczony z reszty).
_EXCLUDED_FIELDS = frozenset({"signature"})

ALGORITHM_HMAC = "hmac-sha256"
ALGORITHM_ED25519 = "ed25519"
SUPPORTED_ALGORITHMS = (ALGORITHM_HMAC, ALGORITHM_ED25519)


class RoeSignatureError(SecurityError):
    """Błąd podpisu ROE (zły format, brak klucza, nieznany algorytm, brak zależności)."""


def canonical_payload(roe: RoeConfig) -> bytes:
    """Buduje kanoniczną, deterministyczną postać treści autoryzacyjnej ROE.

    Deterministyczna = ten sam ``RoeConfig`` zawsze daje te same bajty: klucze posortowane,
    daty znormalizowane do UTC (robi to walidator schematu), bez zbędnych spacji. Kolejność
    elementów LIST jest zachowana — lista celów to dane, nie zbiór, więc jej przestawienie
    świadomie unieważnia podpis (operator podpisuje to, co widzi).

    Args:
        roe: efektywna konfiguracja ROE (po scaleniu wszystkich warstw).

    Returns:
        Bajty ``husarz-roe-v1`` + ``\\n`` + kanoniczny JSON (UTF-8).
    """
    data: dict[str, Any] = roe.model_dump(mode="json", exclude=set(_EXCLUDED_FIELDS))
    body = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _DOMAIN_TAG + b"\n" + body.encode("utf-8")


def _split_signature(signature: str) -> tuple[str, bytes]:
    """Rozbija ``<alg>:<base64>`` na algorytm i surowe bajty podpisu.

    Raises:
        RoeSignatureError: brak separatora, nieznany algorytm albo zły base64.
    """
    raw = signature.strip()
    algorithm, separator, encoded = raw.partition(":")
    if not separator:
        raise RoeSignatureError(
            "Podpis ROE musi mieć postać '<algorytm>:<base64>' "
            f"(dozwolone algorytmy: {', '.join(SUPPORTED_ALGORITHMS)})."
        )
    algorithm = algorithm.strip().lower()
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise RoeSignatureError(f"Nieznany algorytm podpisu ROE: '{algorithm}'.")
    try:
        # validate=True: znaki spoza alfabetu base64 są błędem, a nie po cichu pomijane.
        blob = base64.b64decode(encoded.strip(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RoeSignatureError("Podpis ROE nie jest poprawnym base64.") from exc
    if not blob:
        raise RoeSignatureError("Podpis ROE jest pusty.")
    return algorithm, blob


def sign_hmac(roe: RoeConfig, key: str) -> str:
    """Podpisuje ROE algorytmem HMAC-SHA256. Zwraca łańcuch ``hmac-sha256:<base64>``.

    Args:
        roe: konfiguracja ROE do podpisania.
        key: sekret współdzielony (z dostawcy sekretów).

    Returns:
        Wartość gotowa do wklejenia w pole ``signature`` pliku ROE.

    Raises:
        RoeSignatureError: pusty klucz (fail-closed — nie podpisujemy „niczym").
    """
    material = key.strip()
    if not material:
        raise RoeSignatureError("Klucz HMAC do podpisu ROE jest pusty.")
    digest = hmac.new(material.encode("utf-8"), canonical_payload(roe), hashlib.sha256).digest()
    return f"{ALGORITHM_HMAC}:{base64.b64encode(digest).decode('ascii')}"


def sign_ed25519(roe: RoeConfig, private_key_pem: str) -> str:
    """Podpisuje ROE kluczem PRYWATNYM Ed25519 (PEM). Zwraca ``ed25519:<base64>``.

    Używane wyłącznie przez narzędzie operatora (``husarz roe sign``) — runtime Husarza
    NIGDY nie potrzebuje klucza prywatnego, bo tylko weryfikuje.

    Args:
        roe: konfiguracja ROE do podpisania.
        private_key_pem: klucz prywatny w formacie PEM (bez hasła).

    Returns:
        Wartość gotowa do wklejenia w pole ``signature``.

    Raises:
        RoeSignatureError: brak pakietu ``cryptography`` albo nieczytelny/niewłaściwy klucz.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
            load_pem_private_key,
        )
    except ImportError as exc:  # pragma: no cover - zależność opcjonalna
        raise RoeSignatureError(
            "Podpisywanie ed25519 wymaga pakietu 'cryptography' (extra: husarz[roe])."
        ) from exc
    try:
        loaded = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise RoeSignatureError("Klucz prywatny ROE (PEM) jest nieczytelny.") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise RoeSignatureError("Klucz prywatny ROE nie jest kluczem Ed25519.")
    blob = loaded.sign(canonical_payload(roe))
    return f"{ALGORITHM_ED25519}:{base64.b64encode(blob).decode('ascii')}"


def _load_ed25519_public_key(key: str) -> Any:
    """Wczytuje klucz publiczny Ed25519 z PEM albo z base64 surowych 32 bajtów.

    Raises:
        RoeSignatureError: brak pakietu ``cryptography`` albo nieczytelny klucz.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
            Ed25519PublicKey,
        )
        from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
            load_pem_public_key,
        )
    except ImportError as exc:  # pragma: no cover - zależność opcjonalna
        raise RoeSignatureError(
            "Weryfikacja ed25519 wymaga pakietu 'cryptography' (extra: husarz[roe])."
        ) from exc

    material = key.strip()
    if "BEGIN PUBLIC KEY" in material:
        try:
            loaded = load_pem_public_key(material.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise RoeSignatureError("Klucz publiczny ROE (PEM) jest nieczytelny.") from exc
        if not isinstance(loaded, Ed25519PublicKey):
            raise RoeSignatureError("Klucz publiczny ROE nie jest kluczem Ed25519.")
        return loaded
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(material, validate=True))
    except (binascii.Error, ValueError) as exc:
        raise RoeSignatureError(
            "Klucz publiczny ROE musi być PEM albo base64 32 surowych bajtów Ed25519."
        ) from exc


def verify(roe: RoeConfig, *, algorithm: str, key: str) -> bool:
    """Weryfikuje podpis ROE. Zwraca ``True`` TYLKO przy poprawnym podpisie.

    Args:
        roe: efektywna konfiguracja ROE (payload liczony z niej).
        algorithm: oczekiwany algorytm z konfiguracji (``hmac-sha256`` albo ``ed25519``).
        key: sekret HMAC albo klucz PUBLICZNY Ed25519 (PEM/base64).

    Returns:
        ``False``, gdy podpisu brak, ma zły format, użyto innego algorytmu niż skonfigurowany
        albo weryfikacja kryptograficzna nie przeszła.

    Raises:
        RoeSignatureError: problem KONFIGURACJI (pusty klucz, nieznany algorytm w configu,
            brak pakietu ``cryptography``). Rozdzielenie jest celowe: „zły podpis" to
            odmowa, a „zepsuta konfiguracja" to błąd operatora, który ma być widoczny.
    """
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise RoeSignatureError(f"Nieznany algorytm weryfikacji ROE: '{algorithm}'.")
    if not key or not key.strip():
        raise RoeSignatureError("Klucz weryfikacji podpisu ROE jest pusty (fail-closed).")
    if not roe.signature or not roe.signature.strip():
        return False
    try:
        declared, blob = _split_signature(roe.signature)
    except RoeSignatureError:
        # Zły format podpisu to NIE błąd konfiguracji Husarza, lecz nieważny dokument.
        return False
    if declared != algorithm:
        # Downgrade-guard: plik deklarujący słabszy/inny algorytm niż skonfigurowany
        # nie może zostać zaakceptowany „bo się zgadza sam ze sobą".
        return False

    payload = canonical_payload(roe)
    if algorithm == ALGORITHM_HMAC:
        expected = hmac.new(key.strip().encode("utf-8"), payload, hashlib.sha256).digest()
        return hmac.compare_digest(expected, blob)

    public_key = _load_ed25519_public_key(key)
    try:
        public_key.verify(blob, payload)
    except Exception:  # noqa: BLE001 - InvalidSignature i pochodne: każdy błąd = odmowa
        return False
    return True
