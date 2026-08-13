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

from husarz.config.schema import SandboxConfig
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
    workspace_read_only: bool = False
    read_only_rootfs: bool = True
    pids_limit: int | None = 512
    run_as_user: str | None = None
    # Ścieżki tmpfs W KONTENERZE (zapisywalny /tmp mimo read-only rootfs) — nie host.
    tmpfs: list[str] = field(default_factory=lambda: ["/tmp"])  # noqa: S108
    container_name: str | None = None
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


def spec_from_config(
    command: list[str],
    sandbox: SandboxConfig,
    *,
    workspace_host_path: str | None = None,
    workspace_read_only: bool = False,
) -> SandboxSpec:
    """Buduje ``SandboxSpec`` z konfiguracji sandboxa (spójne dla wszystkich narzędzi)."""
    return SandboxSpec(
        command=command,
        network=sandbox.network,
        cpu_limit=sandbox.cpu_limit,
        memory_limit=sandbox.memory_limit,
        timeout_seconds=sandbox.timeout_seconds,
        image=sandbox.image,
        runtime_class=sandbox.runtime_class,
        run_as_user=sandbox.run_as_user,
        pids_limit=sandbox.pids_limit,
        read_only_rootfs=sandbox.read_only_rootfs,
        workspace_host_path=workspace_host_path,
        workspace_read_only=workspace_read_only,
    )


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
    if spec.image.startswith("-"):
        raise SandboxError(f"Nazwa obrazu nie może zaczynać się od '-': {spec.image!r}.")
    argv = ["docker", "run", "--rm"]
    if spec.container_name:
        argv += ["--name", spec.container_name]
    argv += ["--network", "bridge" if spec.network else "none"]
    if spec.runtime_class:
        argv += ["--runtime", spec.runtime_class]
    argv += ["--cpus", spec.cpu_limit, "--memory", spec.memory_limit]
    if spec.pids_limit is not None:
        argv += ["--pids-limit", str(spec.pids_limit)]
    argv += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    if spec.read_only_rootfs:
        argv += ["--read-only"]
    for mount in spec.tmpfs:
        argv += ["--tmpfs", mount]
    if spec.run_as_user:
        argv += ["--user", spec.run_as_user]
    if spec.workspace_host_path:
        suffix = ":ro" if spec.workspace_read_only else ""
        argv += ["-v", f"{spec.workspace_host_path}:{spec.workdir}{suffix}"]
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

    def run(self, spec: SandboxSpec) -> ExecResult:  # pragma: no cover - wymaga Dockera
        import subprocess  # noqa: PLC0415 - import leniwy, by testy nie wymagały Dockera
        import uuid  # noqa: PLC0415
        from dataclasses import replace  # noqa: PLC0415

        # Nazwany kontener, by po timeout móc go faktycznie ubić (--rm nie zadziała
        # na żywym kontenerze, którego klienta zabił SIGKILL).
        named = replace(
            spec, container_name=spec.container_name or f"husarz-sbx-{uuid.uuid4().hex}"
        )
        argv = build_docker_argv(named)
        try:
            proc = subprocess.run(  # noqa: S603 - argv z allowlisty narzędzi, bez powłoki
                argv,
                capture_output=True,
                text=True,
                timeout=spec.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxError("Docker nie jest dostępny w tym środowisku.") from exc
        except subprocess.TimeoutExpired as exc:
            self._force_remove(named.container_name)
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return ExecResult(exit_code=124, stdout=partial, stderr="timeout", timed_out=True)
        return ExecResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    @staticmethod
    def _force_remove(name: str | None) -> None:  # pragma: no cover - wymaga Dockera
        if not name:
            return
        import contextlib  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(  # noqa: S603
                ["docker", "rm", "-f", name],  # noqa: S607 - stały argv, bez powłoki
                capture_output=True,
                timeout=15,
                check=False,
            )
