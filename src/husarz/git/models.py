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
    """Połączenie z dostawcą Git. ``token_ref`` to referencja do sekretu (env:/file:/vault:).

    Attributes:
        name: Nazwa połączenia (klucz w magazynie).
        provider: Dostawca (GitHub albo GitLab).
        api_base: Baza API dostawcy.
        token_ref: Referencja do sekretu z tokenem — NIGDY sam token.
        username: Nazwa konta u dostawcy (opcjonalna, informacyjna).
        ca_bundle: Ścieżka do pliku PEM z certyfikatem (albo łańcuchem) urzędu, który
            podpisał certyfikat serwera. Potrzebne dla samodzielnie hostowanego GitLaba
            z PRYWATNYM CA. Zaufanie jest ZAWĘŻONE do tego jednego połączenia: bundle
            ZASTĘPUJE magazyn systemowy, zamiast się do niego dokładać, więc prywatne CA
            nie zyskuje prawa poświadczania `api.github.com`. ``None`` = systemowe CA.
    """

    name: str
    provider: GitProviderKind
    api_base: str  # baza API dostawcy (np. https://api.github.com, https://gitlab.com/api/v4)
    token_ref: str
    username: str | None = None
    ca_bundle: str | None = None


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
