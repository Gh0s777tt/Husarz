"""Budowa weryfikatora podpisu ROE z konfiguracji (spina schema → sekrety → ``RoeGate``).

Oddzielone od :mod:`husarz.security.roe_signature` (czysta kryptografia, bez zależności od
dostawcy sekretów) i od :mod:`husarz.security.roe_gate` (czysta polityka, bez zależności od
kryptografii). Dzięki temu każdą z trzech warstw testujemy osobno, a bramka pozostaje
niezależna od tego, JAK weryfikowany jest podpis.
"""

from __future__ import annotations

from collections.abc import Callable

from husarz.config.schema import RoeConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.security.roe_signature import RoeSignatureError, verify

# Weryfikator: efektywne ROE -> czy podpis jest ważny.
RoeVerifier = Callable[[RoeConfig], bool]


def build_roe_verifier(
    security: SecurityConfig, secrets: SecretsProvider | None = None
) -> RoeVerifier | None:
    """Buduje weryfikator podpisu ROE dla ``RoeGate`` (albo ``None``, gdy wyłączony).

    Klucz jest rozwiązywany **leniwie, przy każdej weryfikacji** — nie przechowujemy go
    w domknięciu. Dzięki temu rotacja sekretu (Vault/SOPS) obowiązuje bez restartu, a klucz
    nie leży w pamięci procesu dłużej, niż to konieczne.

    Args:
        security: sekcja ``security`` (czyta ``security.roe``).
        secrets: dostawca sekretów rozwiązujący ``key_ref``.

    Returns:
        Funkcja ``RoeConfig -> bool`` albo ``None``, gdy ``verify_signature=false``
        (dozwolone wyłącznie w profilu ``dev`` — pilnuje tego walidacja krzyżowa configu).

    Raises:
        RoeSignatureError: włączona weryfikacja bez ``key_ref`` albo bez dostawcy sekretów.
            Fail-closed przy STARCIE: lepiej nie wstać, niż wstać z bramką, która przepuszcza.
    """
    config = security.roe
    if not config.verify_signature:
        return None
    if not config.key_ref:
        raise RoeSignatureError(
            "security.roe.verify_signature=true wymaga security.roe.key_ref "
            "(referencji do klucza weryfikującego podpis ROE)."
        )
    if secrets is None:
        raise RoeSignatureError(
            "Weryfikacja podpisu ROE wymaga dostawcy sekretów do rozwiązania key_ref."
        )
    key_ref = config.key_ref
    algorithm = config.algorithm

    def _verify(roe: RoeConfig) -> bool:
        resolved = secrets.resolve(key_ref)
        if not resolved or not resolved.strip():
            # Brak klucza w runtime = nie da się zweryfikować = odmowa (nigdy „przepuść").
            raise RoeSignatureError(
                f"Nie udało się rozwiązać klucza weryfikacji ROE (key_ref='{key_ref}')."
            )
        return verify(roe, algorithm=algorithm, key=resolved.strip())

    return _verify
