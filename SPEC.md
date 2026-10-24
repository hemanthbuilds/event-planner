# Event Planner — Simple Multi-Agent POC

> **For Claude Code:** Read this file, then build the whole project in one go, following the Build Order (section 10). Keep everything **simple**: small files, no extra features beyond this spec, plain-English comments. If a library API has changed, use the current recommended way. Finish by running the checks in section 11.

---

## 1. What it does

The user types something like:

> "Birthday dinner for 8 in Dallas next Saturday at 7pm, budget $400, invite sam@gmail.com"

1. The **Orchestrator** turns the request into a plan.
2. The **Venue Scout** and **Logistics** agents run **in parallel**.
3. The **Invites** agent drafts the email and calendar event.
4. The app **pauses**. Nothing is sent until the user clicks **Share**.
5. On Share: the Gmail email is sent and the Google Calendar event is created.

State is saved per conversation (follow-ups like "make it cheaper" work), and user preferences ("I'm vegetarian") are remembered across conversations.

---

## 2. Stack

| Part | Choice |
|---|---|
| LLM | Claude (`langchain-anthropic`), model from env `ANTHROPIC_MODEL` (default `claude-sonnet-5-5`) |
| Agents | LangGraph |
| MCP | One FastMCP server for Google (Gmail + Calendar), connected with `langchain-mcp-adapters` |
| Native tools | `@tool` functions + Tavily web search |
| Structured output | Pydantic |
| State + memory | Postgres (Neon): LangGraph `PostgresSaver` + `PostgresStore` |
| Backend | FastAPI with SSE streaming |
| Frontend | Next.js + TypeScript + Tailwind + shadcn/ui |
| Observability | LangSmith (tracing turned on with env vars, no extra code needed) |
| Hosting | Vercel (frontend), Render (backend), Neon (database) — all free |

---

## 3. Folder structure

```
event-planner/
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── render.yaml
│   ├── main.py          # FastAPI routes + SSE streaming + Google OAuth
│   ├── graph.py         # agents, graph, approval pause
│   ├── models.py        # Pydantic output models
│   ├── prompts.py       # system prompts
│   ├── tools.py         # weather, budget, web search, save_preference
│   ├── google_mcp.py    # MCP server: Gmail + Calendar
│   └── test_app.py      # tests
└── frontend/
    ├── app/page.tsx     # the one main screen
    ├── components/
    │   ├── Chat.tsx
    │   ├── AgentPanel.tsx    # 3 agents' live status + tool call log
    │   ├── ResultCards.tsx   # venues, weather, budget
    │   └── ApprovalCard.tsx  # email preview + Share / Cancel
    └── lib/api.ts       # API calls + SSE reader
```

---

## 4. Agents and tools

| Agent | Job | Tools | Output |
|---|---|---|---|
| **Orchestrator** | Understand the request, save preferences | `save_preference` | `EventRequest` |
| **Venue Scout** | Find 3 venues | `web_search` (Tavily) | `VenueList` |
| **Logistics** | Weather, availability, budget | `get_weather`, `calculate_budget`, `check_free_busy` (MCP) | `LogisticsReport` |
| **Invites** | Draft the email + calendar event | `create_gmail_draft` (MCP) | `InviteDraft` |

**Tools:**

| Tool | Type | What it does |
|---|---|---|
| `web_search(query)` | Native (Tavily) | Search for venues, max 5 results |
| `get_weather(city, date)` | Native | Open-Meteo forecast (free, no key). If the date is too far out, say "forecast not available yet" |
| `calculate_budget(total, guests)` | Native | Plain math: food 70%, tip 20%, misc 10%, per-person cost |
| `save_preference(text)` | Native | Saves a preference to the Postgres store |
| `check_free_busy(start, end)` | MCP | Google Calendar free/busy |
| `create_gmail_draft(to, subject, body)` | MCP | Saves a Gmail draft |
| `send_draft(draft_id)` | MCP | **Share step only, never given to an agent** |
| `create_event(...)` | MCP | **Share step only, never given to an agent** |

Build each sub-agent with LangGraph's prebuilt tool-calling agent and a structured `response_format`.

---

## 5. Graph

```
START → orchestrator → [venue, logistics in parallel] → invites → approval → share → END
                                                                     ↓
                                                                  cancel → END
```

- `approval` uses `interrupt()`. Resume with `Command(resume="share")` or `Command(resume="cancel")`.
- Follow-ups on the same `thread_id` rerun the whole graph with the updated request. Keep it simple: always run all agents.
- Changes to the email are made by chat ("change the subject to …"), not an edit form.

**Shared state:**

```python
class EventState(TypedDict):
    messages: Annotated[list, add_messages]
    request: EventRequest | None
    venues: VenueList | None
    logistics: LogisticsReport | None
    invite: InviteDraft | None
    draft_id: str | None
    status: str          # "planning" | "waiting_approval" | "shared" | "cancelled"
```

---

## 6. Structured output (`models.py`)

```python
class EventRequest(BaseModel):
    title: str
    date: str               # ISO date
    start_time: str         # "19:00"
    location: str
    guest_count: int
    guest_emails: list[str] = []
    budget: float | None = None
    notes: str = ""

class Venue(BaseModel):
    name: str
    address: str
    price_level: str        # "$" to "$$$$"
    why_it_fits: str

class VenueList(BaseModel):
    venues: list[Venue]

class LogisticsReport(BaseModel):
    weather: str            # e.g. "Clear, 72°F, 5% rain"
    available: bool         # is the user free at that time?
    total_budget: float
    per_person: float
    warnings: list[str]

class InviteDraft(BaseModel):
    to: list[str]
    subject: str
    body: str
    start_iso: str
    end_iso: str
    location: str
```

---

## 7. Prompts (`prompts.py`)

Structured role prompts with XML tags. `{memory}` = saved preferences, `{today}` = today's date.

```python
ORCHESTRATOR_PROMPT = """
<role>You are the Orchestrator of an event planning team.</role>
<task>Turn the user's request into an EventRequest.</task>
<rules>
- Today is {today}. Turn "next Saturday" into an ISO date.
- If something is missing, choose a sensible default.
- If the user states a lasting preference (diet, budget, area), call save_preference.
- On follow-ups ("also invite x@y.com", "make it cheaper"), update the existing request.
</rules>
<user_preferences>{memory}</user_preferences>
"""

VENUE_PROMPT = """
<role>You are the Venue Scout.</role>
<can_do>Search for venues with web_search.</can_do>
<cannot_do>Book anything, send emails, or plan the budget.</cannot_do>
<output>Return 3 venues that fit the location, size, budget, and preferences.</output>
<user_preferences>{memory}</user_preferences>
"""

LOGISTICS_PROMPT = """
<role>You are the Logistics agent.</role>
<can_do>Check weather, the user's calendar availability, and the budget.</can_do>
<cannot_do>Pick venues or send anything.</cannot_do>
<rules>Call all three tools. Add warnings for rain, conflicts, or going over budget.</rules>
"""

INVITES_PROMPT = """
<role>You are the Invites agent.</role>
<can_do>Write a short, friendly invite and save it with create_gmail_draft.</can_do>
<cannot_do>NEVER send email or create calendar events. A human approves first.</cannot_do>
<example>
Subject: You're invited: Sam's Birthday Dinner 🎉
Hi all, join us Saturday, Oct 31 at 7pm at Uchi Dallas (2817 Maple Ave).
See you there!
</example>
<user_preferences>{memory}</user_preferences>
"""
```

---

## 8. Backend (`main.py`)

| Route | Purpose |
|---|---|
| `POST /api/plan` | `{thread_id, message}` → SSE stream of the run |
| `POST /api/approve` | `{thread_id, action: "share" \| "cancel"}` → SSE stream |
| `GET /api/thread/{id}` | Current state (restore UI on reload) |
| `GET /auth/google` + `/auth/google/callback` | Google OAuth; token saved to Postgres |
| `GET /health` | Health check |

**SSE events:** `agent_status` (agent, status), `tool_call` (agent, tool, mcp or native), `result` (venues / logistics / invite), `approval_required`, `shared`, `error`, `done`.

**Google OAuth:** scopes `gmail.compose`, `calendar.events`, `calendar.freebusy`. Use `access_type="offline"`. Store the token in a Postgres table, refresh automatically. Only ever *create* calendar events.

**Simple protection:** all `/api/*` routes require header `x-app-password` matching env `APP_PASSWORD` (the app is wired to the owner's Gmail).

**Mock mode:** if `MOCK_MODE=true`, `google_mcp.py` returns fake data and "send" only logs. Claude still runs for real.

**`.env.example`:**

```
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5-5
TAVILY_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback
DATABASE_URL=
FRONTEND_URL=http://localhost:3000
APP_PASSWORD=
MOCK_MODE=true

# LangSmith observability
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=event-planner
```

**LangSmith:** tracing works automatically from the env vars above. Also add, on every graph run:

```python
config = {
    "configurable": {"thread_id": thread_id},
    "run_name": "event-planner",
    "tags": ["mock" if settings.MOCK_MODE else "live"],
    "metadata": {"thread_id": thread_id},
}
```

- Give each agent node a clear name (`orchestrator`, `venue_scout`, `logistics`, `invites`, `approval`, `share`) so traces are easy to read.
- Wrap the plain-Python share step with `@traceable(name="share")` so it shows up too.
- If `LANGSMITH_API_KEY` is empty, the app still runs normally (tracing just stays off).

---

## 9. UI (one screen)

```
┌────────────────────────────────────────────────────┐
│ Event Planner        [Demo mode] [Connect Google]  │
├──────────────────────────────┬─────────────────────┤
│ Chat                         │ Agents              │
│  - plan                      │ 🧭 Orchestrator  ✓  │
│  - venue cards (3)           │ 📍 Venue    ...     │
│  - weather + budget card     │ 🌤 Logistics ...    │
│  - ApprovalCard              │ ✉️ Invites          │
│      [Share] [Cancel]        │ Tool calls:         │
│                              │  [MCP] check_free…  │
│ [ type a message ...    ]    │  [Tool] get_weather │
└──────────────────────────────┴─────────────────────┘
```

- Agent cards show live status (idle → working → done) with a small pulse while working.
- Tool call log tags each call **MCP** or **Tool**.
- ApprovalCard shows recipients, subject, body, and the date/time/place. **Share** asks "Send email and create the event?" before sending.
- A password prompt on first load (saved in localStorage).
- 3 example prompt chips on an empty chat.
- Clean, modern, dark mode. Panels stack on phone width.

---

## 10. Build order

1. `models.py`, `prompts.py`, `tools.py`
2. `google_mcp.py` (with mock mode)
3. `graph.py` (agents, parallel run, `interrupt()` approval, share step, Postgres memory)
4. `main.py` (routes, SSE, OAuth)
5. Frontend
6. `render.yaml` + `README.md` (local setup, Google Cloud setup step by step, LangSmith setup, deploy to Render + Vercel + Neon)

---

## 11. Done when

- [ ] With `MOCK_MODE=true`, a full run works: request → parallel agents → cards → Share → success.
- [ ] Nothing is sent before Share, and no agent has `send_draft` or `create_event` (test this).
- [ ] "Make it cheaper" on the same thread updates the plan.
- [ ] "Remember I'm vegetarian" is used in a new thread.
- [ ] With real keys, Share sends a real email and creates a real Calendar event.
- [ ] A run shows up in LangSmith under project `event-planner`, with each agent and tool call visible and tagged mock or live.
- [ ] `pytest` passes and `npm run build` succeeds.
