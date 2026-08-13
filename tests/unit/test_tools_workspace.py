"""Testy konfinacji ścieżek (workspace) i narzędzia file_edit."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.tools import FileEditTool, PathNotAllowedError, glob_match, resolve_within_workspace

pytestmark = pytest.mark.unit


# --- glob_match / resolve_within_workspace --------------------------------


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("a/b/.env", "**/.env", True),
        (".env", "**/.env", True),
        ("models/glm/x", "models/**", True),
        ("models", "models/**", True),
        ("a/secret.key", "**/*.key", True),
        ("a/note.txt", "**/*.key", False),
        ("src/app.py", "models/**", False),
    ],
)
def test_glob_match(path: str, pattern: str, expected: bool) -> None:
    assert glob_match(path, pattern) is expected


def test_resolve_within_ok(tmp_path: Path) -> None:
    target = resolve_within_workspace(tmp_path, "a/b.txt")
    assert target.is_relative_to(tmp_path.resolve())
    assert target.name == "b.txt"


def test_resolve_traversal_blocked(tmp_path: Path) -> None:
    with pytest.raises(PathNotAllowedError, match="workspace"):
        resolve_within_workspace(tmp_path, "../evil.txt")


def test_resolve_deny_glob_blocked(tmp_path: Path) -> None:
    for bad in [".env", "sub/.env", "models/glm/w.bin"]:
        with pytest.raises(PathNotAllowedError):
            resolve_within_workspace(tmp_path, bad, deny_globs=["**/.env", "models/**"])


# --- FileEditTool ----------------------------------------------------------


def test_write_then_read(tmp_path: Path) -> None:
    tool = FileEditTool(tmp_path)
    write = tool.write("notes/todo.md", "treść")
    assert write.ok
    read = tool.read("notes/todo.md")
    assert read.ok
    assert read.output == "treść"


def test_read_missing_file(tmp_path: Path) -> None:
    assert FileEditTool(tmp_path).read("nie-ma.txt").ok is False


def test_write_traversal_blocked(tmp_path: Path) -> None:
    result = FileEditTool(tmp_path).write("../evil.txt", "x")
    assert result.ok is False
    assert "workspace" in result.error


def test_write_deny_glob_blocked(tmp_path: Path) -> None:
    tool = FileEditTool(tmp_path, deny_globs=["**/.env", "models/**"])
    assert tool.write(".env", "sekret").ok is False
    assert tool.write("models/w.bin", "x").ok is False


def test_write_max_bytes(tmp_path: Path) -> None:
    result = FileEditTool(tmp_path, max_bytes=5).write("big.txt", "123456")
    assert result.ok is False
    assert "limit" in result.error
