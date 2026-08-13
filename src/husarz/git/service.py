"""GitService — fasada integracji Git: połączenia + budowa klienta dostawcy.

Token rozwiązywany jest z referencji (``token_ref``) przez dostawcę sekretów DOPIERO
przy operacji (nie jest przechowywany). Egress bazy API sprawdzany jest w
``build_provider`` (deny-all). Transport wstrzykiwalny (testy bez sieci).
"""

from __future__ import annotations

from husarz.config.schema import EgressConfig
from husarz.config.secrets import NullSecretsProvider, SecretsProvider
from husarz.git.client import GitProvider, GitTransport, build_provider
from husarz.git.connections import GitConnectionStore, InMemoryGitConnectionStore
from husarz.git.errors import GitAuthError, GitConnectionError
from husarz.git.models import GitConnection


class GitService:
    """Zarządza połączeniami Git i buduje klientów dostawców na żądanie."""

    def __init__(
        self,
        store: GitConnectionStore | None = None,
        *,
        secrets: SecretsProvider | None = None,
        egress: EgressConfig | None = None,
        transport: GitTransport | None = None,
    ) -> None:
        self._store: GitConnectionStore = (
            store if store is not None else InMemoryGitConnectionStore()
        )
        self._secrets = secrets if secrets is not None else NullSecretsProvider()
        self._egress = egress if egress is not None else EgressConfig()
        self._transport = transport

    def add(self, conn: GitConnection) -> None:
        """Dodaje połączenie (metadane + referencja tokenu; bez sekretu)."""
        self._store.add(conn)

    def remove(self, name: str) -> None:
        """Usuwa połączenie."""
        self._store.remove(name)

    def get(self, name: str) -> GitConnection:
        """Zwraca połączenie. Rzuca ``GitConnectionError``, gdy nie istnieje."""
        conn = self._store.get(name)
        if conn is None:
            raise GitConnectionError(f"Nieznane połączenie Git: '{name}'.")
        return conn

    def list_connections(self) -> list[GitConnection]:
        """Zwraca wszystkie połączenia."""
        return self._store.list_connections()

    def provider_for(self, name: str) -> GitProvider:
        """Buduje klienta dostawcy dla połączenia (rozwiązuje token, sprawdza egress).

        Raises:
            GitConnectionError: nieznane połączenie.
            GitAuthError: nie udało się rozwiązać tokenu z ``token_ref``.
            EgressError: host bazy API niedozwolony (deny-all).
        """
        conn = self.get(name)
        token = self._secrets.resolve(conn.token_ref)
        if not token or not token.strip():
            raise GitAuthError(
                f"Nie udało się rozwiązać tokenu połączenia '{name}' "
                f"(token_ref='{conn.token_ref}')."
            )
        return build_provider(conn, token.strip(), self._egress, transport=self._transport)
