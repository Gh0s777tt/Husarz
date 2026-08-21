"""Bramka egress dla ścieżki wywołania modelu (defense-in-depth).

Router NIE łączy się z hostem w WAN, dopóki nie zezwala na to polityka
``security.egress``. Endpointy lokalne/prywatne (loopback, RFC 1918, ``.local``)
nie są traktowane jako egress i są zawsze dozwolone — tak działa lokalny vLLM/Ollama.

Uwaga: to kontrola na poziomie aplikacji (dobór hosta przed połączeniem).
Pełne wymuszenie sieciowe (NetworkPolicy, sandbox) pozostaje w gestii Etapu 4/6.
"""

from __future__ import annotations

from husarz.config.net import endpoint_host, is_local_endpoint
from husarz.config.schema import EgressConfig, EgressPolicy

# Definicja w `husarz.core.errors` (warstwa najniższa) — zgłasza ją `husarz.ssrf`, który nie
# może zależeć od routera. Re-eksport zachowuje dotychczasową ścieżkę importu.
from husarz.core.errors import EgressError

__all__ = ["EgressError", "check_endpoint_allowed"]


def check_endpoint_allowed(endpoint: str | None, egress: EgressConfig) -> None:
    """Sprawdza, czy wolno połączyć się z ``endpoint`` wg polityki egress.

    Dozwolone bez warunków: endpoint lokalny/prywatny (nie jest ruchem do WAN).
    Dla hostów zdalnych: gdy ``default_policy=allow`` — wolno; gdy ``deny`` —
    host musi znajdować się na ``allowlist`` (dokładnie lub jako subdomena).

    Raises:
        EgressError: gdy host jest zdalny i niedozwolony.
    """
    if is_local_endpoint(endpoint):
        return
    if egress.default_policy is EgressPolicy.ALLOW:
        return
    host = endpoint_host(endpoint) if endpoint is not None else None
    if host is not None and any(
        host == domain or host.endswith(f".{domain}") for domain in egress.allowlist
    ):
        return
    raise EgressError(
        f"Egress zabroniony dla hosta '{host}' (polityka deny-all). "
        f"Dodaj domenę do security.egress.allowlist, aby zezwolić."
    )
