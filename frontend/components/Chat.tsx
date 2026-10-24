"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type ChatMessage = { role: "user" | "assistant" | "error"; content: string };

const EXAMPLES = [
  "Birthday dinner for 8 in Dallas next Saturday at 7pm, budget $400, invite sam@gmail.com",
  "Team lunch for 5 in Austin this Friday at noon, remember I'm vegetarian",
  "Picnic for 12 in Denver next Sunday at 11am, cheap, invite alex@example.com",
];

// Chat history, the result cards (passed as children), and the message box.
export default function Chat({ messages, busy, onSend, children }: {
  messages: ChatMessage[]; busy: boolean; onSend: (text: string) => void; children: React.ReactNode;
}) {
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, children]);

  function send(value: string) {
    if (!value.trim() || busy) return;
    onSend(value.trim());
    setText("");
  }

  return (
    <div className="flex h-full min-h-[60vh] flex-col rounded-xl ring-1 ring-foreground/10">
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">Describe your event, or try one:</p>
            {EXAMPLES.map((e) => (
              <button key={e} onClick={() => send(e)}
                className="block w-full rounded-lg bg-muted/50 px-3 py-2 text-left text-sm hover:bg-muted">
                {e}
              </button>
            ))}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={
            m.role === "user" ? "ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
            : m.role === "error" ? "max-w-[85%] rounded-lg bg-destructive/20 px-3 py-2 text-sm text-destructive"
            : "max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm"}>
            {m.content}
          </div>
        ))}
        {children}
        {busy && <p className="animate-pulse text-sm text-muted-foreground">Agents are working…</p>}
        <div ref={bottom} />
      </div>
      <form className="flex gap-2 border-t p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <Input value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
          placeholder="Type a message…" aria-label="Message" />
        <Button type="submit" disabled={busy || !text.trim()}>Send</Button>
      </form>
    </div>
  );
}
