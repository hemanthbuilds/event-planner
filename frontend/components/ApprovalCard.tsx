import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import type { Invite } from "@/lib/api";

// Email + event preview. Nothing is sent until the user clicks Share and confirms.
export default function ApprovalCard({ invite, status, waiting, busy, onShare, onCancel }: {
  invite: Invite; status?: string; waiting: boolean; busy: boolean;
  onShare: () => void; onCancel: () => void;
}) {
  // Show the event's own local time (no conversion to the viewer's time zone).
  const when = invite.start_iso.slice(0, 16).replace("T", " at ");
  return (
    <Card className="ring-sky-500/40">
      <CardHeader><CardTitle>✉️ Ready to share</CardTitle></CardHeader>
      <CardContent className="space-y-2">
        <p><span className="text-muted-foreground">To:</span> {invite.to.join(", ") || "(no recipients)"}</p>
        <p><span className="text-muted-foreground">Subject:</span> {invite.subject}</p>
        <p className="whitespace-pre-wrap rounded-lg bg-muted/50 p-3">{invite.body}</p>
        <p><span className="text-muted-foreground">When:</span> {when}</p>
        <p><span className="text-muted-foreground">Where:</span> {invite.location}</p>
      </CardContent>
      <CardFooter className="gap-2">
        {status === "shared" ? (
          <p className="text-emerald-400">✓ Email sent and calendar event created.</p>
        ) : status === "cancelled" ? (
          <p className="text-muted-foreground">Cancelled. Nothing was sent.</p>
        ) : (
          <>
            <Button disabled={!waiting || busy}
              onClick={() => confirm("Send email and create the event?") && onShare()}>
              Share
            </Button>
            <Button variant="outline" disabled={!waiting || busy} onClick={onCancel}>Cancel</Button>
            <span className="text-xs text-muted-foreground">Edit by chat, e.g. “change the subject to …”</span>
          </>
        )}
      </CardFooter>
    </Card>
  );
}
