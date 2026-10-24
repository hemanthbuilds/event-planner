"""FastAPI app: chat routes with SSE streaming, approval, and Google OAuth."""
import hashlib
import hmac
import json
import os
import secrets
import time
from contextlib import AsyncExitStack, asynccontextmanager
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()  # must run before importing graph/google_mcp, which read env vars

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.memory import InMemoryStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from google_mcp import SCOPES, load_token, save_token
from graph import MOCK_MODE, build_graph, mcp_client, run_config

DATABASE_URL = os.getenv("DATABASE_URL", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
TOKEN_URI = "https://oauth2.googleapis.com/token"


@asynccontextmanager
async def lifespan(app: FastAPI):
    mcp_tools = await mcp_client().get_tools()
    async with AsyncExitStack() as stack:
        if DATABASE_URL:
            # A checked pool, because Neon closes idle connections when it scales to zero.
            pool = await stack.enter_async_context(AsyncConnectionPool(
                DATABASE_URL, check=AsyncConnectionPool.check_connection, open=False,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row}))
            saver, store = AsyncPostgresSaver(pool), AsyncPostgresStore(pool)
            await saver.setup()
            await store.setup()
        else:
            # shortcut: no DATABASE_URL means memory is lost on restart; set it for real use.
            saver, store = InMemorySaver(), InMemoryStore()
        app.state.graph = build_graph(mcp_tools, saver, store)
        yield


app = FastAPI(title="Event Planner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_URL],
                   allow_methods=["*"], allow_headers=["*"])


def check_password(x_app_password: str = Header("")):
    """Simple protection: the app is wired to the owner's Gmail."""
    if not APP_PASSWORD or not secrets.compare_digest(x_app_password, APP_PASSWORD):
        raise HTTPException(401, "Wrong or missing app password")


class PlanBody(BaseModel):
    thread_id: str
    message: str


class ApproveBody(BaseModel):
    thread_id: str
    action: str  # "share" | "cancel"


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def run_stream(graph_input, thread_id: str):
    """Run the graph and turn its custom events into SSE messages."""
    graph, config = app.state.graph, run_config(thread_id)
    try:
        async for event in graph.astream(graph_input, config, stream_mode="custom"):
            yield sse(event.pop("event"), event)
        state = await graph.aget_state(config)
        if state.interrupts:
            yield sse("approval_required", state.interrupts[0].value)
    except Exception as e:  # report to the UI instead of dropping the stream
        yield sse("error", {"message": str(e)})
    yield sse("done", {})


@app.post("/api/plan", dependencies=[Depends(check_password)])
async def plan(body: PlanBody):
    return StreamingResponse(run_stream({"messages": [("user", body.message)]}, body.thread_id),
                             media_type="text/event-stream")


@app.post("/api/approve", dependencies=[Depends(check_password)])
async def approve(body: ApproveBody):
    if body.action not in ("share", "cancel"):
        raise HTTPException(400, "action must be 'share' or 'cancel'")
    state = await app.state.graph.aget_state(run_config(body.thread_id))
    if not state.interrupts:
        raise HTTPException(409, "This plan is not waiting for approval")
    return StreamingResponse(run_stream(Command(resume=body.action), body.thread_id),
                             media_type="text/event-stream")


@app.get("/api/thread/{thread_id}", dependencies=[Depends(check_password)])
async def get_thread(thread_id: str):
    state = await app.state.graph.aget_state(run_config(thread_id))
    values = dict(state.values)
    values["messages"] = [{"role": m.type, "content": m.content} for m in values.get("messages", [])]
    return {**values, "waiting_approval": bool(state.interrupts)}


def sign_state(issued_at: str) -> str:
    return hmac.new(APP_PASSWORD.encode(), issued_at.encode(), hashlib.sha256).hexdigest()


@app.get("/auth/google", dependencies=[Depends(check_password)])
def google_login():
    """Returns the Google consent URL. Password-protected so only the owner can connect an account."""
    issued_at = str(int(time.time()))
    params = {
        "client_id": GOOGLE_CLIENT_ID, "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent",  # consent makes Google return a refresh token
        "state": f"{issued_at}.{sign_state(issued_at)}",  # proves the callback came from our login
    }
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)}


@app.get("/auth/google/callback")
def google_callback(code: str, state: str = ""):
    issued_at, _, signature = state.partition(".")
    if (not APP_PASSWORD or not issued_at.isdigit()
            or not hmac.compare_digest(signature, sign_state(issued_at))
            or time.time() - int(issued_at) > 600):
        raise HTTPException(400, "Invalid or expired login link. Click Connect Google again.")
    token = httpx.post(TOKEN_URI, data={
        "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI, "grant_type": "authorization_code",
    }, timeout=10).json()
    if "refresh_token" not in token:
        raise HTTPException(400, f"Google did not return a refresh token: {token}")
    # No access token stored: google_mcp refreshes on first use and saves the result.
    save_token({"refresh_token": token["refresh_token"], "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET, "token_uri": TOKEN_URI, "scopes": SCOPES})
    return RedirectResponse(FRONTEND_URL)


@app.get("/health")
def health():
    try:
        connected = bool(load_token())
    except Exception:
        connected = False
    return {"ok": True, "mock_mode": MOCK_MODE, "google_connected": connected}
