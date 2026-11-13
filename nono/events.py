"""WebSocket broadcast hub: reminders firing, Telegram messages, status changes → live HUD alerts."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("nono.events")


class EventHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def clients(self) -> int:
        return len(self._clients)

    async def publish(self, kind: str, **data: Any) -> dict[str, Any]:
        event = {"type": kind, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **data}
        if kind not in ("status", "ping", "state", "conversation"):
            self.recent.append(event)
        dead = []
        for ws in list(self._clients):
            try:
                await asyncio.wait_for(ws.send_json(event), timeout=5)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
        return event
