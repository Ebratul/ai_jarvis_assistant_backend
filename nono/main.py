"""NONO backend — FastAPI app (REST + WebSocket)."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .agent import Agent
from .app_context import Nono
from .config import settings
from .stt import STTUnavailable, transcribe
from .timeparse import parse_when
from .tools import system_snapshot, upcoming_events
from .voices import TTSUnavailable, public_voices

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nono.api")

nono: Nono | None = None
agent: Agent | None = None


def ctx() -> Nono:
    if nono is None:
        raise HTTPException(503, "NONO is starting")
    return nono


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    global nono, agent
    nono = Nono()
    agent = Agent(nono)

    async def on_telegram(text: str, meta: dict[str, Any]) -> str:
        res = await agent.handle(text, source="telegram", session=f"tg:{meta['chat_id']}")
        await nono.events.publish("telegram", text=text, reply=res["reply"], sender=meta.get("from"))
        return res["reply"]

    nono.start(on_telegram)
    asyncio.create_task(nono.rag.health())
    log.info("NONO online · provider=%s model=%s voice=%s", nono.state["provider"], nono.state["model"], nono.state["voice"])
    yield
    await nono.stop()
    nono = None


app = FastAPI(title="NONO Backend", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])


def auth(x_nono_key: str | None = Header(default=None), key: str | None = Query(default=None)) -> None:
    if settings.api_key and (x_nono_key or key) != settings.api_key:
        raise HTTPException(401, "invalid NONO key")


api = [Depends(auth)]


# ----------------------------------------------------------------------------- schemas
class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    source: str = "text"
    session: str = "default"


class StatePatch(BaseModel):
    provider: str | None = None
    model: str | None = None
    voice: str | None = None
    language: str | None = None
    muted: bool | None = None
    city: str | None = None


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = None


class ReminderIn(BaseModel):
    text: str
    when: str
    repeat: str | None = None


class EventIn(BaseModel):
    title: str
    start: str
    end: str | None = None
    location: str = ""
    notes: str = ""


class TelegramIn(BaseModel):
    text: str


# ----------------------------------------------------------------------------- core
@app.get("/api/health")
async def health() -> dict[str, Any]:
    n = ctx()
    return {"ok": True, "service": "nono-backend", "version": __version__, "rag": n.rag.online,
            "state": n.public_state()}


@app.get("/api/state", dependencies=api)
async def get_state() -> dict[str, Any]:
    return ctx().public_state()


@app.patch("/api/state", dependencies=api)
async def patch_state(body: StatePatch) -> dict[str, Any]:
    n = ctx()
    changes = body.model_dump(exclude_none=True)
    if "provider" in changes:
        if changes["provider"] not in n.hub.providers:
            raise HTTPException(400, "unknown provider")
        if "model" not in changes:
            changes["model"] = await n.hub.default_model_for(changes["provider"])
    n.set_state(**changes)
    return n.public_state()


@app.post("/api/chat", dependencies=api)
async def chat(body: ChatIn) -> dict[str, Any]:
    assert agent is not None
    return await agent.handle(body.text, source=body.source, session=body.session)


@app.get("/api/providers", dependencies=api)
async def providers() -> dict[str, Any]:
    return {"providers": await ctx().hub.summary()}


@app.get("/api/models", dependencies=api)
async def models(provider: str = "openrouter", refresh: bool = False) -> dict[str, Any]:
    n = ctx()
    if provider not in n.hub.providers:
        raise HTTPException(404, "unknown provider")
    return {"provider": provider, "models": await n.hub.list_models(provider, refresh)}


# ----------------------------------------------------------------------------- voice
@app.get("/api/voices", dependencies=api)
async def voices() -> dict[str, Any]:
    return {"voices": public_voices(), "active": ctx().state["voice"], "elevenlabs": ctx().tts.enabled}


@app.post("/api/tts", dependencies=api)
async def tts(body: TTSIn) -> Response:
    n = ctx()
    try:
        audio = await n.tts.synthesize(body.text, body.voice or n.state["voice"])
    except TTSUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/stt", dependencies=api)
async def stt(file: UploadFile = File(...), language: str | None = Form(default=None)) -> dict[str, str]:
    try:
        return await transcribe(await file.read(), file.filename or "audio.webm",
                                file.content_type or "audio/webm", language)
    except STTUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


# ----------------------------------------------------------------------------- automation
@app.get("/api/system", dependencies=api)
async def system() -> dict[str, Any]:
    return system_snapshot()


@app.get("/api/reminders", dependencies=api)
async def reminders(status: str | None = "pending") -> dict[str, Any]:
    return {"reminders": ctx().db.list_reminders(status or None, 100)}


@app.post("/api/reminders", dependencies=api)
async def add_reminder(body: ReminderIn) -> dict[str, Any]:
    try:
        due = parse_when(body.when, settings.tz)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(400, f"cannot understand time: {exc}") from exc
    rem = ctx().db.add_reminder(body.text, due, body.repeat, "ui")
    await ctx().events.publish("reminder_set", reminder=rem)
    return rem


@app.delete("/api/reminders/{rid}", dependencies=api)
async def delete_reminder(rid: str) -> dict[str, Any]:
    return {"cancelled": len(ctx().db.cancel_reminder(rid))}


@app.get("/api/events", dependencies=api)
async def events(days: int = Query(14, ge=1, le=365)) -> dict[str, Any]:
    start = datetime.now(settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"events": await upcoming_events(ctx(), start, start + timedelta(days=days)),
            "google": ctx().gcal.connected}


@app.post("/api/events", dependencies=api)
async def add_event(body: EventIn) -> dict[str, Any]:
    from .tools import add_event as add_event_tool
    return await add_event_tool(ctx(), body.model_dump(exclude_none=True))


@app.delete("/api/events/{eid}", dependencies=api)
async def delete_event(eid: str) -> dict[str, Any]:
    return {"deleted": ctx().db.delete_event(eid)}


@app.get("/api/telegram", dependencies=api)
async def telegram_status() -> dict[str, Any]:
    return ctx().telegram.status()


@app.post("/api/telegram/send", dependencies=api)
async def telegram_send(body: TelegramIn) -> dict[str, Any]:
    try:
        return await ctx().telegram.send(body.text)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/integrations", dependencies=api)
async def integrations() -> dict[str, Any]:
    n = ctx()
    rag = await n.rag.health()
    return {
        "telegram": n.telegram.status(),
        "google_calendar": {"connected": n.gcal.connected},
        "rag": {"online": bool(rag), **(rag or {})},
        "tts": {"elevenlabs": n.tts.enabled, "model": settings.elevenlabs_model},
        "stt": {"server": bool(settings.groq_api_key or settings.openai_api_key)},
        "providers": await n.hub.summary(),
    }


@app.get("/api/alerts", dependencies=api)
async def alerts() -> dict[str, Any]:
    return {"alerts": list(ctx().events.recent)[::-1]}


@app.get("/api/conversation", dependencies=api)
async def conversation(limit: int = 30) -> dict[str, Any]:
    return {"messages": ctx().db.recent_conversation(limit)}


# ----------------------------------------------------------------------------- brain proxy
BRAIN_GET = {"graph", "stats", "memories", "export"}


@app.get("/api/brain/{path:path}", dependencies=api)
async def brain_get(path: str, request: Request) -> Response:
    root = path.split("/")[0]
    if root not in BRAIN_GET or path.count("/") > 1:
        raise HTTPException(404, "unknown brain endpoint")
    params = {k: v for k, v in request.query_params.items() if k != "key"}
    try:
        if root == "export":
            r = await ctx().rag.proxy("GET", "/export")
            return StreamingResponse(iter([r.content]), media_type="application/x-ndjson",
                                     headers={"Content-Disposition": 'attachment; filename="nono-brain.jsonl"'})
        r = await ctx().rag.proxy("GET", f"/{path}", params=params)
    except Exception as exc:
        raise HTTPException(503, f"brain offline: {exc}") from exc
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.post("/api/brain/search", dependencies=api)
async def brain_search(request: Request) -> Response:
    r = await ctx().rag.proxy("POST", "/search", content=await request.body(),
                              headers={"Content-Type": "application/json"})
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.patch("/api/brain/memories/{mid}", dependencies=api)
async def brain_patch(mid: str, request: Request) -> Response:
    r = await ctx().rag.proxy("PATCH", f"/memories/{mid}", content=await request.body(),
                              headers={"Content-Type": "application/json"})
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.delete("/api/brain/memories/{mid}", dependencies=api)
async def brain_delete(mid: str) -> Response:
    r = await ctx().rag.proxy("DELETE", f"/memories/{mid}")
    return Response(r.content, status_code=r.status_code, media_type="application/json")


# ----------------------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(websocket: WebSocket, key: str | None = None) -> None:
    if settings.api_key and key != settings.api_key:
        await websocket.close(code=4401)
        return
    n = ctx()
    await n.events.connect(websocket)
    try:
        await websocket.send_json({"type": "hello", "state": n.public_state(), "telegram": n.telegram.status()})
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong", "system": system_snapshot()})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        n.events.disconnect(websocket)


def run() -> None:
    import uvicorn

    uvicorn.run("nono.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()
