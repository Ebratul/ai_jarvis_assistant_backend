"""Everything NONO can *do*. The LLM picks tools through the JSON action protocol (works on any model).

kind:
  act      – performs a side effect, result is confirmed in the reply
  observe  – fetches information; NONO makes a second LLM pass to talk about the result
  client   – executed by the frontend (open URL, mute, change view)
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import psutil

from . import web
from .config import settings
from .timeparse import parse_when
from .voices import VOICE_BY_ID, VOICES, resolve_voice

if TYPE_CHECKING:
    from .app_context import Nono

STARTED = time.time()


@dataclass
class Tool:
    name: str
    description: str
    args: str
    kind: str
    run: Callable[["Nono", dict[str, Any]], Awaitable[dict[str, Any]]]


def _fmt(dt: datetime) -> str:
    return dt.astimezone(settings.tz).strftime("%a %d %b %Y, %I:%M %p")


# ----------------------------------------------------------------------------- reminders
async def set_reminder(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    text = a.get("text") or a.get("title") or "Reminder"
    due = parse_when(str(a.get("when") or a.get("time") or "in 10 minutes"), settings.tz)
    rem = ctx.db.add_reminder(text, due, a.get("repeat"), a.get("_source", "voice"))
    await ctx.events.publish("reminder_set", reminder=rem)
    return {"id": rem["id"], "text": text, "due": _fmt(due), "repeat": rem["repeat"]}


async def list_reminders(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    items = ctx.db.list_reminders("pending", 20)
    return {"reminders": [{"id": r["id"], "text": r["text"], "due": _fmt(datetime.fromisoformat(r["due_at"])),
                           "repeat": r["repeat"]} for r in items]}


async def cancel_reminder(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    rows = ctx.db.cancel_reminder(str(a.get("id") or a.get("text") or ""))
    return {"cancelled": [r["text"] for r in rows]}


# ----------------------------------------------------------------------------- calendar
async def add_event(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    title = a.get("title") or a.get("text") or "Event"
    start = parse_when(str(a.get("start") or a.get("when") or "tomorrow 10am"), settings.tz)
    end = parse_when(str(a["end"]), settings.tz) if a.get("end") else start + timedelta(minutes=int(a.get("duration_minutes") or 60))
    google_id = None
    where = "local calendar"
    if ctx.gcal.connected:
        try:
            google_id = (await ctx.gcal.add_event(title, start, end, a.get("location", ""), a.get("notes", "")))["id"]
            where = "Google Calendar"
        except Exception as exc:
            where = f"local calendar (Google failed: {exc})"
    ctx.db.add_event(title, start, end, a.get("location", ""), a.get("notes", ""), google_id)
    if a.get("remind_before_minutes") is not None or a.get("remind", True):
        before = int(a.get("remind_before_minutes") or 15)
        remind_at = start - timedelta(minutes=before)
        if remind_at > datetime.now(settings.tz):
            ctx.db.add_reminder(f"{title} in {before} minutes", remind_at, None, "calendar")
    await ctx.events.publish("event_added", title=title, start=start.isoformat())
    return {"title": title, "start": _fmt(start), "end": _fmt(end), "saved_to": where}


async def list_events(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    days = int(a.get("days") or 7)
    start = datetime.now(settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    return {"events": await upcoming_events(ctx, start, end), "range_days": days}


async def upcoming_events(ctx: "Nono", start: datetime, end: datetime) -> list[dict[str, Any]]:
    events = []
    if ctx.gcal.connected:
        try:
            events = await ctx.gcal.list_events(start, end)
        except Exception:
            events = []
    google_ids = {e["id"] for e in events}
    for e in ctx.db.list_events(start, end):
        if e.get("google_id") not in google_ids:
            events.append({**e, "source": "local"})
    events.sort(key=lambda e: str(e["start"]))
    return events


# ----------------------------------------------------------------------------- telegram
async def send_telegram(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    text = a.get("text") or a.get("message")
    if not text:
        return {"error": "no message text"}
    res = await ctx.telegram.send(str(text), a.get("chat_id"))
    return {"sent": True, **res}


# ----------------------------------------------------------------------------- info
async def get_weather(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    return await web.weather(a.get("city") or a.get("location") or ctx.state.get("city", "Dhaka"))


async def web_search(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    return await web.search(a.get("query") or a.get("q") or "")


async def get_time(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(settings.tz)
    return {"now": _fmt(now), "timezone": settings.timezone, "iso": now.isoformat(timespec="seconds")}


async def system_status(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    return system_snapshot()


def system_snapshot() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    net = psutil.net_io_counters()
    return {
        "cpu": psutil.cpu_percent(interval=None), "cores": psutil.cpu_count(), "ram": vm.percent,
        "ram_used_gb": round(vm.used / 1e9, 1), "ram_total_gb": round(vm.total / 1e9, 1), "disk": du.percent,
        "battery": round(bat.percent) if bat else None, "charging": bool(bat.power_plugged) if bat else None,
        "uptime_s": int(time.time() - psutil.boot_time()), "nono_uptime_s": int(time.time() - STARTED), "host": platform.node(), "os": f"{platform.system()} {platform.release()}",
        "net_sent_mb": round(net.bytes_sent / 1e6, 1), "net_recv_mb": round(net.bytes_recv / 1e6, 1),
    }


async def recall_memory(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    hits = await ctx.rag.search(a.get("query") or "", k=int(a.get("k") or 6))
    return {"memories": [{"when": h["memory"]["created_at"][:16], "instruction": h["memory"]["text"],
                          "response": (h["memory"].get("response") or "")[:200]} for h in hits]}


async def save_note(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    mem = await ctx.rag.remember(a.get("text") or "", kind="note", category="memory", source="note")
    if mem:
        await ctx.rag.update(mem["id"], pinned=True, importance=0.9)
    return {"saved": bool(mem)}


# ----------------------------------------------------------------------------- control
async def switch_model(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    query = " ".join(str(a.get(k) or "") for k in ("provider", "model")).strip()
    resolved = await ctx.hub.resolve(query, ctx.state["provider"])
    if not resolved:
        return {"error": f"no available model matches '{query}'"}
    ctx.set_state(provider=resolved[0], model=resolved[1])
    return {"provider": resolved[0], "model": resolved[1]}


async def switch_voice(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    v = resolve_voice(str(a.get("voice") or ""))
    if not v:
        return {"error": "unknown voice", "available": [x["name"] for x in VOICES]}
    ctx.set_state(voice=v["id"])
    return {"voice": v["id"], "name": v["name"]}


async def client_action(ctx: "Nono", a: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **a}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("set_reminder", "Create a reminder. 'when' = ISO local time (YYYY-MM-DDTHH:MM) or relative like 'in 20 minutes'. repeat optional: hourly|daily|weekly", '{"text": str, "when": str, "repeat"?: str}', "act", set_reminder),
    Tool("list_reminders", "List pending reminders", "{}", "observe", list_reminders),
    Tool("cancel_reminder", "Cancel reminders by id or matching text", '{"text": str}', "act", cancel_reminder),
    Tool("add_event", "Add a calendar event (Google Calendar if connected, else local)", '{"title": str, "start": str, "end"?: str, "duration_minutes"?: int, "location"?: str, "notes"?: str}', "act", add_event),
    Tool("list_events", "List calendar events for the next N days", '{"days": int}', "observe", list_events),
    Tool("send_telegram", "Send a Telegram message to the user's paired chat", '{"text": str}', "act", send_telegram),
    Tool("get_weather", "Current weather + 3 day forecast for a city", '{"city": str}', "observe", get_weather),
    Tool("web_search", "Search the web / Wikipedia for facts or news", '{"query": str}', "observe", web_search),
    Tool("get_time", "Current date and time", "{}", "observe", get_time),
    Tool("system_status", "Computer CPU / RAM / disk / battery status", "{}", "observe", system_status),
    Tool("recall_memory", "Search everything the user has told NONO in the past (years of instructions)", '{"query": str}', "observe", recall_memory),
    Tool("save_note", "Permanently remember an important fact or preference for the user", '{"text": str}', "act", save_note),
    Tool("switch_model", "Switch the AI model/provider (openrouter, groq, huggingface, gemini, openai, ollama or a model name)", '{"provider"?: str, "model"?: str}', "act", switch_model),
    Tool("switch_voice", "Change NONO's voice: " + ", ".join(v["name"] for v in VOICES), '{"voice": str}', "act", switch_voice),
    Tool("open_url", "Open a website in the user's browser", '{"url": str}', "client", client_action),
    Tool("set_mute", "Mute or unmute NONO's voice", '{"muted": bool}', "client", client_action),
    Tool("show_view", "Show a screen: 'hud' (main) or 'brain' (3D memory network)", '{"view": str}', "client", client_action),
]}


def tool_prompt() -> str:
    return "\n".join(f"- {t.name}{t.args}: {t.description}" for t in TOOLS.values())


def voice_name(voice_id: str) -> str:
    return VOICE_BY_ID.get(voice_id, VOICES[0])["name"]
