import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { EventRequest, Logistics, Venue } from "@/lib/api";

// The plan summary, the 3 venue cards, and the weather + budget card.
export default function ResultCards({ request, venues, logistics }: {
  request?: EventRequest | null; venues?: Venue[]; logistics?: Logistics | null;
}) {
  return (
    <div className="space-y-3">
      {request && (
        <Card size="sm">
          <CardHeader>
            <CardTitle>{request.title}</CardTitle>
            <CardDescription>
              {request.date} at {request.start_time} · {request.location} · {request.guest_count} guests
              {request.budget != null && ` · $${request.budget}`}
            </CardDescription>
          </CardHeader>
          {request.notes && <CardContent className="text-muted-foreground">{request.notes}</CardContent>}
        </Card>
      )}
      {venues && venues.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {venues.map((v) => (
            <Card key={v.name} size="sm">
              <CardHeader>
                <CardTitle>📍 {v.name}</CardTitle>
                <CardDescription>{v.address} · {v.price_level}</CardDescription>
              </CardHeader>
              <CardContent className="text-muted-foreground">{v.why_it_fits}</CardContent>
            </Card>
          ))}
        </div>
      )}
      {logistics && (
        <Card size="sm">
          <CardHeader><CardTitle>🌤 Weather & budget</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            <p>Weather: {logistics.weather}</p>
            <p>Calendar: {logistics.available ? "✓ You're free" : "⚠️ You have a conflict"}</p>
            <p>Budget: ${logistics.total_budget} total · ${logistics.per_person} per person</p>
            {logistics.warnings.map((w) => <p key={w} className="text-amber-400">⚠️ {w}</p>)}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
