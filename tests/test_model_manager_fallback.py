"""ModelManager's fallback chain, 404 cooldown and per-request logging, driven
with fake clients (no network). The provider clients RETURN LLMResponse(success=
False) on API errors rather than raising, so before this a 404 on the primary
model never reached its fallback at all."""

import pytest

from src.message.types import HumanMessage
from src.model import manager as manager_module
from src.model.manager import ModelManager
from src.model.types import LLMExtra, LLMResponse, ModelConfig

SECRET = "sk-or-v1-THIS-MUST-NEVER-APPEAR-IN-ANY-LOG-LINE"


class _Recorder:
    def __init__(self):
        self.lines = []

    def _add(self, level, msg):
        self.lines.append((level, str(msg)))

    def info(self, msg, *a, **k):
        self._add("info", msg)

    def warning(self, msg, *a, **k):
        self._add("warning", msg)

    def error(self, msg, *a, **k):
        self._add("error", msg)

    def text(self, level=None):
        return "\n".join(m for lv, m in self.lines if level in (None, lv))


class _FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def __call__(self, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _ok(text="ok"):
    return LLMResponse(success=True, message=text)


def _http_404(message="API status error: Error code: 404 - {'error': {'message': 'No endpoints found for google/x'}}"):
    return LLMResponse(success=False, message=message, extra=LLMExtra(data={"status_code": 404}))


@pytest.fixture()
def log(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(manager_module, "logger", rec)
    return rec


def _register(manager, name, outcome, fallback=None, model_id=None, provider="openrouter"):
    manager.models[name] = ModelConfig(
        model_name=name, model_type="chat/completions", model_id=model_id or name, provider=provider,
        api_key=SECRET, fallback_model=fallback,
    )
    client = _FakeClient(outcome)
    manager.model_clients[name] = client
    return client


def _msgs():
    return [HumanMessage(content="hi")]


@pytest.mark.asyncio
async def test_logs_which_model_answered(log):
    m = ModelManager()
    _register(m, "A", _ok())
    result = await m("A", _msgs())
    assert result.success
    assert "Model answered: A" in log.text("info")


@pytest.mark.asyncio
async def test_404_response_falls_back_warns_and_logs_the_answering_model(log):
    m = ModelManager()
    a = _register(m, "A", _http_404(), fallback="B")
    b = _register(m, "B", _ok("from B"))
    result = await m("A", _msgs())
    assert result.success and result.message == "from B"
    assert (a.calls, b.calls) == (1, 1)
    warnings = log.text("warning")
    assert "A returned 404/NOT_FOUND" in warnings and "skipping it for 60 min" in warnings
    assert "Model answered: B" in log.text("info") and "requested A" in log.text("info")


@pytest.mark.asyncio
async def test_a_404_model_is_skipped_on_the_next_request_not_retried(log):
    m = ModelManager()
    a = _register(m, "A", _http_404(), fallback="B")
    b = _register(m, "B", _ok())
    await m("A", _msgs())
    await m("A", _msgs())
    await m("A", _msgs())
    assert a.calls == 1, "the 404 model must not be hit again within the cooldown"
    assert b.calls == 3


@pytest.mark.asyncio
async def test_the_model_is_tried_again_after_the_cooldown(log, monkeypatch):
    m = ModelManager()
    a = _register(m, "A", _http_404(), fallback="B")
    _register(m, "B", _ok())
    clock = {"now": 1000.0}
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: clock["now"])
    await m("A", _msgs())
    clock["now"] += 3599
    await m("A", _msgs())
    assert a.calls == 1
    clock["now"] += 2  # past the hour
    await m("A", _msgs())
    assert a.calls == 2


@pytest.mark.asyncio
async def test_the_plugins_twin_shares_the_cooldown_of_the_same_remote_model(log):
    # 'X' and 'X-plugins' are one remote model; a 404 on one must skip both.
    m = ModelManager()
    base = _register(m, "X", _http_404(), fallback="Y", model_id="google/x")
    twin = _register(m, "X-plugins", _http_404(), fallback="Y-plugins", model_id="google/x")
    _register(m, "Y", _ok(), model_id="google/y")
    _register(m, "Y-plugins", _ok(), model_id="google/y")
    await m("X", _msgs())
    await m("X-plugins", _msgs())
    assert base.calls == 1 and twin.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        _http_404("API error: 404 models/gemini-x is not found for API version v1beta"),
        LLMResponse(success=False, message="API status error: google/x is not a valid model ID"),
        LLMResponse(success=False, message="No endpoints found for google/x."),
        LLMResponse(success=False, message="boom", extra=LLMExtra(data={"status_code": 404})),
        Exception("404 NOT_FOUND: models/gemini-x is not found"),
    ],
)
async def test_every_not_found_shape_is_recognised(log, outcome):
    m = ModelManager()
    a = _register(m, "A", outcome, fallback="B")
    _register(m, "B", _ok())
    result = await m("A", _msgs())
    assert result.success
    await m("A", _msgs())
    assert a.calls == 1


@pytest.mark.asyncio
async def test_a_rate_limit_response_is_returned_as_is_and_not_cooled_down(log):
    # Only 404/NOT_FOUND moves on and starts a cooldown; other error responses behave as before.
    m = ModelManager()
    limited = LLMResponse(success=False, message="Rate limit error: slow down", extra=LLMExtra(data={"status_code": 429}))
    a = _register(m, "A", limited, fallback="B")
    b = _register(m, "B", _ok())
    result = await m("A", _msgs())
    assert result is limited and b.calls == 0
    await m("A", _msgs())
    assert a.calls == 2
    assert "returned an error response" in log.text("warning")


@pytest.mark.asyncio
async def test_a_non_404_exception_still_falls_back_but_is_not_cooled_down(log):
    m = ModelManager()
    a = _register(m, "A", RuntimeError("connection reset"), fallback="B")
    b = _register(m, "B", _ok())
    assert (await m("A", _msgs())).success
    await m("A", _msgs())
    assert a.calls == 2 and b.calls == 2


@pytest.mark.asyncio
async def test_when_every_model_is_in_cooldown_it_fails_fast_without_calling_any(log):
    m = ModelManager()
    a = _register(m, "A", _http_404(), fallback="B")
    b = _register(m, "B", _http_404())
    first = await m("A", _msgs())
    assert not first.success
    second = await m("A", _msgs())
    assert not second.success and "No available model" in second.message
    assert (a.calls, b.calls) == (1, 1)


@pytest.mark.asyncio
async def test_a_single_failing_model_keeps_returning_its_own_error_message(log):
    m = ModelManager()
    _register(m, "A", ValueError("bad request"))
    result = await m("A", _msgs())
    assert not result.success and result.message == "bad request"


@pytest.mark.asyncio
async def test_a_fallback_cycle_cannot_loop_forever(log):
    m = ModelManager()
    a = _register(m, "A", RuntimeError("x"), fallback="B")
    b = _register(m, "B", RuntimeError("y"), fallback="A")
    result = await m("A", _msgs())
    assert not result.success and (a.calls, b.calls) == (1, 1)


@pytest.mark.asyncio
async def test_a_retired_name_is_routed_to_its_replacement_with_a_warning(log, monkeypatch):
    m = ModelManager()
    new = _register(m, "NEW", _ok())
    monkeypatch.setitem(manager_module.LEGACY_ALIASES, "OLD", "NEW")
    assert (await m("OLD", _msgs())).success
    await m("OLD", _msgs())
    assert new.calls == 2
    assert log.text("warning").count("Model OLD is retired") == 1, "warn once, not on every request"


@pytest.mark.asyncio
async def test_unknown_model_still_reports_not_found(log):
    m = ModelManager()
    result = await m("does/not-exist", _msgs())
    assert not result.success and "not found" in result.message


@pytest.mark.asyncio
async def test_no_api_key_ever_appears_in_a_log_line(log):
    m = ModelManager()
    _register(m, "A", _http_404(), fallback="B")
    _register(m, "B", _ok())
    await m("A", _msgs())
    await m("A", _msgs())
    assert SECRET not in log.text()
