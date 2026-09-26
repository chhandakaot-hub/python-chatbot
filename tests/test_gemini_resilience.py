"""Retry and timeout around the model call.

Seen for real: one question in a run of ~45 failed with a 503 from Google and
was never retried, and another took ~5 minutes while every portal lookup in it
finished in under 0.3s. Both must now be handled without a caller seeing them.
"""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from google.genai import errors

from app.services import gemini_service


def _api_error(code: int):
    body = {"error": {"code": code, "message": "boom", "status": "X"}}
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, body)


class _Reply:
    """Just enough of a GenerateContentResponse for generate_reply()."""

    def __init__(self, text="all done"):
        self.text = text
        self.candidates = [
            SimpleNamespace(
                finish_reason="STOP",
                content=SimpleNamespace(parts=[SimpleNamespace(function_call=None, text=text)]),
            )
        ]


def _client(script):
    """A fake client whose n-th call runs script[n]: an exception to raise, an
    awaitable-returning callable, or a reply to return."""
    calls = []

    async def generate_content(model, contents, config):
        step = script[min(len(calls), len(script) - 1)]
        calls.append(step)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return await step()
        return step

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    client.calls = calls
    return client


@pytest.fixture(autouse=True)
def fast_settings(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(gemini_service.settings, "AI_MAX_RETRIES", 2)
    monkeypatch.setattr(gemini_service.settings, "AI_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(gemini_service.settings, "AI_TOOLS_ENABLED", False)


def _reply_with(monkeypatch, client):
    monkeypatch.setattr(gemini_service, "get_client", lambda: client)
    return asyncio.run(gemini_service.generate_reply([], "hi"))


def test_a_transient_503_is_retried_and_the_caller_never_sees_it(monkeypatch):
    client = _client([_api_error(503), _api_error(503), _Reply("recovered")])
    assert _reply_with(monkeypatch, client) == "recovered"
    assert len(client.calls) == 3


def test_a_429_rate_limit_is_retried(monkeypatch):
    client = _client([_api_error(429), _Reply("ok")])
    assert _reply_with(monkeypatch, client) == "ok"
    assert len(client.calls) == 2


def test_retries_are_bounded_then_the_error_surfaces_as_502(monkeypatch):
    client = _client([_api_error(503)])
    with pytest.raises(HTTPException) as exc:
        _reply_with(monkeypatch, client)
    assert exc.value.status_code == 502
    assert len(client.calls) == 3  # 1 try + AI_MAX_RETRIES


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_a_request_that_can_never_succeed_is_not_retried(monkeypatch, code):
    client = _client([_api_error(code), _Reply("never reached")])
    with pytest.raises(HTTPException) as exc:
        _reply_with(monkeypatch, client)
    assert exc.value.status_code == 502
    assert len(client.calls) == 1


def test_zero_retries_means_one_attempt(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_MAX_RETRIES", 0)
    client = _client([_api_error(503), _Reply("unused")])
    with pytest.raises(HTTPException):
        _reply_with(monkeypatch, client)
    assert len(client.calls) == 1


async def _hang():
    await asyncio.sleep(3600)


def test_a_hung_call_times_out_with_504_after_retrying(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_TIMEOUT_SECONDS", 0.05)
    client = _client([_hang])
    with pytest.raises(HTTPException) as exc:
        _reply_with(monkeypatch, client)
    assert exc.value.status_code == 504
    assert "0.05s" in exc.value.detail and "3 attempt" in exc.value.detail
    assert len(client.calls) == 3


def test_a_hung_call_that_recovers_on_retry_succeeds(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_TIMEOUT_SECONDS", 0.05)

    async def _fast():
        return _Reply("second try worked")

    client = _client([_hang, _fast])
    assert _reply_with(monkeypatch, client) == "second try worked"
    assert len(client.calls) == 2


def test_timeout_of_zero_disables_the_cap(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_TIMEOUT_SECONDS", 0)

    async def _slowish():
        await asyncio.sleep(0.05)
        return _Reply("patient")

    assert _reply_with(monkeypatch, _client([_slowish])) == "patient"


def test_backoff_doubles_between_attempts(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "AI_RETRY_BACKOFF_SECONDS", 1.0)
    waits: list[float] = []

    async def _record(seconds):
        waits.append(seconds)

    monkeypatch.setattr(gemini_service.asyncio, "sleep", _record)
    client = _client([_api_error(503), _api_error(503), _Reply("ok")])
    assert _reply_with(monkeypatch, client) == "ok"
    assert waits == [1.0, 2.0]


def test_the_fallback_call_after_too_many_tool_rounds_is_protected_too(monkeypatch):
    """The final tools-off turn goes through the same wrapper, so a 503 there
    does not throw away a whole lookup."""
    monkeypatch.setattr(gemini_service.settings, "AI_TOOLS_ENABLED", True)
    monkeypatch.setattr(gemini_service, "MAX_TOOL_ROUNDS", 1)
    monkeypatch.setattr(gemini_service.ai_tools, "dispatch", lambda name, args: {"count": 1})

    tool_turn = _Reply("")
    tool_turn.candidates[0].content.parts = [
        SimpleNamespace(function_call=SimpleNamespace(name="count_students", args={}), text=None)
    ]
    # 1st: asks for a tool. 2nd (fallback): 503. 3rd (its retry): the answer.
    client = _client([tool_turn, _api_error(503), _Reply("from the results")])
    assert _reply_with(monkeypatch, client) == "from the results"
    assert len(client.calls) == 3
