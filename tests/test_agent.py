import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from nono.agent import Agent
from nono.app_context import Nono
from nono.providers import LLMResult


class FakeRag:
    def __init__(self):
        self.mem = {}
        self.online = True

    async def remember(self, text, **kw):
        mid = f"m{len(self.mem)}"
        self.mem[mid] = {"id": mid, "text": text, **kw}
        return self.mem[mid]

    async def update(self, mid, **kw):
        self.mem[mid].update({k: v for k, v in kw.items() if v is not None})
        return self.mem[mid]

    async def search(self, q, k=5):
        return [{"memory": {"id": "old", "text": "my favourite color is blue", "created_at": "2025-01-01T00:00:00",
                            "response": "Noted."}, "score": 0.9}]

    async def health(self):
        return {"ok": True}

    async def close(self):
        pass


def make(responses):
    nono = Nono()
    nono.rag = FakeRag()
    calls = []

    async def fake_chat(messages, provider, model, **kw):
        calls.append(messages)
        return LLMResult(responses.pop(0), "openrouter", "test/model", [], 5)

    nono.hub.chat = fake_chat
    return nono, Agent(nono), calls


@pytest.mark.asyncio
async def test_reminder_action_and_scheduler():
    nono, agent, calls = make([json.dumps({"reply": "Reminder set.", "actions": [
        {"tool": "set_reminder", "args": {"text": "call mom", "when": "in 1 minute"}}]})])
    res = await agent.handle("remind me to call mom in 1 minute", source="voice")
    assert res["reply"] == "Reminder set."
    assert res["actions"][0]["ok"] and res["actions"][0]["tool"] == "set_reminder"
    assert "favourite color is blue" in calls[0][0]["content"]  # RAG memory injected
    mem = nono.rag.mem[res["memory_id"]]
    assert mem["response"] == "Reminder set." and mem["tools"] == ["set_reminder"]
    fired = await nono.fire_due_reminders(datetime.now(timezone.utc) + timedelta(minutes=2))
    assert [f["text"] for f in fired] == ["call mom"]
    assert nono.db.list_reminders("pending") == []


@pytest.mark.asyncio
async def test_observe_tool_second_pass():
    nono, agent, calls = make([
        json.dumps({"reply": "", "actions": [{"tool": "get_time", "args": {}}]}),
        json.dumps({"reply": "It is ten o'clock.", "actions": []}),
    ])
    res = await agent.handle("what time is it")
    assert res["reply"] == "It is ten o'clock."
    assert len(calls) == 2 and "TOOL RESULTS" in calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_fast_path_switches_without_llm():
    nono, agent, calls = make([])

    async def fake_resolve(q, cur):
        return ("groq", "llama-3.3-70b-versatile") if "groq" in q else None

    nono.hub.resolve = fake_resolve
    res = await agent.handle("nono switch to groq")
    assert res["fast_path"] and nono.state["provider"] == "groq" and calls == []
    res = await agent.handle("change your voice to Nova")
    assert nono.state["voice"] == "nova" and "Nova" in res["reply"]
    res = await agent.handle("show my brain")
    assert res["client_actions"] == [{"tool": "show_view", "args": {"view": "brain"}}]


@pytest.mark.asyncio
async def test_client_actions_and_plain_text_reply():
    nono, agent, calls = make([
        json.dumps({"reply": "Opening YouTube.", "actions": [{"tool": "open_url", "args": {"url": "https://youtube.com"}}]}),
        "Sure thing, plain text answer.",
    ])
    res = await agent.handle("open youtube")
    assert res["client_actions"][0]["args"]["url"] == "https://youtube.com"
    res = await agent.handle("tell me a joke")
    assert res["reply"] == "Sure thing, plain text answer."
