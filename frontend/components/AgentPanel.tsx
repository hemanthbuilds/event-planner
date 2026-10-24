import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export type AgentStatus = "idle" | "working" | "done";
export type ToolCall = { agent: string; tool: string; kind: "mcp" | "tool" };

export const AGENTS = [
  { id: "orchestrator", label: "Orchestrator", icon: "🧭" },
  { id: "venue_scout", label: "Venue Scout", icon: "📍" },
  { id: "logistics", label: "Logistics", icon: "🌤" },
  { id: "invites", label: "Invites", icon: "✉️" },
];

// Live status of each agent plus a log of every tool call.
export default function AgentPanel({ statuses, toolCalls }: {
  statuses: Record<string, AgentStatus>; toolCalls: ToolCall[];
}) {
  return (
    <Card>
      <CardHeader><CardTitle>Agents</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-2">
          {AGENTS.map((a) => {
            const status = statuses[a.id] ?? "idle";
            return (
              <li key={a.id} className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2">
                <span>{a.icon} {a.label}</span>
                {status === "working" ? (
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <span className="size-2 animate-pulse rounded-full bg-sky-400" /> working
                  </span>
                ) : status === "done" ? (
                  <span className="text-emerald-400" aria-label="done">✓</span>
                ) : (
                  <span className="text-xs text-muted-foreground">idle</span>
                )}
              </li>
            );
          })}
        </ul>
        <div>
          <p className="mb-2 text-xs font-medium uppercase text-muted-foreground">Tool calls</p>
          {toolCalls.length === 0 && <p className="text-xs text-muted-foreground">None yet</p>}
          <ul className="space-y-1 font-mono text-xs">
            {toolCalls.map((c, i) => (
              <li key={i} className="flex items-center gap-2">
                <Badge variant={c.kind === "mcp" ? "default" : "secondary"}>
                  {c.kind === "mcp" ? "MCP" : "Tool"}
                </Badge>
                <span className="truncate">{c.tool}</span>
                <span className="ml-auto text-muted-foreground">{c.agent}</span>
              </li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}
