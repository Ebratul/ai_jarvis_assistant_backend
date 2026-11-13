"""The NONO container: shared services + persistent assistant state + reminder scheduler."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import settings
from .db import Database
from .events import EventHub
from .gcal import GoogleCalendar
from .providers import ProviderHub
from .rag_client import RagClient
from .telegram import Telegram
from .tools import voice_name
from .voices import TTS, VOICE_BY_ID

log = logging.getLogger("nono")

REPEAT = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(weeks=1)}


class Nono:
    def __init__(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.hub = ProviderHub()
        self.rag = RagClient()
        self.tts = TTS()
        self.events = EventHub()
        self.telegram = Telegram(self.db)
        self.gcal = GoogleCalendar()
        saved = self.db.get_setting("state", {}) or {}
        self.state: dict[str, Any] = {
            "provider": settings.default_provider,
            "model": settings.default_model,
            "voice": settings.default_voice if settings.default_voice in VOICE_BY_ID else "prime",
            "language": settings.language,
            "muted": False,
            "city": "Dhaka",
            **saved,
        }
        if self.state["provider"] not in self.hub.providers:
            self.state["provider"] = "openrouter"
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------ state
    def set_state(self, **changes: Any) -> dict[str, Any]:
        changes = {k: v for k, v in changes.items() if v is not None and k in self.state}
        if "voice" in changes and changes["voice"] not in VOICE_BY_ID:
            changes.pop("voice")
        self.state.update(changes)
        self.db.set_setting("state", self.state)
        try:
            asyncio.get_running_loop().create_task(self.events.publish("state", state=self.public_state()))
        except RuntimeError:
            pass
        return self.state

    def public_state(self) -> dict[str, Any]:
        return {**self.state, "voice_name": voice_name(self.state["voice"]), "user_name": settings.user_name,
                "timezone": settings.timezone, "tts": self.tts.enabled}

    # ------------------------------------------------------------------ lifecycle
    def start(self, telegram_handler) -> None:
        self._tasks.append(asyncio.create_task(self._scheduler()))
        self.telegram.start(telegram_handler)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await self.telegram.stop()
        await asyncio.gather(self.hub.close(), self.rag.close(), self.tts.close(), return_exceptions=True)

    async def _scheduler(self) -> None:
        while True:
            try:
                await self.fire_due_reminders()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover
                log.warning("scheduler error: %s", exc)
            await asyncio.sleep(10)

    async def fire_due_reminders(self, now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        fired = []
        for r in self.db.due_reminders(now):
            await self.events.publish("reminder", id=r["id"], text=r["text"], due_at=r["due_at"])
            if self.telegram.enabled and self.telegram.chat_id:
                try:
                    await self.telegram.send(f"⏰ Reminder: {r['text']}")
                except Exception as exc:
                    log.warning("telegram reminder failed: %s", exc)
            step = REPEAT.get((r["repeat"] or "").lower())
            if step:
                nxt = datetime.fromisoformat(r["due_at"])
                while nxt <= now:
                    nxt += step
                self.db.update_reminder(r["id"], due_at=nxt.isoformat(timespec="seconds"), fired_at=now.isoformat(timespec="seconds"))
            else:
                self.db.update_reminder(r["id"], status="fired", fired_at=now.isoformat(timespec="seconds"))
            fired.append(r)
        return fired
