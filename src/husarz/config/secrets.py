"""Dostawcy sekretów.

Sekrety NIGDY nie są trzymane w repo ani w plikach konfiguracji. W konfiguracji
mogą występować jedynie *referencje* (np. ``vault:secret/data/husarz#token``),
które ten moduł rozwiązuje do wartości w czasie działania.

Dostawcy:
  - ``NullSecretsProvider`` — nic nie zwraca (domyślny, brak zależności),
  - ``EnvSecretsProvider`` — ``env:NAZWA`` ze zmiennych środowiskowych,
  - ``FileSecretsProvider`` — ``file:nazwa`` z katalogu sekretów (konfinacja),
  - ``SopsSecretsProvider`` — ``sops:plik#klucz`` (deszyfrowanie wstrzykiwalne),
  - ``VaultSecretsProvider`` — ``vault:mount/ścieżka#klucz`` (odczyt wstrzykiwalny).

Backendy Vault/SOPS są wstrzykiwalne (jak executor sandboxa), więc logika jest
testowalna bez zewnętrznych narzędzi; domyślne implementacje wymagają ``sops``/Vault.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from husarz.config.schema import SecretsProviderKind


@runtime_checkable
class SecretsProvider(Protocol):
    """Interfejs dostawcy sekretów."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca wartość sekretu dla referencji albo ``None``, gdy brak."""
        ...


def _strip_scheme(ref: str, scheme: str) -> str:
    prefix = f"{scheme}:"
    return ref[len(prefix) :] if ref.startswith(prefix) else ref


def _navigate(data: Any, dotted_key: str) -> str | None:
    """Nawiguje po zagnieżdżonym słowniku wg klucza ``a.b.c``; zwraca string lub None."""
    current: Any = data
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


class NullSecretsProvider:
    """Dostawca, który nic nie zwraca. Bezpieczny domyślny wybór."""

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        return None


class EnvSecretsProvider:
    """Rozwiązuje referencje ``env:NAZWA`` (lub samą NAZWĘ) ze środowiska."""

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        name = _strip_scheme(ref, "env")
        return os.environ.get(name)


class FileSecretsProvider:
    """Rozwiązuje ``file:nazwa`` z katalogu sekretów (jeden plik = jeden sekret)."""

    def __init__(self, secrets_dir: str | Path) -> None:
        self._dir = Path(secrets_dir)

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        name = _strip_scheme(ref, "file")
        base = self._dir.resolve()
        target = (base / name).resolve()
        if not target.is_relative_to(base):  # konfinacja — brak path traversal
            return None
        try:
            return target.read_text(encoding="utf-8").strip()
        except OSError:
            return None


class SopsSecretsProvider:
    """Rozwiązuje ``sops:plik#klucz.zagnieżdżony`` przez wstrzykiwalny dekryptor."""

    def __init__(self, decrypt: Callable[[str], dict[str, Any]] | None = None) -> None:
        self._decrypt = decrypt or _default_sops_decrypt

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        body = _strip_scheme(ref, "sops")
        path, _, key = body.partition("#")
        if not path or not key:
            return None
        try:
            data = self._decrypt(path)
        except (OSError, ValueError):
            return None
        return _navigate(data, key)


class VaultSecretsProvider:
    """Rozwiązuje ``vault:mount/ścieżka#klucz`` przez wstrzykiwalny odczyt."""

    def __init__(self, read: Callable[[str], dict[str, Any]]) -> None:
        self._read = read

    def resolve(self, ref: str) -> str | None:  # noqa: D102 - patrz Protocol
        body = _strip_scheme(ref, "vault")
        path, _, key = body.partition("#")
        if not path or not key:
            return None
        try:
            data = self._read(path)
        except (OSError, ValueError):
            return None
        return _navigate(data, key)


def _default_sops_decrypt(path: str) -> dict[str, Any]:  # pragma: no cover - wymaga sops
    import json  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    proc = subprocess.run(  # noqa: S603
        ["sops", "-d", "--output-type", "json", path],  # noqa: S607 - stały argv, bez powłoki
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    result: dict[str, Any] = json.loads(proc.stdout)
    return result


def get_secrets_provider(kind: SecretsProviderKind) -> SecretsProvider:
    """Fabryka dostawców prostych (``none``/``env``).

    ``vault``/``sops`` wymagają jawnej konstrukcji z parametrami (adres/dekryptor),
    więc buduj je bezpośrednio: ``VaultSecretsProvider(read=...)`` / ``SopsSecretsProvider()``.
    """
    if kind is SecretsProviderKind.NONE:
        return NullSecretsProvider()
    if kind is SecretsProviderKind.ENV:
        return EnvSecretsProvider()
    if kind in (SecretsProviderKind.VAULT, SecretsProviderKind.SOPS):
        raise ValueError(
            f"Dostawca '{kind.value}' wymaga jawnej konstrukcji z parametrami "
            f"(np. VaultSecretsProvider(read=...), SopsSecretsProvider(decrypt=...))."
        )
    raise ValueError(f"Nieznany rodzaj dostawcy sekretów: {kind!r}")
