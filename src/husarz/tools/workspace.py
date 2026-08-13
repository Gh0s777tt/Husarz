"""Konfinacja ścieżek do katalogu workspace + deny-globi.

Bezpieczeństwo: narzędzia plikowe nie mogą wyjść poza workspace (path traversal),
ani dotykać ścieżek zablokowanych (sekrety, wagi modeli). Matcher globów obsługuje
``**`` (dowolna liczba segmentów) i jest zgodny z Pythonem 3.11 (bez ``full_match``).
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from husarz.tools.errors import PathNotAllowedError


def _match_segments(path_segments: list[str], pattern_segments: list[str]) -> bool:
    if not pattern_segments:
        return not path_segments
    head, *rest = pattern_segments
    if head == "**":
        # ** dopasowuje zero lub więcej segmentów.
        return any(_match_segments(path_segments[i:], rest) for i in range(len(path_segments) + 1))
    if not path_segments:
        return False
    if fnmatch(path_segments[0], head):
        return _match_segments(path_segments[1:], rest)
    return False


def glob_match(relative_posix: str, pattern: str) -> bool:
    """Dopasowuje ścieżkę względną (posix) do wzorca glob z obsługą ``**``."""
    return _match_segments(relative_posix.split("/"), pattern.split("/"))


def resolve_within_workspace(
    workspace: Path,
    relative_path: str,
    *,
    deny_globs: list[str] | None = None,
) -> Path:
    """Rozwiązuje ścieżkę wewnątrz workspace lub zgłasza ``PathNotAllowedError``.

    Odrzuca wyjście poza workspace (``..``, ścieżki absolutne) oraz dopasowania
    do ``deny_globs`` (np. ``**/.env``, ``models/**``).
    """
    base = workspace.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise PathNotAllowedError(f"Ścieżka poza workspace: {relative_path!r} (odrzucono).")
    relative = target.relative_to(base).as_posix()
    for pattern in deny_globs or []:
        if glob_match(relative, pattern):
            raise PathNotAllowedError(
                f"Ścieżka zablokowana przez deny-glob '{pattern}': {relative}."
            )
    return target
