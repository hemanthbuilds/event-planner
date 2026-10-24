"""Native tools: web search, weather, budget math, and saving preferences."""
import uuid
from datetime import date, timedelta

import httpx
from langchain_core.tools import tool
from langgraph.config import get_store

PREFS_NAMESPACE = ("preferences",)  # single-owner app, so one shared namespace


@tool
def web_search(query: str) -> str:
    """Search the web for venues. Returns up to 5 results."""
    from langchain_tavily import TavilySearch  # created lazily so tests run without a Tavily key
    return str(TavilySearch(max_results=5).invoke({"query": query}))


def describe_weather(code: int) -> str:
    """Turn an Open-Meteo weather code into a word or two."""
    if code == 0:
        return "Clear"
    if code <= 3:
        return "Partly cloudy"
    if code in (45, 48):
        return "Fog"
    if code <= 67 or 80 <= code <= 82:
        return "Rain"
    if code <= 77 or code in (85, 86):
        return "Snow"
    return "Thunderstorms"


@tool
def get_weather(city: str, date_iso: str) -> str:
    """Get the weather forecast for a city on a date (YYYY-MM-DD)."""
    day = date.fromisoformat(date_iso)
    if not date.today() <= day <= date.today() + timedelta(days=15):  # Open-Meteo forecasts 16 days
        return "forecast not available yet"
    geo = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": city.split(",")[0], "count": 1}, timeout=10).json()
    if not geo.get("results"):
        return f"could not find city {city}"
    place = geo["results"][0]
    daily = httpx.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": place["latitude"], "longitude": place["longitude"],
        "daily": "weather_code,temperature_2m_max,precipitation_probability_max",
        "temperature_unit": "fahrenheit", "timezone": "auto",
        "start_date": date_iso, "end_date": date_iso,
    }, timeout=10).json()["daily"]
    return (f"{describe_weather(daily['weather_code'][0])}, "
            f"{round(daily['temperature_2m_max'][0])}°F, "
            f"{daily['precipitation_probability_max'][0]}% rain")


@tool
def calculate_budget(total: float, guests: int) -> dict:
    """Split a budget: food 70%, tip 20%, misc 10%, plus the per-person cost."""
    return {
        "total": round(total, 2),
        "food": round(total * 0.7, 2),
        "tip": round(total * 0.2, 2),
        "misc": round(total * 0.1, 2),
        "per_person": round(total / max(guests, 1), 2),
    }


@tool
async def save_preference(text: str) -> str:
    """Save a lasting user preference, like "I'm vegetarian"."""
    await get_store().aput(PREFS_NAMESPACE, str(uuid.uuid4()), {"text": text})
    return f"Saved preference: {text}"
