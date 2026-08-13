"""Narzędzie file_edit — odczyt/zapis plików ograniczony do workspace.

Wszystkie ścieżki przechodzą przez ``resolve_within_workspace`` (konfinacja +
deny-globi), więc narzędzie nie wyjdzie poza workspace ani nie dotknie sekretów/wag.
"""

from __future__ import annotations

from pathlib import Path

from husarz.tools.base import ToolResult
from husarz.tools.errors import PathNotAllowedError
from husarz.tools.workspace import resolve_within_workspace

DEFAULT_MAX_BYTES = 5_000_000


class FileEditTool:
    """Odczyt i zapis plików w obrębie workspace."""

    name = "file_edit"

    def __init__(
        self,
        workspace: str | Path,
        *,
        deny_globs: list[str] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._workspace = Path(workspace)
        self._deny_globs = deny_globs or []
        self._max_bytes = max_bytes

    def read(self, path: str) -> ToolResult:
        try:
            target = resolve_within_workspace(self._workspace, path, deny_globs=self._deny_globs)
        except PathNotAllowedError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(self.name, ok=False, error=f"Nie można odczytać pliku: {exc}")
        return ToolResult(self.name, ok=True, output=content, metadata={"path": path})

    def write(self, path: str, content: str) -> ToolResult:
        if len(content.encode("utf-8")) > self._max_bytes:
            return ToolResult(
                self.name, ok=False, error=f"Plik przekracza limit {self._max_bytes} bajtów."
            )
        try:
            target = resolve_within_workspace(self._workspace, path, deny_globs=self._deny_globs)
        except PathNotAllowedError as exc:
            return ToolResult(self.name, ok=False, error=str(exc))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult(self.name, ok=False, error=f"Nie można zapisać pliku: {exc}")
        return ToolResult(self.name, ok=True, metadata={"path": path, "bytes": len(content)})
