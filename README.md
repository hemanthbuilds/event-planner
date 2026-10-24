# Event Planner — Simple Multi-Agent POC

Type something like *"Birthday dinner for 8 in Dallas next Saturday at 7pm, budget $400, invite sam@gmail.com"*.
An Orchestrator turns it into a plan, a Venue Scout and a Logistics agent run in parallel, the Invites agent
drafts the email, and the app **pauses**. Nothing is sent until you click **Share**.

```
backend/   FastAPI + LangGraph + MCP server (Python, uv)
frontend/  Next.js + Tailwind + shadcn/ui
```

## Run it locally

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+.

**Backend**

```bash
cd backend
cp .env.example .env      # then fill it in (see below)
uv sync
uv run uvicorn main:app --reload --port 8000
```

**Frontend** (second terminal)

```bash
cd frontend
cp .env.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000 and enter your `APP_PASSWORD`.

### What goes in `backend/.env`

| Variable | Needed for | Where to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | always | console.anthropic.com → API Keys |
| `ANTHROPIC_MODEL` | optional | defaults to `claude-sonnet-5-5` |
| `TAVILY_API_KEY` | always (venue search) | app.tavily.com (free tier) |
| `APP_PASSWORD` | always | any password you pick; the UI asks for it |
| `MOCK_MODE` | always | `true` = fake Gmail/Calendar, `false` = real |
| `DATABASE_URL` | recommended | Neon connection string (see below). If empty, memory lives in RAM and is lost on restart |
| `FRONTEND_URL` | always | `http://localhost:3000` locally (used for CORS + OAuth redirect back) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | `MOCK_MODE=false` | Google Cloud setup below |
| `GOOGLE_REDIRECT_URI` | `MOCK_MODE=false` | `http://localhost:8000/auth/google/callback` locally |
| `LANGSMITH_API_KEY` | optional | LangSmith setup below. Empty = tracing off, app still works |

With `MOCK_MODE=true` you only need `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` and `APP_PASSWORD`.

## Tests

```bash
cd backend && uv run pytest
cd frontend && npm run build
```

Tests use a scripted fake Claude, so they need no API keys. They cover the full mock run, the approval pause,
follow-ups on the same thread, preferences carried to a new thread, and that no agent has `send_draft` or `create_event`.

## Database (Neon)

1. Sign up at https://neon.tech and create a project.
2. Dashboard → **Connect** → copy the connection string (`postgresql://...?sslmode=require`).
3. Put it in `DATABASE_URL`. Tables are created automatically on startup.

## Google Cloud setup (for real email + calendar)

1. Go to https://console.cloud.google.com and create a project.
2. **APIs & Services → Library**: enable **Gmail API** and **Google Calendar API**.
3. **Google Auth Platform → Branding** (OAuth consent screen): app name + your email. Audience: **External**.
4. **Audience → Test users**: add the Gmail address you'll send from.
5. **Data Access → Add scopes**: `.../auth/gmail.compose`, `.../auth/calendar.events`, `.../auth/calendar.freebusy`.
6. **Clients → Create client** → *Web application*. Under **Authorized redirect URIs** add
   `http://localhost:8000/auth/google/callback` (and later your Render URL + `/auth/google/callback`).
7. Copy the **Client ID** and **Client secret** into `.env`.
8. Set `MOCK_MODE=false`, make sure `DATABASE_URL` is set (the token is saved there), restart the backend.
9. In the app click **Connect Google** and approve. The button turns into "Google connected".

Note: while the consent screen is in *Testing*, Google expires the refresh token after 7 days. Click
Connect Google again, or publish the app.

## LangSmith (observability)

1. Sign up at https://smith.langchain.com → **Settings → API Keys** → create a key.
2. Set `LANGSMITH_API_KEY`, keep `LANGSMITH_TRACING=true` and `LANGSMITH_PROJECT=event-planner`.
3. Run a plan. The trace `event-planner` appears in the project with nodes `orchestrator`, `venue_scout`,
   `logistics`, `invites`, `approval`, `share`, each tool call, and the tag `mock` or `live`.

## Deploy (all free tiers)

1. **Neon**: create the database as above.
2. **Render (backend)**: push this repo to GitHub. Render → **New → Blueprint** → pick the repo and set the
   blueprint path to `backend/render.yaml`. Fill in the secret env vars it asks for
   (`FRONTEND_URL` = your Vercel URL, `GOOGLE_REDIRECT_URI` = `https://<your-render-app>.onrender.com/auth/google/callback`).
   Add that redirect URI to your Google OAuth client too.
3. **Vercel (frontend)**: Vercel → **Add New Project** → pick the repo, set **Root Directory** to `frontend`,
   and add env var `NEXT_PUBLIC_API_URL=https://<your-render-app>.onrender.com`. Deploy.

Render's free tier sleeps when idle, so the first request after a while takes ~1 minute.
