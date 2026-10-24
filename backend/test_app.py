"""Tests. Run with: MOCK_MODE=true uv run pytest
Claude is replaced by a scripted fake model so tests need no API keys."""
import asyncio
import os
import uuid
from datetime import date, timedelta

os.environ["MOCK_MODE"] = "true"

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

import graph
from tools import calculate_budget, get_weather

PROMPTS_SEEN = []  # system prompts the fake model received


def call(name, args):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": uuid.uuid4().hex}])


class FakeClaude(BaseChatModel):
    """Plays each agent's part based on its system prompt."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system, task = messages[0].content, messages[1].content
        used_tools = [m for m in messages if m.type == "tool"]
        PROMPTS_SEEN.append(system)
        if "Orchestrator" in system:
            if "vegetarian" in task and not used_tools:
                reply = call("save_preference", {"text": "I'm vegetarian"})
            else:
                reply = call("EventRequest", {
                    "title": "Birthday dinner", "date": "2026-10-31", "start_time": "19:00",
                    "location": "Dallas", "guest_count": 8, "guest_emails": ["sam@gmail.com"],
                    "budget": 200 if "cheaper" in task else 400})
        elif "Venue Scout" in system:
            reply = call("VenueList", {"venues": [{"name": f"Venue {i}", "address": "Main St",
                         "price_level": "$$", "why_it_fits": "fits"} for i in range(3)]})
        elif "Logistics" in system:
            reply = call("check_free_busy", {"start": "2026-10-31T19:00:00-05:00",
                                             "end": "2026-10-31T22:00:00-05:00"}) \
                if not used_tools else call("LogisticsReport", {
                    "weather": "Clear", "available": True, "total_budget": 400,
                    "per_person": 50, "warnings": []})
        else:  # Invites
            reply = call("create_gmail_draft", {"to": ["sam@gmail.com"], "subject": "Hi",
                                                "body": "Join us"}) \
                if not used_tools else call("InviteDraft", {
                    "to": ["sam@gmail.com"], "subject": "Hi", "body": "Join us",
                    "start_iso": "2026-10-31T19:00:00-05:00",
                    "end_iso": "2026-10-31T22:00:00-05:00", "location": "Venue 0"})
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools, **kwargs):
        return self

    @property
    def _llm_type(self):
        return "fake-claude"


async def load_mcp_tools():
    return await graph.mcp_client().get_tools()


def test_budget_math():
    assert calculate_budget.invoke({"total": 400, "guests": 8}) == {
        "total": 400, "food": 280, "tip": 80, "misc": 40, "per_person": 50}


def test_weather_too_far_out():
    far = (date.today() + timedelta(days=60)).isoformat()
    assert get_weather.invoke({"city": "Dallas", "date_iso": far}) == "forecast not available yet"


def test_no_agent_can_send():
    tools = graph.agent_tools(asyncio.run(load_mcp_tools()))
    names = {t.name for agent in tools.values() for t in agent}
    assert not names & {"send_draft", "create_event", "delete_draft"}
    assert {t.name for t in tools["invites"]} == {"create_gmail_draft"}


def test_full_mock_run(monkeypatch):
    monkeypatch.setattr(graph, "ChatAnthropic", lambda **kw: FakeClaude())

    deleted = []

    @tool
    def delete_draft(draft_id: str) -> dict:
        """Spy that records which drafts get deleted."""
        deleted.append(draft_id)
        return {"deleted": True}

    async def run():
        mcp_tools = [t for t in await load_mcp_tools() if t.name != "delete_draft"] + [delete_draft]
        app = graph.build_graph(mcp_tools, InMemorySaver(), InMemoryStore())

        async def stream(inp, thread):
            return [e async for e in app.astream(inp, graph.run_config(thread),
                                                 stream_mode="custom")]

        # 1. First request: all agents run, then it pauses for approval.
        events = await stream({"messages": [("user", "Dinner for 8, remember I'm vegetarian")]}, "a")
        state = await app.aget_state(graph.run_config("a"))
        assert state.values["status"] == "waiting_approval" and state.interrupts
        assert state.values["draft_id"].startswith("mock-draft-")
        assert not any(e["event"] == "shared" for e in events)  # nothing sent before Share
        tool_calls = {(e["agent"], e["tool"], e["kind"]) for e in events if e["event"] == "tool_call"}
        assert ("logistics", "check_free_busy", "mcp") in tool_calls
        assert ("orchestrator", "save_preference", "tool") in tool_calls

        # 2. Follow-up on the same thread reruns the plan and deletes the outdated draft.
        first_draft = state.values["draft_id"]
        await stream({"messages": [("user", "make it cheaper")]}, "a")
        state = await app.aget_state(graph.run_config("a"))
        assert state.values["request"]["budget"] == 200 and state.interrupts
        assert deleted == [first_draft] and state.values["draft_id"] != first_draft

        # 3. A new thread sees the saved preference.
        PROMPTS_SEEN.clear()
        await stream({"messages": [("user", "Lunch for 4")]}, "b")
        assert any("vegetarian" in p for p in PROMPTS_SEEN if "Venue Scout" in p)

        # 4. Share sends the email and creates the event (mock).
        events = await stream(Command(resume="share"), "a")
        shared = [e for e in events if e["event"] == "shared"]
        assert shared and shared[0]["result"]["email"]
        assert (await app.aget_state(graph.run_config("a"))).values["status"] == "shared"

        # 5. Cancel on the other thread sends nothing and deletes its draft.
        b_draft = (await app.aget_state(graph.run_config("b"))).values["draft_id"]
        events = await stream(Command(resume="cancel"), "b")
        assert deleted[-1] == b_draft
        assert not any(e["event"] == "shared" for e in events)
        assert (await app.aget_state(graph.run_config("b"))).values["status"] == "cancelled"

    asyncio.run(run())


def test_api_needs_password(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "APP_PASSWORD", "secret")
    with TestClient(main.app) as client:
        assert client.get("/health").json()["mock_mode"] is True
        assert client.get("/api/thread/x").status_code == 401
        assert client.get("/api/thread/x", headers={"x-app-password": "wrong"}).status_code == 401
        assert client.get("/api/thread/x", headers={"x-app-password": "secret"}).status_code == 200


def test_sse_plan_then_share(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "APP_PASSWORD", "secret")
    monkeypatch.setattr(graph, "ChatAnthropic", lambda **kw: FakeClaude())
    headers = {"x-app-password": "secret"}
    with TestClient(main.app) as client:
        text = client.post("/api/plan", json={"thread_id": "t", "message": "Dinner for 8"},
                           headers=headers).text
        assert "event: approval_required" in text and "event: shared" not in text
        assert text.rstrip().endswith('event: done\ndata: {}')
        text = client.post("/api/approve", json={"thread_id": "t", "action": "share"},
                           headers=headers).text
        assert "event: shared" in text
        assert client.get("/api/thread/t", headers=headers).json()["status"] == "shared"


def test_google_login_is_protected(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "APP_PASSWORD", "secret")
    with TestClient(main.app) as client:
        assert client.get("/auth/google").status_code == 401
        url = client.get("/auth/google", headers={"x-app-password": "secret"}).json()["url"]
        assert "access_type=offline" in url and "state=" in url
        # A callback without our signed state is rejected before talking to Google.
        assert client.get("/auth/google/callback?code=x&state=123.forged").status_code == 400
        assert client.get("/auth/google/callback?code=x").status_code == 400
