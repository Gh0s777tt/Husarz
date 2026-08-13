"""Testy REST API (FastAPI TestClient — bez serwera i sieci)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.orchestrator import PHASE_PLAN, PHASE_REFLECT, PHASE_SYNTH
from husarz.router import ChatResponse
from husarz.security import AuditLog

pytestmark = pytest.mark.unit


class ScriptedRouter:
    """Router testowy: odpowiada wg fazy, echo dla specjalistów (bez sieci)."""

    def complete(self, request, *, agent=None, model=None, tags=None):  # noqa: ANN001
        content = request.messages[-1].content
        if PHASE_PLAN in content:
            return ChatResponse(
                model="glm", content='{"steps": [{"agent": "bielik", "task": "x"}]}'
            )
        if PHASE_REFLECT in content:
            return ChatResponse(model="glm", content='{"done": true}')
        if PHASE_SYNTH in content:
            return ChatResponse(model="glm", content="Gotowe.")
        return ChatResponse(model=f"mock-{agent}", content=f"[{agent}] ok")


@pytest.fixture
def client(repo_config_dir: Path) -> TestClient:
    config = load_config(repo_config_dir)
    app = create_app(
        config,
        config_dir=repo_config_dir,
        audit=AuditLog(),
        router=ScriptedRouter(),
        prompts_dir=repo_config_dir.parent / "prompts",
    )
    return TestClient(app)


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["profile"] == "dev"


def test_config_summary(client: TestClient) -> None:
    body = client.get("/api/config/summary").json()
    assert body["default_model"] == "glm-main"
    assert "husarz" in body["agents"]
    assert body["egress_policy"] == "deny"


def test_agents_models_tools(client: TestClient) -> None:
    agents = client.get("/api/agents").json()
    assert any(a["name"] == "puszkarz" and a["roe_required"] for a in agents)
    assert any(m["id"] == "glm-main" for m in client.get("/api/models").json())
    assert any(t["name"] == "web" and t["requires_egress"] for t in client.get("/api/tools").json())


def test_orchestrate(client: TestClient) -> None:
    response = client.post("/api/orchestrate", json={"task": "Zbuduj moduł."})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Gotowe."
    assert body["observations"][0]["agent"] == "bielik"


def test_orchestrate_unavailable_without_router(repo_config_dir: Path) -> None:
    config = load_config(repo_config_dir)
    app = create_app(config, audit=AuditLog())  # brak routera
    response = TestClient(app).post("/api/orchestrate", json={"task": "x"})
    assert response.status_code == 503


def test_audit_reflects_orchestration(client: TestClient) -> None:
    assert client.get("/api/audit").json()["count"] == 0
    client.post("/api/orchestrate", json={"task": "x"})
    audit = client.get("/api/audit").json()
    assert audit["verified"] is True
    assert audit["count"] >= 1
    assert any(e["action"] == "orchestrate" for e in audit["entries"])


def test_config_validate(client: TestClient) -> None:
    ok = client.post(
        "/api/config/validate", json={"overrides": {"platform": {"log_level": "DEBUG"}}}
    )
    assert ok.json()["ok"] is True
    assert ok.json()["summary"]["log_level"] == "DEBUG"

    bad = client.post("/api/config/validate", json={"overrides": {"platform": {"profile": "zły"}}})
    assert bad.json()["ok"] is False
    assert bad.json()["error"]


def test_usage(client: TestClient) -> None:
    body = client.get("/api/usage").json()
    assert body["orchestrations"] == 0
    assert body["max_tokens_per_request"] == 8192


def test_console_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Husarz" in response.text
