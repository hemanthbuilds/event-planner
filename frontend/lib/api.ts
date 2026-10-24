// API calls to the FastAPI backend + a small reader for its SSE streams.

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type EventRequest = {
  title: string; date: string; start_time: string; location: string;
  guest_count: number; guest_emails: string[]; budget: number | null; notes: string;
};
export type Venue = { name: string; address: string; price_level: string; why_it_fits: string };
export type Logistics = {
  weather: string; available: boolean; total_budget: number; per_person: number; warnings: string[];
};
export type Invite = {
  to: string[]; subject: string; body: string; start_iso: string; end_iso: string; location: string;
};
export type ThreadState = {
  messages?: { role: string; content: string }[];
  request?: EventRequest | null;
  venues?: { venues: Venue[] } | null;
  logistics?: Logistics | null;
  invite?: Invite | null;
  status?: string;
  waiting_approval: boolean;
};
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type OnEvent = (event: string, data: any) => void;

export class AuthError extends Error {}

function headers() {
  return {
    "content-type": "application/json",
    "x-app-password": localStorage.getItem("app-password") ?? "",
  };
}

async function check(res: Response) {
  if (res.status === 401) throw new AuthError("Wrong app password");
  if (!res.ok) throw new Error(await res.text());
  return res;
}

// POST and read the Server-Sent Events stream ("event: x\ndata: {...}\n\n").
async function streamPost(path: string, body: object, onEvent: OnEvent) {
  const res = await check(await fetch(API_URL + path, {
    method: "POST", headers: headers(), body: JSON.stringify(body),
  }));
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    const parts = buffer.split("\n\n");
    buffer = parts.pop()!;
    for (const part of parts) {
      const event = part.match(/^event: (.*)$/m)?.[1];
      const data = part.match(/^data: (.*)$/m)?.[1];
      if (event) onEvent(event, data ? JSON.parse(data) : {});
    }
  }
}

export const plan = (threadId: string, message: string, onEvent: OnEvent) =>
  streamPost("/api/plan", { thread_id: threadId, message }, onEvent);

export const approve = (threadId: string, action: "share" | "cancel", onEvent: OnEvent) =>
  streamPost("/api/approve", { thread_id: threadId, action }, onEvent);

export async function getThread(threadId: string): Promise<ThreadState> {
  const res = await check(await fetch(`${API_URL}/api/thread/${threadId}`, { headers: headers() }));
  return res.json();
}

// The backend only hands out the Google login link to someone with the app password.
export async function googleAuthUrl(): Promise<string> {
  const res = await check(await fetch(`${API_URL}/auth/google`, { headers: headers() }));
  return (await res.json()).url;
}

export async function getHealth(): Promise<{ mock_mode: boolean; google_connected: boolean }> {
  return (await fetch(`${API_URL}/health`)).json();
}
