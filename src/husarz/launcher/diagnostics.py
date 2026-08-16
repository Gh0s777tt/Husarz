"""Kontrole diagnostyczne uruchamiane przy starcie launchera.

Moduł odpowiada na pytanie „czy ta konfiguracja w ogóle ma szansę zadziałać na TEJ maszynie",
zanim serwer zacznie nasłuchiwać. Kontrole są **czystymi funkcjami** (bez sieci, bez I/O), więc
w całości testowalne offline — i przeznaczone do ponownego użycia przez przyszłą podkomendę
``husarz doctor`` oraz panel konsoli, żeby diagnostyka w terminalu i w UI nie rozjechały się.

Zasada: kontrola **ostrzega, nie blokuje**. Twarda walidacja krzyżowa byłaby tu błędem —
Husarz w kontenerze legalnie nasłuchuje na ``0.0.0.0:8000``, podczas gdy vLLM działa na
``:8000`` HOSTA, i to nie jest kolizja. Zbyt gorliwa bramka wywróciłaby poprawne wdrożenia.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from husarz.config.net import is_loopback_endpoint
from husarz.config.schema import HusarzConfig

# Porty domyślne dla schematów, gdy URL ich nie podaje (``http://host/v1`` → 80).
_DEFAULT_PORTS = {"http": 80, "https": 443}

# Adresy nasłuchu oznaczające „wszystkie interfejsy tej maszyny" — nasłuch na nich
# obejmuje także loopback, więc endpoint na ``localhost`` NADAL koliduje.
# noqa S104: to zbiór do ROZPOZNAWANIA adresu podanego przez operatora, nie miejsce,
# w którym cokolwiek bindujemy — nasłuch ustawia wyłącznie `uvicorn.run` w cli.py.
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", ""})  # noqa: S104


@dataclass(frozen=True, slots=True)
class PortConflict:
    """Model, którego endpoint wskazuje na port zajmowany przez samego Husarza.

    Attributes:
        model_id: identyfikator modelu z ``models.registry``.
        endpoint: endpoint z konfiguracji (bez zmian, do pokazania operatorowi).
        port: kolidujący port.
    """

    model_id: str
    endpoint: str
    port: int


def _endpoint_port(endpoint: str) -> int | None:
    """Zwraca port endpointu modelu; ``None`` gdy URL jest bezużyteczny.

    Args:
        endpoint: wartość pola ``endpoint`` modelu z rejestru.

    Returns:
        Numer portu (jawny albo domyślny dla schematu) albo ``None``, gdy URL nie ma
        hosta lub niesie port spoza zakresu 0–65535. Kontrola startowa NIGDY nie rzuca —
        chory URL to sprawa walidacji konfiguracji, nie diagnostyki.
    """
    parts = urlsplit(endpoint)
    if not parts.hostname:
        return None
    try:
        # SplitResult.port rzuca ValueError dla portu nieliczbowego lub spoza 0–65535.
        port = parts.port
    except ValueError:
        return None
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme.lower(), 0)
    return port or None


def port_conflicts(config: HusarzConfig, *, host: str, port: int) -> list[PortConflict]:
    """Wskazuje modele, których endpoint celuje w port, na którym stanie sam Husarz.

    Sytuacja jest realna, bo **oba** domyślne porty to 8000: launcher (``--port``) i vLLM
    w dostarczonym ``config/models.yaml``. Bez ostrzeżenia żądanie do takiego modelu wraca
    do własnego API Husarza i kończy się mylącym błędem zamiast czytelnej diagnozy.

    Zgłaszamy WYŁĄCZNIE endpointy loopbackowe: ``http://gpu-box:8000`` to inna maszyna
    i żadna kolizja. Nasłuch na ``0.0.0.0`` obejmuje loopback, więc też się liczy.

    Args:
        config: wczytana konfiguracja Husarza.
        host: adres nasłuchu przekazany do serwera (``--host``).
        port: port nasłuchu (``--port``).

    Returns:
        Lista kolizji, posortowana po identyfikatorze modelu (pusta = brak problemu).
        Modele wyłączone (``enabled: false``) są pomijane — nie zostaną użyte.
    """
    listen_host = host.strip().lower()
    # `is_loopback_endpoint` sam radzi sobie z wartością bez schematu (dokleja `//`),
    # więc podajemy goły host — własne doklejanie dałoby `////host` i pusty hostname.
    listens_on_loopback = listen_host in _WILDCARD_HOSTS or is_loopback_endpoint(listen_host)
    if not listens_on_loopback:
        return []

    conflicts: list[PortConflict] = []
    for model_id, spec in config.models.registry.items():
        if not spec.enabled:
            continue
        endpoint = getattr(spec, "endpoint", "") or ""
        if not endpoint or not is_loopback_endpoint(endpoint):
            continue
        if _endpoint_port(endpoint) == port:
            conflicts.append(PortConflict(model_id=model_id, endpoint=endpoint, port=port))
    return sorted(conflicts, key=lambda c: c.model_id)


def format_port_conflicts(conflicts: list[PortConflict], *, port: int) -> list[str]:
    """Buduje komunikaty ostrzegawcze dla operatora (po polsku, z podpowiedzią naprawy).

    Args:
        conflicts: wynik ``port_conflicts``.
        port: port nasłuchu Husarza.

    Returns:
        Lista linii do wypisania; pusta, gdy nie ma kolizji.
    """
    if not conflicts:
        return []
    models = ", ".join(f"{c.model_id} ({c.endpoint})" for c in conflicts)
    return [
        f"UWAGA: Husarz nasłuchuje na porcie {port}, a ten sam port jest endpointem "
        f"modelu: {models}.",
        "       Żądania do tego modelu trafią z powrotem do API Husarza, nie do backendu. "
        "Zmień --port albo endpoint modelu w config/models.yaml.",
    ]
