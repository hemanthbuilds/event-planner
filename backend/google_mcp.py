"""MCP server for Google: Gmail drafts/sending and Calendar free-busy/events.

Run by the backend as a stdio subprocess. With MOCK_MODE=true it returns fake
data and "sending" only writes a log line.
"""
import base64
import json
import logging
import os
import uuid
from email.message import EmailMessage

import psycopg
from mcp.server.fastmcp import FastMCP

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]
log = logging.getLogger("google_mcp")  # logs go to stderr; stdout is the MCP channel
mcp = FastMCP("google")


# ---------- token storage (also used by main.py for OAuth) ----------

def _db():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def save_token(info: dict) -> None:
    with _db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS google_token (id int PRIMARY KEY, data jsonb)")
        conn.execute(
            "INSERT INTO google_token (id, data) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
            (json.dumps(info),),
        )


def load_token() -> dict | None:
    if not os.getenv("DATABASE_URL"):
        return None
    with _db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS google_token (id int PRIMARY KEY, data jsonb)")
        row = conn.execute("SELECT data FROM google_token WHERE id = 1").fetchone()
    return row[0] if row else None


def google_service(name: str, version: str):
    """Build a Google API client, refreshing the saved token if it expired."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    info = load_token()
    if not info:
        raise RuntimeError("Google is not connected. Click 'Connect Google' first.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        save_token(json.loads(creds.to_json()))
    return build(name, version, credentials=creds, cache_discovery=False)


# ---------- MCP tools ----------

@mcp.tool()
def check_free_busy(start: str, end: str) -> dict:
    """Check the user's Google Calendar for busy times.
    start/end are RFC3339 with a UTC offset, e.g. 2026-10-31T19:00:00-05:00."""
    if MOCK_MODE:
        return {"busy": [], "free": True}
    result = google_service("calendar", "v3").freebusy().query(body={
        "timeMin": start, "timeMax": end, "items": [{"id": "primary"}],
    }).execute()
    busy = result["calendars"]["primary"]["busy"]
    return {"busy": busy, "free": not busy}


@mcp.tool()
def create_gmail_draft(to: list[str], subject: str, body: str) -> dict:
    """Save an email as a Gmail draft (does NOT send it). Returns the draft_id."""
    if MOCK_MODE:
        return {"draft_id": f"mock-draft-{uuid.uuid4().hex[:8]}"}
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = google_service("gmail", "v1").users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    return {"draft_id": draft["id"]}


@mcp.tool()
def send_draft(draft_id: str) -> dict:
    """Send a saved Gmail draft. Only called by the Share step after human approval."""
    if MOCK_MODE:
        log.warning("MOCK send_draft: would send draft %s", draft_id)
        return {"sent": True, "mock": True, "draft_id": draft_id}
    sent = google_service("gmail", "v1").users().drafts().send(
        userId="me", body={"id": draft_id}).execute()
    return {"sent": True, "message_id": sent["id"]}


@mcp.tool()
def delete_draft(draft_id: str) -> dict:
    """Delete an outdated Gmail draft this app created. Called by the graph, never by an agent."""
    if MOCK_MODE:
        log.warning("MOCK delete_draft: would delete draft %s", draft_id)
        return {"deleted": True, "mock": True}
    google_service("gmail", "v1").users().drafts().delete(userId="me", id=draft_id).execute()
    return {"deleted": True}


@mcp.tool()
def create_event(summary: str, start_iso: str, end_iso: str, location: str,
                 attendees: list[str], description: str = "") -> dict:
    """Create a Google Calendar event. Only called by the Share step after human approval."""
    if MOCK_MODE:
        log.warning("MOCK create_event: would create '%s' at %s", summary, start_iso)
        return {"event_id": "mock-event", "link": "", "mock": True}
    event = google_service("calendar", "v3").events().insert(
        calendarId="primary", sendUpdates="all", body={
            "summary": summary, "location": location, "description": description,
            "start": {"dateTime": start_iso}, "end": {"dateTime": end_iso},
            "attendees": [{"email": e} for e in attendees],
        }).execute()
    return {"event_id": event["id"], "link": event.get("htmlLink", "")}


if __name__ == "__main__":
    mcp.run()  # stdio transport
