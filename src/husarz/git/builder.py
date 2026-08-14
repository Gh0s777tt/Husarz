"""Budowa ``GitService`` z konfiguracji (magazyn + egress + dostawca sekretów)."""

from __future__ import annotations

from husarz.config.schema import GitConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.git.client import GitTransport
from husarz.git.connections import (
    FileGitConnectionStore,
    GitConnectionStore,
    InMemoryGitConnectionStore,
)
from husarz.git.service import GitService
from husarz.ssrf import HostResolver


def build_git_service(
    git: GitConfig,
    security: SecurityConfig,
    *,
    secrets: SecretsProvider | None = None,
    transport: GitTransport | None = None,
    resolve: HostResolver | None = None,
    store: GitConnectionStore | None = None,
) -> GitService:
    """Buduje ``GitService`` wg ``git`` (magazyn) i ``security.egress`` (bramka egress).

    ``resolve`` (opcjonalny) to resolver DNS dla pinowania IP — przewleczony jak w
    ``build_tools``/``build_plugin_service``, żeby testy nie odpytywały sieci (ADR-0020).

    ``store`` (opcjonalny) pozwala PRZEBUDOWAĆ serwis z nową polityką egress, zachowując
    DOTYCHCZASOWY magazyn połączeń. Używa tego ``git_service_factory`` przy nadpisaniu
    konfiguracji w runtime: bez tego przebudowa gubiłaby połączenia dodane przez API
    (magazyn w pamięci), a z tym polityka jest świeża, a dane nienaruszone.
    """
    active_store: GitConnectionStore = (
        store
        if store is not None
        else (
            FileGitConnectionStore(git.connections_path)
            if git.connections_path is not None
            else InMemoryGitConnectionStore()
        )
    )
    return GitService(
        active_store,
        secrets=secrets,
        egress=security.egress,
        transport=transport,
        resolve=resolve,
    )
