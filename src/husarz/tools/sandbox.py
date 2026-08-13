"""Sandbox narzędzi — specyfikacja, executor i budowa polecenia Dockera.

Bezpieczeństwo: domyślnie BEZ sieci (``--network none``), z limitami CPU/RAM/czasu,
``--cap-drop ALL`` i ``no-new-privileges``, montując wyłącznie workspace. Budowa
argumentów (``build_docker_argv``) jest czysta i testowalna. Realne wykonanie
(``DockerSandboxExecutor``) wymaga Dockera (+ opcjonalnie gVisor ``runsc``) w środowisku —
w testach wstrzykujemy własny executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from husarz.tools.base import ToolResult
from husarz.tools.errors import SandboxError


@dataclass(slots=True)
class SandboxSpec:
    """Specyfikacja pojedynczego uruchomienia w sandboxie."""

    command: list[str]
    workdir: str = "/workspace"
    network: bool = False
    cpu_limit: str = "1"
    memory_limit: str = "512m"
    timeout_seconds: int = 60
    image: str | None = None
    runtime_class: str | None = None
    workspace_host_path: str | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExecResult:
    """Wynik uruchomienia w sandboxie."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


@runtime_checkable
class SandboxExecutor(Protocol):
    """Executor sandboxa."""

    def run(self, spec: SandboxSpec) -> ExecResult: ...


def exec_to_result(tool_name: str, result: ExecResult) -> ToolResult:
    """Mapuje wynik sandboxa na ``ToolResult`` (sukces = kod wyjścia 0)."""
    return ToolResult(
        tool=tool_name,
        ok=result.exit_code == 0,
        output=result.stdout,
        error=result.stderr,
        metadata={"exit_code": result.exit_code, "timed_out": result.timed_out},
    )


def build_docker_argv(spec: SandboxSpec) -> list[str]:
    """Buduje argumenty ``docker run`` dla specyfikacji (czysta funkcja).

    Egzekwuje izolację: brak sieci gdy ``network=False``, limity, brak przywilejów,
    montaż wyłącznie workspace. Wymaga ustawionego obrazu.
    """
    if not spec.image:
        raise SandboxError("Brak obrazu sandboxa (ustaw security.sandbox.image).")
    argv = ["docker", "run", "--rm"]
    argv += ["--network", "bridge" if spec.network else "none"]
    if spec.runtime_class:
        argv += ["--runtime", spec.runtime_class]
    argv += ["--cpus", spec.cpu_limit, "--memory", spec.memory_limit]
    argv += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    if spec.workspace_host_path:
        argv += ["-v", f"{spec.workspace_host_path}:{spec.workdir}"]
    argv += ["-w", spec.workdir]
    for key, value in spec.env.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(spec.image)
    argv += spec.command
    return argv


class DockerSandboxExecutor:
    """Produkcyjny executor: uruchamia polecenie przez ``docker run``.

    Wymaga Dockera (oraz gVisor, gdy ``runtime_class='runsc'``). Nieużywany w testach.
    """

    def run(self, spec: SandboxSpec) -> ExecResult:
        import subprocess  # noqa: PLC0415 - import leniwy, by testy nie wymagały Dockera

        argv = build_docker_argv(spec)
        try:
            proc = subprocess.run(  # noqa: S603 - argv z allowlisty narzędzi, bez powłoki
                argv,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:  # pragma: no cover - zależne od środowiska
            raise SandboxError("Docker nie jest dostępny w tym środowisku.") from exc
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - zależne od środowiska
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return ExecResult(exit_code=124, stdout=partial, stderr="timeout", timed_out=True)
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)
