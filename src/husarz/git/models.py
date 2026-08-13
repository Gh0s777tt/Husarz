"""Modele danych integracji Git — połączenie, repozytorium, wynik PR/MR.

``GitConnection`` przechowuje WYŁĄCZNIE referencję do sekretu z tokenem
(``token_ref``), nigdy samego tokenu — zgodnie z zasadą „sekrety poza repo/magazynem".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GitProviderKind(StrEnum):
    """Obsługiwani dostawcy Git."""

    GITHUB = "github"
    GITLAB = "gitlab"


@dataclass(slots=True, frozen=True)
class GitConnection:
    """Połączenie z dostawcą Git. ``token_ref`` to referencja do sekretu (env:/file:/vault:)."""

    name: str
    provider: GitProviderKind
    api_base: str  # baza API dostawcy (np. https://api.github.com, https://gitlab.com/api/v4)
    token_ref: str
    username: str | None = None


@dataclass(slots=True, frozen=True)
class Repo:
    """Repozytorium (znormalizowane między dostawcami)."""

    full_name: str
    default_branch: str
    private: bool
    url: str


@dataclass(slots=True, frozen=True)
class PullRequest:
    """Wynik utworzenia Pull Request (GitHub) / Merge Request (GitLab)."""

    number: int | None
    url: str
    title: str
