"""Telegram integration: NONO can message you, and you can command NONO from Telegram.

Pairing: the HUD shows a 6-digit code; send `/pair <code>` to your bot once. Only the paired
chat can control NONO (or the chat in TELEGRAM_CHAT_ID).
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any, Awaitable, Callable

import httpx

from .config import settings
from .db import Database

log = logging.getLogger("nono.telegram")

Handler = Callable[[str, dict[str, Any]], Awaitable[str]]


class Telegram:
    def __init__(self, db: Database):
        self.db = db
        self.token = settings.telegram_bot_token
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(40, connect=8))
        self.pair_code = f"{secrets.randbelow(10**6):06d}"
        self.bot_username: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def chat_id(self) -> str | None:
        return settings.telegram_chat_id or self.db.get_setting("telegram_chat_id")

    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "paired": bool(self.chat_id), "bot": self.bot_username,
                "pair_code": self.pair_code if self.enabled and not self.chat_id else None}

    async def _api(self, method: str, **payload: Any) -> Any:
        r = await self._client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    async def send(self, text: str, chat_id: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Telegram is not configured (TELEGRAM_BOT_TOKEN missing)")
        target = chat_id or self.chat_id
        if not target:
            raise RuntimeError(f"Telegram not paired yet. Send '/pair {self.pair_code}' to your bot.")
        res = await self._api("sendMessage", chat_id=target, text=text[:4000])
        return {"message_id": res["message_id"], "chat_id": target}

    def start(self, handler: Handler) -> None:
        if self.enabled and not self._task:
            self._task = asyncio.create_task(self._poll(handler))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._client.aclose()

    async def _poll(self, handler: Handler) -> None:
        try:
            me = await self._api("getMe")
            self.bot_username = me.get("username")
            log.info("Telegram bot @%s online", self.bot_username)
        except Exception as exc:
            log.warning("Telegram disabled: %s", exc)
            return
        offset = self.db.get_setting("telegram_offset", 0)
        while True:
            try:
                updates = await self._api("getUpdates", offset=offset, timeout=30, allowed_updates=["message"])
                for u in updates:
                    offset = u["update_id"] + 1
                    self.db.set_setting("telegram_offset", offset)
                    await self._handle_update(u, handler)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("telegram poll error: %s", exc)
                await asyncio.sleep(5)

    async def _handle_update(self, update: dict[str, Any], handler: Handler) -> None:
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        sender = (msg.get("from") or {}).get("first_name", "someone")
        if not text or not chat_id:
            return
        if text.startswith("/pair"):
            code = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if code == self.pair_code:
                self.db.set_setting("telegram_chat_id", chat_id)
                await self.send("✅ Paired with NONO. Send me any instruction.", chat_id)
            else:
                await self.send("❌ Wrong pairing code. Check the code in NONO → Settings.", chat_id)
            return
        if text.startswith("/start"):
            await self.send(f"Hi {sender}! I'm NONO. Send '/pair <code>' using the code shown in NONO settings.", chat_id)
            return
        if chat_id != str(self.chat_id):
            await self.send("This NONO is private. Pair first with /pair <code>.", chat_id)
            return
        try:
            reply = await handler(text, {"chat_id": chat_id, "from": sender})
        except Exception as exc:  # pragma: no cover
            reply = f"Sorry, something failed: {exc}"
        if reply:
            await self.send(reply, chat_id)
