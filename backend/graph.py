"""The agents, the graph that runs them, and the approval pause before sharing."""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, TypedDict

from langchain.agents import create_agent
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.config import get_store, get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt
from langsmith import traceable

from models import EventRequest, InviteDraft, LogisticsReport, VenueList
from prompts import INVITES_PROMPT, LOGISTICS_PROMPT, ORCHESTRATOR_PROMPT, VENUE_PROMPT
from tools import PREFS_NAMESPACE, calculate_budget, get_weather, save_preference, web_search

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MCP_TOOL_NAMES = {"check_free_busy", "create_gmail_draft", "send_draft", "create_event", "delete_draft"}


class EventState(TypedDict):
    # Agent outputs are stored as plain dicts (model_dump()) so checkpoints stay simple JSON.
    messages: Annotated[list, add_messages]
    request: dict | None    # EventRequest
    venues: dict | None     # VenueList
    logistics: dict | None  # LogisticsReport
    invite: dict | None     # InviteDraft
    draft_id: str | None
    status: str             # "planning" | "waiting_approval" | "shared" | "cancelled"


def mcp_client() -> MultiServerMCPClient:
    """Connect to google_mcp.py as a stdio subprocess (it inherits our env, e.g. MOCK_MODE)."""
    return MultiServerMCPClient({"google": {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(Path(__file__).parent / "google_mcp.py")],
        "env": dict(os.environ),
    }})


def agent_tools(mcp_tools: list) -> dict[str, list]:
    """Which tools each agent gets. send_draft, create_event and delete_draft are never given to an agent."""
    mcp = {t.name: t for t in mcp_tools}
    return {
        "orchestrator": [save_preference],
        "venue_scout": [web_search],
        "logistics": [get_weather, calculate_budget, mcp["check_free_busy"]],
        "invites": [mcp["create_gmail_draft"]],
    }


def emit(event: str, **data) -> None:
    """Send a custom event to whoever is streaming the graph (the SSE route)."""
    get_stream_writer()({"event": event, **data})


async def load_memory() -> str:
    items = await get_store().asearch(PREFS_NAMESPACE, limit=50)
    return "\n".join(f"- {item.value['text']}" for item in items) or "none saved"


async def run_agent(name: str, prompt: str, tools: list, response_format, task: str) -> dict:
    """Run one tool-calling sub-agent and return its final state.
    Tool calls are reported live as `tool_call` events."""
    agent = create_agent(ChatAnthropic(model=MODEL), tools, system_prompt=prompt,
                         response_format=response_format, name=name)
    tool_names = {t.name for t in tools}
    final = {"messages": []}
    async for update in agent.astream({"messages": [("user", task)]}, stream_mode="updates"):
        for node_output in update.values():
            for out in node_output if isinstance(node_output, list) else [node_output]:
                if not isinstance(out, dict):
                    continue
                for msg in out.get("messages", []):
                    final["messages"].append(msg)
                    for call in getattr(msg, "tool_calls", None) or []:
                        if call["name"] in tool_names:
                            emit("tool_call", agent=name, tool=call["name"],
                                 kind="mcp" if call["name"] in MCP_TOOL_NAMES else "tool")
                if "structured_response" in out:
                    final["structured_response"] = out["structured_response"]
    return final


def find_draft_id(messages: list) -> str | None:
    """Pull the draft_id out of the create_gmail_draft tool result."""
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage) and msg.name == "create_gmail_draft":
            text = msg.content if isinstance(msg.content, str) else "".join(
                block.get("text", "") for block in msg.content if isinstance(block, dict))
            try:
                return json.loads(text)["draft_id"]
            except (ValueError, KeyError):
                return None
    return None


async def discard_old_draft(state: EventState, mcp: dict) -> None:
    """Delete the unsent draft from an earlier plan so Gmail Drafts doesn't fill up."""
    if state.get("draft_id") and state.get("status") != "shared":
        try:
            await mcp["delete_draft"].ainvoke({"draft_id": state["draft_id"]})
        except Exception:
            pass  # already deleted by hand in Gmail: nothing to clean up


@traceable(name="share")
async def share_invite(invite: dict, draft_id: str, mcp: dict) -> dict:
    """The only place email is sent and the calendar event is created."""
    email = await mcp["send_draft"].ainvoke({"draft_id": draft_id})
    event = await mcp["create_event"].ainvoke({
        "summary": invite["subject"], "start_iso": invite["start_iso"], "end_iso": invite["end_iso"],
        "location": invite["location"], "attendees": invite["to"], "description": invite["body"],
    })
    return {"email": email, "event": event}


def build_graph(mcp_tools: list, checkpointer=None, store=None):
    tools = agent_tools(mcp_tools)
    mcp = {t.name: t for t in mcp_tools}

    async def orchestrator(state: EventState):
        emit("agent_status", agent="orchestrator", status="working")
        await discard_old_draft(state, mcp)
        today = date.today().strftime("%A, %Y-%m-%d")
        prompt = ORCHESTRATOR_PROMPT.format(today=today, memory=await load_memory())
        prompt += f"\n<current_request>{json.dumps(state.get('request'))}</current_request>"
        history = "\n".join(f"- {m.content}" for m in state["messages"] if m.type == "human")
        result = await run_agent("orchestrator", prompt, tools["orchestrator"], EventRequest,
                                 f"Conversation so far (latest last):\n{history}")
        request = result["structured_response"].model_dump()
        emit("agent_status", agent="orchestrator", status="done")
        emit("result", kind="request", data=request)
        return {"request": request, "status": "planning", "venues": None,
                "logistics": None, "invite": None, "draft_id": None}

    async def venue_scout(state: EventState):
        emit("agent_status", agent="venue_scout", status="working")
        prompt = VENUE_PROMPT.format(memory=await load_memory())
        result = await run_agent("venue_scout", prompt, tools["venue_scout"], VenueList,
                                 f"Find 3 venues for this event: {json.dumps(state['request'])}")
        venues = result["structured_response"].model_dump()
        emit("agent_status", agent="venue_scout", status="done")
        emit("result", kind="venues", data=venues)
        return {"venues": venues}

    async def logistics(state: EventState):
        emit("agent_status", agent="logistics", status="working")
        req = state["request"]
        start = datetime.fromisoformat(f"{req['date']}T{req['start_time']}")
        task = (f"Event: {json.dumps(req)}\n"
                f"Check weather in {req['location']} on {req['date']}, calendar availability "
                f"from {start.isoformat()} to {(start + timedelta(hours=3)).isoformat()} "
                f"(add the UTC offset for {req['location']}), and the budget for "
                f"{req['guest_count']} guests (if no budget, assume $50 per person).")
        result = await run_agent("logistics", LOGISTICS_PROMPT, tools["logistics"],
                                 LogisticsReport, task)
        report = result["structured_response"].model_dump()
        emit("agent_status", agent="logistics", status="done")
        emit("result", kind="logistics", data=report)
        return {"logistics": report}

    async def invites(state: EventState):
        emit("agent_status", agent="invites", status="working")
        prompt = INVITES_PROMPT.format(memory=await load_memory())
        task = (f"Event: {json.dumps(state['request'])}\n"
                f"Use the first venue: {json.dumps(state['venues']['venues'][:1])}\n"
                f"Logistics: {json.dumps(state['logistics'])}\n"
                f"Latest user message: {state['messages'][-1].content}\n"
                "Save the draft with create_gmail_draft, then return the InviteDraft. "
                "Use ISO times with the UTC offset for the event's city; default length 3 hours.")
        result = await run_agent("invites", prompt, tools["invites"], InviteDraft, task)
        invite = result["structured_response"].model_dump()
        emit("agent_status", agent="invites", status="done")
        emit("result", kind="invite", data=invite)
        return {"invite": invite, "draft_id": find_draft_id(result["messages"]),
                "status": "waiting_approval"}

    def approval(state: EventState):
        # Pauses the run here. Resume with Command(resume="share") or Command(resume="cancel").
        decision = interrupt({"invite": state["invite"], "draft_id": state["draft_id"]})
        return Command(goto="share" if decision == "share" else "cancel")

    async def share(state: EventState):
        if not state.get("draft_id"):
            raise RuntimeError("No Gmail draft was saved, so there is nothing to send.")
        result = await share_invite(state["invite"], state["draft_id"], mcp)
        emit("shared", result=result)
        return {"status": "shared"}

    async def cancel(state: EventState):
        await discard_old_draft(state, mcp)
        return {"status": "cancelled", "draft_id": None}

    builder = StateGraph(EventState)
    builder.add_node("orchestrator", orchestrator)
    builder.add_node("venue_scout", venue_scout)
    builder.add_node("logistics", logistics)
    builder.add_node("invites", invites)
    builder.add_node("approval", approval, destinations=("share", "cancel"))
    builder.add_node("share", share)
    builder.add_node("cancel", cancel)
    builder.add_edge(START, "orchestrator")
    builder.add_edge("orchestrator", "venue_scout")  # these two run in parallel
    builder.add_edge("orchestrator", "logistics")
    builder.add_edge(["venue_scout", "logistics"], "invites")  # waits for both
    builder.add_edge("invites", "approval")
    builder.add_edge("share", END)
    builder.add_edge("cancel", END)
    return builder.compile(checkpointer=checkpointer, store=store)


def run_config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "event-planner",
        "tags": ["mock" if MOCK_MODE else "live"],
        "metadata": {"thread_id": thread_id},
    }
