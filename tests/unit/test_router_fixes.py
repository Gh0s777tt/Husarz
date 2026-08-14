"""Testy regresyjne dla poprawek routera po adwersaryjnym przeglądzie Etapu 1."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from husarz.config.schema import CostControls, ModelSpec
from husarz.config.secrets import EnvSecretsProvider
from husarz.router import (
    AllModelsFailedError,
    ChatMessage,
    ChatRequest,
    HttpxTransport,
    MockClient,
    ModelBackendError,
    ModelRouter,
    OpenAICompatClient,
    RateLimiter,
    RateLimitExceededError,
    TransportError,
    build_client,
    select_candidates,
)
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.unit

_OPENAI_RESPONSE = {
    "choices": [{"message": {"content": "Cześć"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


class CapturingTransport:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self.calls: list[dict[str, Any]] = []
        self._response = response if response is not None else _OPENAI_RESPONSE
        self._error = error

    def __call__(self, target, headers, payload, timeout):  # noqa: ANN001
        self.calls.append(
            {
                "url": target.connect_url,
                "target": target,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        if self._error is not None:
            raise self._error
        return self._response


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeClient:
    def __init__(self, model_id: str, *, error: Exception | None = None):
        self.model_id = model_id
        self._error = error

    def chat(self, request):  # noqa: ANN001
        if self._error is not None:
            raise self._error
        from husarz.router import ChatResponse

        return ChatResponse(model=self.model_id, content=f"from {self.model_id}")


def _spec(**overrides: Any) -> ModelSpec:
    base: dict[str, Any] = {
        "backend": "openai_compat",
        "model": "glm-5.2",
        "endpoint": "http://localhost:8000/v1",
        "params": {"temperature": 0.3},
    }
    base.update(overrides)
    return ModelSpec(**base)


# --------------------------------------------------------------------------
# client.py — payload, precedencja, parsowanie
# --------------------------------------------------------------------------


def test_extra_cannot_override_canonical_model_and_messages() -> None:
    transport = CapturingTransport()
    client = OpenAICompatClient(_spec(), "glm-main", api_key=None, transport=transport)
    req = ChatRequest(
        messages=[ChatMessage("user", "prawdziwa")],
        extra={"model": "zły", "messages": [{"role": "user", "content": "podmiana"}]},
    )
    client.chat(req)
    payload = transport.calls[0]["payload"]
    assert payload["model"] == "glm-5.2"
    assert payload["messages"] == [{"role": "user", "content": "prawdziwa"}]


def test_typed_request_fields_win_over_extra_plus_stop() -> None:
    transport = CapturingTransport()
    client = OpenAICompatClient(_spec(), "glm-main", api_key=None, transport=transport)
    req = ChatRequest(
        messages=[ChatMessage("user", "hej")],
        temperature=0.1,
        max_tokens=64,
        stop=["\n\n"],
        extra={"temperature": 0.9, "top_p": 0.8},
    )
    client.chat(req)
    payload = transport.calls[0]["payload"]
    assert payload["stop"] == ["\n\n"]
    assert payload["top_p"] == 0.8  # extra dokłada nowe klucze
    assert payload["temperature"] == 0.1  # jawne pole żądania wygrywa nad extra
    assert payload["max_tokens"] == 64


def test_usage_absent_yields_none() -> None:
    resp = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    client = OpenAICompatClient(
        _spec(), "glm-main", api_key=None, transport=CapturingTransport(response=resp)
    )
    result = client.chat(ChatRequest(messages=[ChatMessage("user", "x")]))
    assert result.usage is None
    assert result.content == "ok"
    assert result.finish_reason == "stop"


def test_null_content_raises_backend_error() -> None:
    resp = {"choices": [{"message": {"content": None}, "finish_reason": "tool_calls"}]}
    client = OpenAICompatClient(
        _spec(), "glm-main", api_key=None, transport=CapturingTransport(response=resp)
    )
    with pytest.raises(ModelBackendError, match="content"):
        client.chat(ChatRequest(messages=[ChatMessage("user", "x")]))


def test_timeout_propagated_default_and_custom() -> None:
    t1 = CapturingTransport()
    OpenAICompatClient(_spec(), "m", api_key=None, transport=t1).chat(
        ChatRequest(messages=[ChatMessage("user", "x")])
    )
    assert t1.calls[0]["timeout"] == 60

    t2 = CapturingTransport()
    OpenAICompatClient(_spec(request_timeout_seconds=5), "m", api_key=None, transport=t2).chat(
        ChatRequest(messages=[ChatMessage("user", "x")])
    )
    assert t2.calls[0]["timeout"] == 5


def test_httpx_transport_wraps_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> Any:
            raise ValueError("to nie JSON")

    class FakeClient:
        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def post(self, *a: Any, **k: Any) -> Any:
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", lambda **k: FakeClient())
    with pytest.raises(TransportError):
        HttpxTransport()(PinnedTarget.direct("http://x/v1/chat/completions"), {}, {}, 5)


def test_invalid_message_role_rejected() -> None:
    with pytest.raises(ValueError, match="rola"):
        ChatMessage("human", "x")


def test_mock_client_handles_empty_messages() -> None:
    client = MockClient("m1", ModelSpec(backend="mock", model="t"))
    result = client.chat(ChatRequest(messages=[]))
    assert result.content == "[mock:t] "


def test_build_client_unresolved_api_key_fails_closed() -> None:
    spec = _spec(api_key_ref="env:BRAK_KLUCZA_XYZ")
    with pytest.raises(ModelBackendError, match="sekretu klucza API"):
        build_client(spec, "glm-main", secrets=EnvSecretsProvider(), transport=CapturingTransport())


def test_build_client_strips_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLM_KEY", " secret123\n")
    transport = CapturingTransport()
    client = build_client(
        _spec(api_key_ref="env:GLM_KEY"), "m", secrets=EnvSecretsProvider(), transport=transport
    )
    client.chat(ChatRequest(messages=[ChatMessage("user", "x")]))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret123"


# --------------------------------------------------------------------------
# router.py — clamp kosztów odporny na 'extra'; fallbacki wyłączone
# --------------------------------------------------------------------------


def test_extra_cannot_bypass_max_tokens_clamp(make_config) -> None:
    registry = {
        "glm": {"backend": "openai_compat", "model": "glm", "endpoint": "http://localhost:8000/v1"}
    }
    config = make_config(
        registry=registry, default="glm", cost_controls={"max_tokens_per_request": 100}
    )
    transport = CapturingTransport()
    router = ModelRouter(
        config,
        client_factory=lambda spec, mid: OpenAICompatClient(
            spec, mid, api_key=None, transport=transport
        ),
    )
    req = ChatRequest(
        messages=[ChatMessage("user", "x")], max_tokens=500, extra={"max_tokens": 9999}
    )
    router.complete(req)
    assert transport.calls[0]["payload"]["max_tokens"] == 100  # clamp wygrywa nad extra


def test_fallbacks_disabled_does_not_try_fallback(make_config) -> None:
    registry = {
        "glm": {"backend": "mock", "model": "glm", "fallback": ["hermes"]},
        "hermes": {"backend": "mock", "model": "hermes"},
    }
    config = make_config(registry=registry, default="glm", fallbacks_enabled=False)
    router = ModelRouter(
        config,
        client_factory=lambda spec, mid: FakeClient(mid, error=ModelBackendError(mid, "padło")),
    )
    with pytest.raises(AllModelsFailedError) as exc:
        router.complete(ChatRequest(messages=[ChatMessage("user", "x")]))
    assert "glm" in str(exc.value)
    assert "hermes" not in str(exc.value)


# --------------------------------------------------------------------------
# selection.py — reguły puste/wielokrotne, kolizja tagów
# --------------------------------------------------------------------------

_TAG_REGISTRY = {
    "a": {"backend": "mock", "model": "a", "tags": ["x"]},
    "b": {"backend": "mock", "model": "b", "tags": ["x", "y"]},
    "c": {"backend": "mock", "model": "c", "tags": ["z"]},
}


def test_empty_match_tags_rule_is_skipped(make_config) -> None:
    registry = {"bielik": {"backend": "mock", "model": "bielik", "tags": ["polish"]}}
    rules = [{"match_tags": [], "prefer": ["bielik"]}]
    config = make_config(registry=registry, default="bielik", rules=rules)
    # Zadanie z tagiem 'code' (którego nikt nie ma) nie może przez pustą regułę wciągnąć bielika.
    assert select_candidates(config, tags=["code"]) == ["bielik"]  # tylko default


def test_multiple_matching_rules_accumulate_in_order(make_config) -> None:
    rules = [{"match_tags": ["x"], "prefer": ["a"]}, {"match_tags": ["x", "y"], "prefer": ["b"]}]
    config = make_config(registry=_TAG_REGISTRY, default="a", rules=rules)
    assert select_candidates(config, tags=["x"]) == ["a", "b"]
    assert select_candidates(config, tags=["x", "y"]) == ["a", "b"]


def test_tag_collision_earliest_position_wins(make_config) -> None:
    registry = {
        "A": {"backend": "mock", "model": "A", "tags": ["x"]},
        "B": {"backend": "mock", "model": "B", "tags": ["x"]},
    }
    rules = [{"match_tags": ["x"], "prefer": ["B", "A"]}]
    config = make_config(registry=registry, default="A", rules=rules)
    assert select_candidates(config, tags=["x"]) == ["B", "A"]


# --------------------------------------------------------------------------
# rate_limit.py — cap i ułamkowe uzupełnienie
# --------------------------------------------------------------------------


def test_refill_capped_at_capacity() -> None:
    clock = Clock()
    limiter = RateLimiter(2, now_fn=clock)
    limiter.acquire()
    limiter.acquire()
    clock.advance(600)  # bardzo długo, ale cap = 2
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(RateLimitExceededError):
        limiter.acquire()


def test_fractional_refill_below_one_blocks() -> None:
    clock = Clock()
    limiter = RateLimiter(2, now_fn=clock)
    limiter.acquire()
    limiter.acquire()
    clock.advance(29.9)  # < 1 token przy 2/min
    with pytest.raises(RateLimitExceededError):
        limiter.acquire()


# --------------------------------------------------------------------------
# schema — walidacja granic (>=1)
# --------------------------------------------------------------------------


def test_cost_controls_reject_nonpositive() -> None:
    with pytest.raises(ValidationError):
        CostControls(max_requests_per_minute=0)
    with pytest.raises(ValidationError):
        CostControls(max_tokens_per_request=0)


def test_model_spec_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValidationError):
        ModelSpec(backend="mock", model="x", request_timeout_seconds=0)
