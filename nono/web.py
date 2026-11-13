"""Free, key-less web tools: weather (Open-Meteo) and search (DuckDuckGo Instant Answer + Wikipedia)."""
from __future__ import annotations

from typing import Any

import httpx

WMO = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "heavy showers",
       82: "violent showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm"}
UA = {"User-Agent": "NONO-Assistant/1.0 (personal assistant)"}


async def weather(city: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15, headers=UA) as c:
        g = await c.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1})
        res = (g.json().get("results") or [])
        if not res:
            return {"error": f"city '{city}' not found"}
        loc = res[0]
        w = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "forecast_days": 3})
        d = w.json()
    cur = d.get("current", {})
    daily = d.get("daily", {})
    return {
        "place": f"{loc['name']}, {loc.get('country', '')}".strip(", "),
        "temperature_c": cur.get("temperature_2m"), "feels_like_c": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"), "wind_kmh": cur.get("wind_speed_10m"),
        "condition": WMO.get(cur.get("weather_code"), "unknown"),
        "forecast": [
            {"date": daily["time"][i], "max_c": daily["temperature_2m_max"][i], "min_c": daily["temperature_2m_min"][i],
             "rain_chance": daily["precipitation_probability_max"][i], "condition": WMO.get(daily["weather_code"][i], "")}
            for i in range(len(daily.get("time", [])))
        ],
    }


async def search(query: str) -> dict[str, Any]:
    out: dict[str, Any] = {"query": query, "results": []}
    async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as c:
        try:
            ddg = (await c.get("https://api.duckduckgo.com/", params={"q": query, "format": "json", "no_html": 1,
                                                                     "skip_disambig": 1})).json()
            if ddg.get("AbstractText"):
                out["results"].append({"title": ddg.get("Heading"), "snippet": ddg["AbstractText"], "url": ddg.get("AbstractURL")})
            if ddg.get("Answer"):
                out["results"].append({"title": "Answer", "snippet": str(ddg["Answer"]), "url": ""})
            for t in (ddg.get("RelatedTopics") or [])[:3]:
                if isinstance(t, dict) and t.get("Text"):
                    out["results"].append({"title": t.get("Text")[:60], "snippet": t["Text"], "url": t.get("FirstURL")})
        except Exception:
            pass
        lang = "bn" if any("ঀ" <= ch <= "৿" for ch in query) else "en"
        try:
            s = (await c.get(f"https://{lang}.wikipedia.org/w/api.php", params={
                "action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 3})).json()
            for hit in s.get("query", {}).get("search", [])[:2]:
                summ = (await c.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{hit['title'].replace(' ', '_')}")).json()
                out["results"].append({"title": hit["title"], "snippet": summ.get("extract", "")[:700],
                                       "url": summ.get("content_urls", {}).get("desktop", {}).get("page")})
        except Exception:
            pass
    if not out["results"]:
        out["note"] = "no results found"
    return out
