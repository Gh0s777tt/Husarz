"""Integracje Git (GitHub/GitLab): połączenia, klienci dostawców, tworzenie PR/MR.

Suwerennie i testowalnie: token jako referencja do sekretu (nie plaintext), host
dostawcy przez bramkę egress (deny-all), transport HTTP wstrzykiwalny (testy bez sieci).
"""

from __future__ import annotations

from husarz.git.builder import build_git_service
from husarz.git.client import (
    GitHubProvider,
    GitLabProvider,
    GitProvider,
    GitTransport,
    HttpxGitTransport,
    build_provider,
)
from husarz.git.connections import (
    FileGitConnectionStore,
    GitConnectionStore,
    InMemoryGitConnectionStore,
)
from husarz.git.errors import (
    GitAuthError,
    GitConnectionError,
    GitError,
    GitTransportError,
)
from husarz.git.models import GitConnection, GitProviderKind, PullRequest, Repo
from husarz.git.service import GitService

__all__ = [
    "FileGitConnectionStore",
    "GitAuthError",
    "GitConnection",
    "GitConnectionError",
    "GitConnectionStore",
    "GitError",
    "GitHubProvider",
    "GitLabProvider",
    "GitProvider",
    "GitProviderKind",
    "GitService",
    "GitTransport",
    "GitTransportError",
    "HttpxGitTransport",
    "InMemoryGitConnectionStore",
    "PullRequest",
    "Repo",
    "build_git_service",
    "build_provider",
]
