"""Testy e2e ModelRouter na wstrzykniętej fabryce klientów (bez sieci)."""

from __future__ import annotations

import pytest

from husarz.config.schema import ModelSpec
from husarz.router import (
    AllModelsFailedError,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ModelBackendError,
    ModelRouter,
    NoModelAvailableError,
    RateLimitExceededError,
)

pytestmark = pytest.mark.unit

_REGISTRY = {
    "glm": {"backend": "mock", "model": "glm", "tags": ["code"], "fallback": ["hermes"]},
    "hermes": {"backend": "mock", "model": "hermes", "tags": ["tools"]},
}


class FakeClient:
    """Klient testowy o sterowanym zachowaniu."""

    def __init__(
        self,
        model_id: str,
        *,
        error: Exception | None = None,
        record: list[tuple[str, ChatRequest]] | None = None,
    ) -> None:
        self.model_id = model_id
        self._error = error
        self._record = record

    def chat(self, request: ChatRequest) -> ChatResponse:
        if self._record is not None:
            self._record.append((self.model_id, request))
        if self._error is not None:
            raise self._error
        return ChatResponse(
            model=self.model_id, content=f"from {self.model_id}", finish_reason="stop"
        )


def _request() -> ChatRequest:
    return ChatRequest(messages=[ChatMessage("user", "cześć")])


def test_complete_uses_first_candidate(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid))
    resp = router.complete(_request())
    assert resp.model == "glm"
    assert resp.content == "from glm"


def test_complete_falls_back_on_backend_error(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")

    def factory(spec: ModelSpec, model_id: str) -> FakeClient:
        error = ModelBackendError(model_id, "padło") if model_id == "glm" else None
        return FakeClient(model_id, error=error)

    router = ModelRouter(config, client_factory=factory)
    resp = router.complete(_request())
    assert resp.model == "hermes"  # glm zawiódł, użyto fallbacku


def test_complete_all_fail_raises_aggregate(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    router = ModelRouter(
        config,
        client_factory=lambda spec, mid: FakeClient(mid, error=ModelBackendError(mid, "padło")),
    )
    with pytest.raises(AllModelsFailedError) as exc:
        router.complete(_request())
    assert "glm" in str(exc.value)
    assert "hermes" in str(exc.value)


def test_complete_no_model_available(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid))
    with pytest.raises(NoModelAvailableError):
        router.complete(_request(), model="ghost")


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(500, 100), (None, 100), (50, 50)],
)
def test_cost_control_clamps_max_tokens(make_config, requested, expected) -> None:
    config = make_config(
        registry=_REGISTRY, default="glm", cost_controls={"max_tokens_per_request": 100}
    )
    record: list[tuple[str, ChatRequest]] = []
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid, record=record))
    req = ChatRequest(messages=[ChatMessage("user", "x")], max_tokens=requested)
    router.complete(req)
    assert record[0][1].max_tokens == expected


def test_rate_limit_blocks_second_call(make_config) -> None:
    config = make_config(
        registry=_REGISTRY, default="glm", cost_controls={"max_requests_per_minute": 1}
    )
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid))
    router.complete(_request())  # pierwszy przechodzi
    with pytest.raises(RateLimitExceededError):
        router.complete(_request())  # drugi w tej samej minucie — blokada


def test_select_is_exposed(make_config) -> None:
    config = make_config(registry=_REGISTRY, default="glm")
    router = ModelRouter(config, client_factory=lambda spec, mid: FakeClient(mid))
    assert router.select(model="glm") == ["glm", "hermes"]
