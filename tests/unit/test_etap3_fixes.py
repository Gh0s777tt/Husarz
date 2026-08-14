"""Testy regresyjne dla poprawek Etapu 3 po adwersaryjnym przeglądzie."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.config import load_config
from husarz.config.schema import EgressConfig, SandboxConfig
from husarz.ssrf import PinnedTarget
from husarz.tools import (
    ExecResult,
    FileEditTool,
    GitTool,
    PathNotAllowedError,
    RagTool,
    RunTestsTool,
    SandboxError,
    SandboxSpec,
    WebTool,
    build_docker_argv,
    build_tools,
    glob_match,
    resolve_within_workspace,
    spec_from_config,
)

pytestmark = pytest.mark.unit

_MODELS = "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"


class FakeExecutor:
    def __init__(self, exit_code: int = 0) -> None:
        self.specs: list[SandboxSpec] = []
        self._exit = exit_code

    def run(self, spec: SandboxSpec) -> ExecResult:
        self.specs.append(spec)
        return ExecResult(exit_code=self._exit, stdout="ok")


class RecFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, target: PinnedTarget, *, timeout: int, max_bytes: int) -> tuple[int, str]:
        self.calls.append((target.connect_url, timeout, max_bytes))
        return 200, "ok"


def _fake_resolve(host: str) -> list[str]:
    """Resolver testowy: każda nazwa → adres publiczny (żaden test nie odpytuje DNS)."""
    return ["93.184.216.34"]


# --------------------------------------------------------------------------
# Sandbox hardening (build_docker_argv / spec_from_config)
# --------------------------------------------------------------------------


def test_argv_has_hardening_flags() -> None:
    argv = build_docker_argv(
        SandboxSpec(command=["ls"], image="img", run_as_user="1000:1000", pids_limit=512)
    )
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert "--read-only" in argv
    assert "--tmpfs" in argv and "/tmp" in argv  # noqa: S108 - ścieżka w kontenerze, nie host
    assert argv[argv.index("--pids-limit") + 1] == "512"


def test_argv_workspace_readonly_mount() -> None:
    argv = build_docker_argv(
        SandboxSpec(
            command=["ls"], image="img", workspace_host_path="/ws", workspace_read_only=True
        )
    )
    assert "/ws:/workspace:ro" in argv


def test_argv_workspace_rw_mount_default() -> None:
    argv = build_docker_argv(SandboxSpec(command=["ls"], image="img", workspace_host_path="/ws"))
    assert "/ws:/workspace" in argv


def test_argv_container_name() -> None:
    argv = build_docker_argv(SandboxSpec(command=["ls"], image="img", container_name="c1"))
    assert argv[argv.index("--name") + 1] == "c1"


def test_argv_rejects_dash_image() -> None:
    with pytest.raises(SandboxError):
        build_docker_argv(SandboxSpec(command=["ls"], image="--privileged"))


def test_spec_from_config_carries_hardening() -> None:
    sandbox = SandboxConfig(image="img", run_as_user="2000:2000", pids_limit=100)
    spec = spec_from_config(["ls"], sandbox, workspace_host_path="/ws")
    assert spec.run_as_user == "2000:2000"
    assert spec.pids_limit == 100
    assert spec.read_only_rootfs is True


# --------------------------------------------------------------------------
# file_edit — max_bytes przy read, bajty w metadata, read deny-glob/traversal
# --------------------------------------------------------------------------


def test_read_enforces_max_bytes(tmp_path: Path) -> None:
    (tmp_path / "big.txt").write_text("123456")
    result = FileEditTool(tmp_path, max_bytes=5).read("big.txt")
    assert result.ok is False
    assert "limit" in result.error


def test_write_metadata_reports_utf8_bytes(tmp_path: Path) -> None:
    result = FileEditTool(tmp_path).write("a.txt", "ą" * 10)  # 20 bajtów UTF-8
    assert result.ok
    assert result.metadata["bytes"] == 20


def test_read_deny_glob_blocked(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SEKRET")
    result = FileEditTool(tmp_path, deny_globs=["**/.env"]).read(".env")
    assert result.ok is False
    assert "deny-glob" in result.error


def test_read_traversal_blocked(tmp_path: Path) -> None:
    result = FileEditTool(tmp_path).read("../evil.txt")
    assert result.ok is False
    assert "workspace" in result.error


# --------------------------------------------------------------------------
# workspace — deny-glob case-insensitive; symlink escape
# --------------------------------------------------------------------------


def test_glob_match_is_case_insensitive() -> None:
    assert glob_match("SECRET.ENV", "**/secret.env")
    assert glob_match("Models/W.bin", "models/**")


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("SEKRET")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    link = workspace / "link"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("brak uprawnień do tworzenia symlinków")
    with pytest.raises(PathNotAllowedError):
        resolve_within_workspace(workspace, "link")


# --------------------------------------------------------------------------
# web — ochrona SSRF + propagacja timeout/max_bytes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1/x",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
    ],
)
def test_web_blocks_internal_ip_literals(url: str) -> None:
    fetcher = RecFetcher()
    tool = WebTool(
        fetcher,
        domain_allowlist=["169.254.169.254", "127.0.0.1", "10.0.0.5", "192.168.1.1"],
        egress=EgressConfig(default_policy="allow"),
    )
    result = tool.fetch(url)
    assert result.ok is False
    assert "SSRF" in result.error
    assert fetcher.calls == []


def test_web_propagates_timeout_and_max_bytes() -> None:
    fetcher = RecFetcher()
    tool = WebTool(
        fetcher,
        domain_allowlist=["example.com"],
        egress=EgressConfig(default_policy="allow"),
        max_bytes=123,
        timeout=7,
        resolve=_fake_resolve,
    )
    tool.fetch("https://example.com/x")
    assert fetcher.calls[0][1] == 7
    assert fetcher.calls[0][2] == 123


# --------------------------------------------------------------------------
# run_tests / git — extra_args, puste args
# --------------------------------------------------------------------------


def test_run_tests_appends_extra_args() -> None:
    executor = FakeExecutor()
    tool = RunTestsTool(executor, command=["pytest", "-q"], sandbox=SandboxConfig(image="img"))
    tool.run(["tests/unit", "-k", "web"])
    assert executor.specs[0].command == ["pytest", "-q", "tests/unit", "-k", "web"]


def test_git_empty_args_guard() -> None:
    executor = FakeExecutor()
    result = GitTool(
        executor, subcommand_allowlist=["status"], sandbox=SandboxConfig(image="img")
    ).run([])
    assert result.ok is False
    assert "podkomendy" in result.error
    assert executor.specs == []


# --------------------------------------------------------------------------
# rag — passthrough metadata
# --------------------------------------------------------------------------


class SpyBackend:
    def __init__(self) -> None:
        self.added: list[tuple] = []

    def add(self, text: str, metadata=None) -> None:  # noqa: ANN001
        self.added.append((text, metadata))

    def search(self, query: str, top_k: int) -> list:
        return []


def test_rag_add_passes_metadata_to_backend() -> None:
    spy = SpyBackend()
    RagTool(spy).add("tekst", {"source": "z1"})
    assert spy.added == [("tekst", {"source": "z1"})]


# --------------------------------------------------------------------------
# loader — okablowanie z config + wartości domyślne przy null
# --------------------------------------------------------------------------


def test_loader_wires_allow_push(write_config, tmp_path: Path) -> None:
    git_yaml = "name: git\nkind: git\nallowlist: [push]\nconfig:\n  allow_push: true\n"
    config_dir = write_config({"models.yaml": _MODELS, "tools/git.yaml": git_yaml})
    tools = build_tools(load_config(config_dir), workspace=tmp_path, executor=FakeExecutor())
    assert tools["git"].run(["push"]).ok is True


def test_loader_wires_deny_globs(write_config, tmp_path: Path) -> None:
    fe_yaml = "name: file_edit\nkind: file_edit\nconfig:\n  deny_globs: ['**/.env']\n"
    config_dir = write_config({"models.yaml": _MODELS, "tools/file_edit.yaml": fe_yaml})
    tools = build_tools(load_config(config_dir), workspace=tmp_path)
    assert tools["file_edit"].write(".env", "x").ok is False


def test_loader_wires_command_and_null_default(write_config, tmp_path: Path) -> None:
    rt_yaml = "name: run_tests\nkind: run_tests\nconfig:\n  command: 'pytest -q --maxfail=1'\n"
    executor = FakeExecutor()
    config_dir = write_config({"models.yaml": _MODELS, "tools/run_tests.yaml": rt_yaml})
    tools = build_tools(load_config(config_dir), workspace=tmp_path, executor=executor)
    tools["run_tests"].run()
    assert executor.specs[0].command == ["pytest", "-q", "--maxfail=1"]


def test_loader_handles_null_settings(write_config, tmp_path: Path) -> None:
    rag_yaml = "name: rag\nkind: rag\nconfig:\n  top_k: null\n"
    config_dir = write_config({"models.yaml": _MODELS, "tools/rag.yaml": rag_yaml})
    tools = build_tools(load_config(config_dir), workspace=tmp_path)  # nie rzuca TypeError
    assert "rag" in tools
