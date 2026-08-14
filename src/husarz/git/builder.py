"""Budowa ``GitService`` z konfiguracji (magazyn + egress + dostawca sekretów)."""

from __future__ import annotations

from husarz.config.schema import GitConfig, SecurityConfig
from husarz.config.secrets import SecretsProvider
from husarz.git.client import GitTransport
from husarz.git.connections import FileGitConnectionStore, InMemoryGitConnectionStore
from husarz.git.service import GitService
from husarz.ssrf import HostResolver


def build_git_service(
    git: GitConfig,
    security: SecurityConfig,
    *,
    secrets: SecretsProvider | None = None,
    transport: GitTransport | None = None,
    resolve: HostResolver | None = None,
) -> GitService:
    """Buduje ``GitService`` wg ``git`` (magazyn) i ``security.egress`` (bramka egress).

    ``resolve`` (opcjonalny) to resolver DNS dla pinowania IP — przewleczony jak w
    ``build_tools``/``build_plugin_service``, żeby testy nie odpytywały sieci (ADR-0020).
    """
    store = (
        FileGitConnectionStore(git.connections_path)
        if git.connections_path is not None
        else InMemoryGitConnectionStore()
    )
    return GitService(
        store,
        secrets=secrets,
        egress=security.egress,
        transport=transport,
        resolve=resolve,
    )
