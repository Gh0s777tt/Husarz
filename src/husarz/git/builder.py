"""Budowa ``GitService`` z konfiguracji (magazyn + egress + dostawca sekretów)."""

from __future__ import annotations

from husarz.config.schema import GitConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.git.client import GitTransport
from husarz.git.connections import FileGitConnectionStore, InMemoryGitConnectionStore
from husarz.git.service import GitService


def build_git_service(
    git: GitConfig,
    security: SecurityConfig,
    *,
    secrets: SecretsProvider | None = None,
    transport: GitTransport | None = None,
) -> GitService:
    """Buduje ``GitService`` wg ``git`` (magazyn) i ``security.egress`` (bramka egress)."""
    store = (
        FileGitConnectionStore(git.connections_path)
        if git.connections_path is not None
        else InMemoryGitConnectionStore()
    )
    return GitService(store, secrets=secrets, egress=security.egress, transport=transport)
