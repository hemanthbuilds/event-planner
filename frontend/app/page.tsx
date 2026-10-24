"use client";
import { useEffect, useState } from "react";
import AgentPanel, { AGENTS, type AgentStatus, type ToolCall } from "@/components/AgentPanel";
import ApprovalCard from "@/components/ApprovalCard";
import Chat, { type ChatMessage } from "@/components/Chat";
import ResultCards from "@/components/ResultCards";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import * as api from "@/lib/api";

const idle = () => Object.fromEntries(AGENTS.map((a) => [a.id, "idle" as AgentStatus]));

export default function Home() {
  const [password, setPassword] = useState<string | null>(null);
  const [threadId, setThreadId] = useState("");
  const [health, setHealth] = useState<{ mock_mode: boolean; google_connected: boolean }>();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [statuses, setStatuses] = useState<Record<string, AgentStatus>>(idle);
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([]);
  const [request, setRequest] = useState<api.EventRequest | null>();
  const [venues, setVenues] = useState<api.Venue[]>();
  const [logistics, setLogistics] = useState<api.Logistics | null>();
  const [invite, setInvite] = useState<api.Invite | null>();
  const [status, setStatus] = useState<string>();
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);

  // On first load: read the saved password and thread id from localStorage.
  useEffect(() => {
    // localStorage only exists in the browser, so it must be read after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPassword(localStorage.getItem("app-password") ?? "");
    let id = localStorage.getItem("thread-id");
    if (!id) localStorage.setItem("thread-id", (id = crypto.randomUUID()));
    setThreadId(id);
    api.getHealth().then(setHealth).catch(() => {});
  }, []);

  // Restore the conversation after a reload.
  useEffect(() => {
    if (!password || !threadId) return;
    api.getThread(threadId).then((s) => {
      setMessages((s.messages ?? []).map((m) => ({ role: m.role === "human" ? "user" : "assistant", content: m.content })));
      setRequest(s.request); setVenues(s.venues?.venues); setLogistics(s.logistics);
      setInvite(s.invite); setStatus(s.status); setWaiting(s.waiting_approval);
    }).catch(handleError);
  }, [password, threadId]);

  function handleError(e: unknown) {
    if (e instanceof api.AuthError) {
      localStorage.removeItem("app-password");
      setPassword("");
    }
    setMessages((m) => [...m, { role: "error", content: e instanceof Error ? e.message : String(e) }]);
  }

  // Apply one SSE event from the backend to the screen.
  const onEvent: api.OnEvent = (event, data) => {
    if (event === "agent_status") setStatuses((s) => ({ ...s, [data.agent]: data.status }));
    else if (event === "tool_call") setToolCalls((t) => [...t, data]);
    else if (event === "result") {
      if (data.kind === "request") setRequest(data.data);
      if (data.kind === "venues") setVenues(data.data.venues);
      if (data.kind === "logistics") setLogistics(data.data);
      if (data.kind === "invite") setInvite(data.data);
    } else if (event === "approval_required") { setWaiting(true); setStatus("waiting_approval"); }
    else if (event === "shared") { setWaiting(false); setStatus("shared"); }
    else if (event === "error") setMessages((m) => [...m, { role: "error", content: data.message }]);
  };

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    try { await fn(); } catch (e) { handleError(e); } finally { setBusy(false); }
  }

  function send(text: string) {
    setMessages((m) => [...m, { role: "user", content: text }]);
    setStatuses(idle()); setToolCalls([]); setWaiting(false); setStatus("planning");
    setVenues(undefined); setLogistics(undefined); setInvite(undefined);
    run(() => api.plan(threadId, text, onEvent));
  }

  function decide(action: "share" | "cancel") {
    run(async () => {
      await api.approve(threadId, action, onEvent);
      if (action === "cancel") { setWaiting(false); setStatus("cancelled"); }
    });
  }

  function newChat() {
    const id = crypto.randomUUID();
    localStorage.setItem("thread-id", id);
    setThreadId(id); setMessages([]); setStatuses(idle()); setToolCalls([]);
    setRequest(undefined); setVenues(undefined); setLogistics(undefined);
    setInvite(undefined); setStatus(undefined); setWaiting(false);
  }

  if (password === null) return null; // still reading localStorage

  if (!password) {
    return (
      <main className="flex flex-1 items-center justify-center p-4">
        <form className="w-full max-w-sm space-y-3" onSubmit={(e) => {
          e.preventDefault();
          const value = new FormData(e.currentTarget).get("password") as string;
          localStorage.setItem("app-password", value);
          setPassword(value);
          setMessages([]);
        }}>
          <h1 className="text-xl font-semibold">Event Planner</h1>
          <Input name="password" type="password" placeholder="App password" aria-label="App password" required />
          <Button type="submit" className="w-full">Enter</Button>
        </form>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">Event Planner</h1>
        {health?.mock_mode && <Badge variant="secondary">Demo mode</Badge>}
        {health && !health.mock_mode && (health.google_connected
          ? <Badge>Google connected</Badge>
          : <Button size="sm" onClick={() => api.googleAuthUrl()
              .then((url) => { window.location.href = url; }).catch(handleError)}>Connect Google</Button>)}
        <Button size="sm" variant="outline" onClick={newChat} disabled={busy}>New chat</Button>
      </header>
      <div className="grid flex-1 gap-4 md:grid-cols-[1fr_320px]">
        <Chat messages={messages} busy={busy} onSend={send}>
          <ResultCards request={request} venues={venues} logistics={logistics} />
          {invite && <ApprovalCard invite={invite} status={status} waiting={waiting} busy={busy}
            onShare={() => decide("share")} onCancel={() => decide("cancel")} />}
        </Chat>
        <aside><AgentPanel statuses={statuses} toolCalls={toolCalls} /></aside>
      </div>
    </main>
  );
}
