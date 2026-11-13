"""Optional Google Calendar sync (falls back to NONO's local calendar when not connected).

Connect once:  python scripts/google_auth.py   (needs data/credentials.json — OAuth desktop client)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("nono.gcal")
SCOPES = ["https://www.googleapis.com/auth/calendar"]
API = "https://www.googleapis.com/calendar/v3"


class GoogleCalendar:
    def __init__(self) -> None:
        self._creds = None

    @property
    def connected(self) -> bool:
        return settings.google_token_path.exists()

    def _token(self) -> str:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if self._creds is None:
            self._creds = Credentials.from_authorized_user_file(str(settings.google_token_path), SCOPES)
        if not self._creds.valid:
            self._creds.refresh(Request())
            settings.google_token_path.write_text(self._creds.to_json())
        return self._creds.token

    async def _headers(self) -> dict[str, str]:
        token = await asyncio.to_thread(self._token)
        return {"Authorization": f"Bearer {token}"}

    async def list_events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{API}/calendars/{settings.google_calendar_id}/events", headers=await self._headers(),
                            params={"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                                    "orderBy": "startTime", "maxResults": 50})
            r.raise_for_status()
        out = []
        for e in r.json().get("items", []):
            s = e.get("start", {})
            en = e.get("end", {})
            out.append({"id": e["id"], "title": e.get("summary", "(no title)"), "start": s.get("dateTime") or s.get("date"),
                        "end": en.get("dateTime") or en.get("date"), "location": e.get("location", ""),
                        "notes": e.get("description", ""), "source": "google"})
        return out

    async def add_event(self, title: str, start: datetime, end: datetime, location: str = "", notes: str = "") -> dict[str, Any]:
        body = {"summary": title, "location": location, "description": notes,
                "start": {"dateTime": start.isoformat(), "timeZone": settings.timezone},
                "end": {"dateTime": end.isoformat(), "timeZone": settings.timezone}}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{API}/calendars/{settings.google_calendar_id}/events", headers=await self._headers(), json=body)
            r.raise_for_status()
        return r.json()
