import asyncio
from types import SimpleNamespace

import pytest

from app.models.message import Message, Role
from app.services import conversation_service, gemini_service


@pytest.fixture
def fake_gemini(monkeypatch):
    """Replace the network call so tests never touch Google's API."""
    calls = []

    async def _generate_reply(history, prompt):
        # Record the contents, not the ORM objects: those detach once the
        # request's session closes.
        calls.append(([m.content for m in history], prompt))
        return "a reply from the model"

    monkeypatch.setattr(gemini_service, "generate_reply", _generate_reply)
    return calls


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #
def test_build_contents_maps_roles_for_gemini():
    history = [
        Message(role=Role.system, content="skipped"),
        Message(role=Role.user, content="hi"),
        Message(role=Role.assistant, content="hello"),
    ]
    contents = gemini_service.build_contents(history, "how are you?")
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert [c.parts[0].text for c in contents] == ["hi", "hello", "how are you?"]


def test_build_contents_respects_history_limit(monkeypatch):
    monkeypatch.setattr(gemini_service.settings, "HISTORY_LIMIT", 2)
    history = [Message(role=Role.user, content=str(i)) for i in range(10)]
    contents = gemini_service.build_contents(history, "latest")
    assert [c.parts[0].text for c in contents] == ["8", "9", "latest"]


def test_suggest_title_truncates_long_prompts():
    assert conversation_service.suggest_title("  hello   world  ") == "hello world"
    long_title = conversation_service.suggest_title("x" * 200)
    assert len(long_title) == 60 and long_title.endswith("…")


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
def test_chat_requires_auth(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_chat_starts_a_conversation(client, auth_headers, fake_gemini):
    response = client.post("/api/chat", json={"message": "hi there"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["reply"]["content"] == "a reply from the model"
    assert body["reply"]["role"] == "assistant"

    detail = client.get(
        f"/api/conversations/{body['conversation_id']}", headers=auth_headers
    ).json()
    assert detail["title"] == "hi there"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_chat_continues_a_conversation_with_history(client, auth_headers, fake_gemini):
    first = client.post("/api/chat", json={"message": "one"}, headers=auth_headers).json()
    client.post(
        "/api/chat",
        json={"message": "two", "conversation_id": first["conversation_id"]},
        headers=auth_headers,
    )
    # The second call must have been given the first exchange as context.
    history, prompt = fake_gemini[-1]
    assert prompt == "two"
    assert history == ["one", "a reply from the model"]

    detail = client.get(
        f"/api/conversations/{first['conversation_id']}", headers=auth_headers
    ).json()
    assert len(detail["messages"]) == 4


def test_empty_message_is_rejected(client, auth_headers):
    assert client.post("/api/chat", json={"message": ""}, headers=auth_headers).status_code == 422


def test_chat_on_unknown_conversation(client, auth_headers, fake_gemini):
    response = client.post(
        "/api/chat", json={"message": "hi", "conversation_id": 999}, headers=auth_headers
    )
    assert response.status_code == 404


def test_provider_failure_leaves_no_conversation(client, auth_headers, monkeypatch):
    from fastapi import HTTPException

    async def _boom(history, prompt):
        raise HTTPException(status_code=502, detail="AI provider error")

    monkeypatch.setattr(gemini_service, "generate_reply", _boom)
    assert client.post("/api/chat", json={"message": "hi"}, headers=auth_headers).status_code == 502
    assert client.get("/api/conversations", headers=auth_headers).json() == []


def test_conversations_are_private_to_their_owner(client, auth_headers, fake_gemini):
    mine = client.post("/api/chat", json={"message": "secret"}, headers=auth_headers).json()

    other = {"email": "other@example.com", "password": "supersecret1"}
    client.post("/api/auth/register", json=other)
    token = client.post("/api/auth/login", json=other).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    cid = mine["conversation_id"]
    assert client.get(f"/api/conversations/{cid}", headers=headers).status_code == 404
    assert client.delete(f"/api/conversations/{cid}", headers=headers).status_code == 404
    assert client.get("/api/conversations", headers=headers).json() == []


def test_delete_conversation(client, auth_headers, fake_gemini):
    cid = client.post("/api/chat", json={"message": "hi"}, headers=auth_headers).json()[
        "conversation_id"
    ]
    assert client.delete(f"/api/conversations/{cid}", headers=auth_headers).status_code == 204
    assert client.get(f"/api/conversations/{cid}", headers=auth_headers).status_code == 404


# --------------------------------------------------------------------------- #
# The tool loop
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """Either a tool request or a text answer, shaped like the SDK's response."""

    def __init__(
        self, tool_name: str | None = None, text: str | None = None, finish_reason=None
    ):
        self.text = text
        part = SimpleNamespace(
            function_call=SimpleNamespace(name=tool_name, args={}) if tool_name else None
        )
        self.candidates = [
            SimpleNamespace(
                content=SimpleNamespace(parts=[part], role="model"),
                finish_reason=finish_reason,
            )
        ]


def _client_answering_after(rounds: int, calls: list):
    """A stand-in Gemini that asks for a tool `rounds` times, then replies."""

    async def _generate_content(*, model, contents, config):
        calls.append(bool(config.tools))
        if len([c for c in calls if c]) <= rounds and config.tools:
            return _FakeResponse(tool_name="count_students")
        return _FakeResponse(text="done")

    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(
        generate_content=_generate_content
    )))


def test_a_lookup_that_needs_several_rounds_still_answers(monkeypatch):
    """Four chained tool calls is an honest lookup, not a runaway loop:
    "find the student, fetch completion, open the enrollment, then the result"
    is exactly what one question about a student costs."""
    calls: list = []
    monkeypatch.setattr(gemini_service, "get_client", lambda: _client_answering_after(4, calls))
    monkeypatch.setattr(gemini_service.ai_tools, "dispatch", lambda name, args: {"count": 1})

    reply = asyncio.run(gemini_service.generate_reply([], "how is this student doing?"))
    assert reply == "done"


def test_running_out_of_rounds_answers_from_what_it_has(monkeypatch):
    """Rather than throwing the lookup away with a 502, the last turn drops the
    tools so the model has to reply in words from the results it already got."""
    calls: list = []
    # Never stops asking for tools while any are offered.
    monkeypatch.setattr(
        gemini_service, "get_client", lambda: _client_answering_after(99, calls)
    )
    monkeypatch.setattr(gemini_service.ai_tools, "dispatch", lambda name, args: {"count": 1})

    reply = asyncio.run(gemini_service.generate_reply([], "something very involved"))
    assert reply == "done"
    # The final turn is the one made with tools removed.
    assert calls[-1] is False
    assert calls.count(True) == gemini_service.MAX_TOOL_ROUNDS


def test_tool_rounds_allow_a_four_step_chain():
    """N rounds of tools needs N+1 model turns, so the cap must exceed the
    deepest real chain -- four -- or that lookup can never finish."""
    assert gemini_service.MAX_TOOL_ROUNDS >= 5


def test_a_truncated_reply_says_so(monkeypatch):
    """A reply cut off at the token limit stops mid-sentence but still reads as
    finished; someone acting on a half-printed table needs to be told."""

    async def _generate_content(*, model, contents, config):
        return _FakeResponse(text="| a | b |", finish_reason="FinishReason.MAX_TOKENS")

    monkeypatch.setattr(
        gemini_service,
        "get_client",
        lambda: SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))
        ),
    )
    reply = asyncio.run(gemini_service.generate_reply([], "a long question"))
    assert "cut short" in reply


def test_a_complete_reply_is_left_alone(monkeypatch):
    async def _generate_content(*, model, contents, config):
        return _FakeResponse(text="all done", finish_reason="FinishReason.STOP")

    monkeypatch.setattr(
        gemini_service,
        "get_client",
        lambda: SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=_generate_content))
        ),
    )
    assert asyncio.run(gemini_service.generate_reply([], "hi")) == "all done"


def test_the_token_budget_leaves_room_for_thinking():
    """Gemini spends this budget on reasoning as well as the answer -- measured
    at ~280 thinking tokens for a trivial table, so 1024 truncated real ones."""
    from app.core.config import settings

    assert settings.AI_MAX_TOKENS >= 4096


def test_the_fallback_turn_forbids_further_tool_calls():
    """Removing `tools` is not enough: with a transcript full of function calls
    behind it the model emits one anyway, and `response.text` comes back empty.
    NONE refuses the call at the API."""
    from google.genai import types

    config = gemini_service._build_config(use_tools=False)
    assert config.tools is None
    mode = config.tool_config.function_calling_config.mode
    assert mode == types.FunctionCallingConfigMode.NONE


def test_the_normal_turn_still_offers_tools():
    config = gemini_service._build_config(use_tools=True)
    assert config.tools
    assert config.tool_config is None


def test_one_gemini_client_per_event_loop():
    """A client cached across loops raises "Event loop is closed" on the second
    request. Same loop must reuse it; a new loop must not."""
    import asyncio as _asyncio

    async def _get():
        return gemini_service.get_client()

    loop_a_first = _asyncio.run(_get())
    loop_a_second = _asyncio.run(_get())
    # Different asyncio.run() calls mean different loops, so different clients.
    assert loop_a_first is not loop_a_second

    async def _twice():
        return gemini_service.get_client(), gemini_service.get_client()

    one, two = _asyncio.run(_twice())
    assert one is two
