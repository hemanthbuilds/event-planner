"""Pydantic models the agents must return (structured output)."""
from pydantic import BaseModel


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
