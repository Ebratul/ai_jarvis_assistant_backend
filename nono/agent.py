"""The NONO agent loop.

1. Store the instruction in the RAG brain (permanent, even if everything else fails)
2. Fast path: control commands (switch model/voice, mute, views) with no LLM call
3. Recall relevant past instructions (RAG) and recent conversation
4. LLM → JSON {reply, actions}; execute tools; for "observe" tools do a second pass
5. Attach the reply/tools/model to the memory, return everything to the UI
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

from .app_context import Nono
from .config import settings
from .providers import LLMError
from .router import route
from .tools import TOOLS, tool_prompt, voice_name
from .voices import VOICES, resolve_voice

log = logging.getLogger("nono.agent")

SYSTEM = """You are NONO, the personal AI assistant of {user} — like J.A.R.V.I.S.: loyal, sharp, calm, a little witty.
Your replies are SPOKEN aloud, so: 1-3 short sentences unless asked for detail, no markdown, no emojis, no lists, no URLs read out.
Reply in the user's language: English → English, Bangla (বাংলা) → Bangla, Banglish → Banglish.
Now: {now} ({tz}). Your active brain: {provider}/{model}. Your voice: {voice}.

Relevant things {user} told you in the past (from your permanent memory):
{memories}

You can act using tools. ALWAYS respond with exactly ONE JSON object and nothing else:
{{"reply": "<what you say>", "actions": [{{"tool": "<tool name>", "args": {{...}}}}]}}
Use "actions": [] if no tool is needed. Never invent tool results; if you call an information tool you will receive its results and answer afterwards.
For times use local ISO format YYYY-MM-DDTHH:MM (timezone {tz}) or relative phrases like "in 20 minutes".
Tools:
{tools}"""

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def parse_llm_json(content: str) -> tuple[str, list[dict[str, Any]]]:
    text = _THINK.sub("", content).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            reply = str(obj.get("reply") or obj.get("response") or obj.get("answer") or "").strip()
            actions = obj.get("actions") or []
            if isinstance(actions, dict):
                actions = [actions]
            actions = [a for a in actions if isinstance(a, dict) and a.get("tool") in TOOLS]
            return reply, actions
        except (json.JSONDecodeError, AttributeError):
            m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if m:
                return json.loads(f'"{m.group(1)}"'), []
    return text, []


class Agent:
    def __init__(self, ctx: Nono):
        self.ctx = ctx

    async def handle(self, text: str, *, source: str = "text", session: str = "default") -> dict[str, Any]:
        t0 = time.perf_counter()
        text = text.strip()
        ctx = self.ctx
        memory = await ctx.rag.remember(text, source=source)
        memory_id = memory["id"] if memory else None
        ctx.db.log(session, "user", text)

        result = await self._fast_path(text)
        if result is None:
            result = await self._llm_path(text, session, source, exclude=memory_id)

        result.update({
            "memory_id": memory_id,
            "memory": memory,
            "state": ctx.public_state(),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "source": source,
        })
        ctx.db.log(session, "assistant", result["reply"], result.get("model"))
        if memory_id:
            await ctx.rag.update(memory_id, response=result["reply"], model=result.get("model"),
                                 tools=[a["tool"] for a in result["actions"]], category=result.pop("_category", None))
        await ctx.events.publish("conversation", text=text, reply=result["reply"], source=source,
                                 model=result.get("model"), memory_id=memory_id)
        return result

    # ------------------------------------------------------------------ fast path
    async def _fast_path(self, text: str) -> dict[str, Any] | None:
        intent = route(text)
        if not intent:
            return None
        ctx = self.ctx
        out: dict[str, Any] = {"actions": [], "client_actions": [], "fast_path": True, "provider": "nono",
                               "model": "intent-router", "_category": "system"}
        if intent.name == "switch_model":
            resolved = await ctx.hub.resolve(intent.args["query"], ctx.state["provider"])
            if not resolved:
                avail = await ctx.hub.available_providers()
                out["reply"] = (f"I couldn't find a model matching \"{intent.args['query']}\". "
                                f"Available brains: {', '.join(avail) or 'none — add a free key'}.")
            else:
                ctx.set_state(provider=resolved[0], model=resolved[1])
                out["reply"] = f"Done. I'm now thinking with {resolved[0]}, model {resolved[1].split('/')[-1]}."
                out["actions"].append({"tool": "switch_model", "args": {}, "ok": True,
                                       "result": {"provider": resolved[0], "model": resolved[1]}, "kind": "act"})
        elif intent.name == "switch_voice":
            v = resolve_voice(intent.args["voice"])
            if not v:
                out["reply"] = "Which voice? I have " + ", ".join(x["name"] for x in VOICES) + "."
            else:
                ctx.set_state(voice=v["id"])
                out["reply"] = f"Voice changed. This is {v['name']}, at your service."
                out["actions"].append({"tool": "switch_voice", "args": {}, "ok": True, "result": {"voice": v["id"]}, "kind": "act"})
        elif intent.name == "which_model":
            out["reply"] = f"I'm running on {ctx.state['provider']}, model {ctx.state['model']}."
        elif intent.name == "list_voices":
            out["reply"] = "My voices are " + ", ".join(f"{v['name']}, {v['style'].lower()}" for v in VOICES) + "."
        elif intent.name == "set_mute":
            muted = bool(intent.args["muted"])
            ctx.set_state(muted=muted)
            out["reply"] = "Muted. I'll stay quiet." if muted else "I'm back. Voice is on."
            out["client_actions"].append({"tool": "set_mute", "args": {"muted": muted}})
        elif intent.name == "show_view":
            view = intent.args["view"]
            out["reply"] = "Opening your neural memory." if view == "brain" else "Back to the main console."
            out["client_actions"].append({"tool": "show_view", "args": {"view": view}})
        else:
            return None
        return out

    # ------------------------------------------------------------------ llm path
    def _system_prompt(self, recalled: list[dict[str, Any]]) -> str:
        st = self.ctx.state
        mem_lines = []
        for h in recalled:
            m = h["memory"]
            line = f"- [{m['created_at'][:10]}] \"{m['text'][:200]}\""
            if m.get("response"):
                line += f" → you answered: \"{m['response'][:160]}\""
            mem_lines.append(line)
        return SYSTEM.format(
            user=settings.user_name, now=datetime.now(settings.tz).strftime("%A %d %B %Y, %H:%M"),
            tz=settings.timezone, provider=st["provider"], model=st["model"], voice=voice_name(st["voice"]),
            memories="\n".join(mem_lines) or "- (nothing relevant yet)", tools=tool_prompt(),
        )

    async def _llm_path(self, text: str, session: str, source: str, exclude: str | None) -> dict[str, Any]:
        ctx = self.ctx
        recalled = [h for h in await ctx.rag.search(text, k=5) if h["memory"]["id"] != exclude][:4]
        history = ctx.db.history(session, 9)[:-1]  # last turns, minus the message we just logged
        messages = [{"role": "system", "content": self._system_prompt(recalled)}]
        for h in history[-8:]:
            messages.append({"role": h["role"], "content": h["text"][:1500]})
        messages.append({"role": "user", "content": text})

        out: dict[str, Any] = {"actions": [], "client_actions": [], "fast_path": False,
                               "recalled": [h["memory"]["id"] for h in recalled]}
        try:
            res = await ctx.hub.chat(messages, ctx.state["provider"], ctx.state["model"])
        except LLMError as exc:
            out.update(reply=f"My language models are unavailable at the moment, but I saved your instruction. {exc}",
                       provider=None, model=None, error=str(exc))
            return out
        reply, actions = parse_llm_json(res.content)
        out.update(provider=res.provider, model=res.model, attempts=res.attempts)

        observations = []
        for action in actions[:4]:
            executed = await self._execute(action, source)
            out["actions"].append(executed)
            if executed["kind"] == "client" and executed["ok"]:
                out["client_actions"].append({"tool": action["tool"], "args": action.get("args") or {}})
            if executed["kind"] == "observe" or not executed["ok"]:
                observations.append(executed)

        if observations:
            messages += [
                {"role": "assistant", "content": res.content},
                {"role": "user", "content": "TOOL RESULTS:\n" + json.dumps(
                    [{"tool": o["tool"], "ok": o["ok"], "result": o["result"]} for o in observations],
                    ensure_ascii=False, default=str)[:6000] +
                    "\nNow answer me using these results. Respond with the JSON object and \"actions\": []."},
            ]
            try:
                res2 = await ctx.hub.chat(messages, res.provider, res.model)
                reply2, _ = parse_llm_json(res2.content)
                reply = reply2 or reply
                out.update(provider=res2.provider, model=res2.model)
            except LLMError:
                reply = reply or "I fetched the information but couldn't summarise it right now."
        out["reply"] = reply or "Done."
        return out

    async def _execute(self, action: dict[str, Any], source: str) -> dict[str, Any]:
        tool = TOOLS[action["tool"]]
        args = dict(action.get("args") or {})
        if tool.name == "set_reminder":
            args["_source"] = source
        try:
            result = await tool.run(self.ctx, args)
            ok = "error" not in result
        except Exception as exc:
            log.warning("tool %s failed: %s", tool.name, exc)
            result, ok = {"error": str(exc)}, False
        args.pop("_source", None)
        return {"tool": tool.name, "args": args, "ok": ok, "result": result, "kind": tool.kind}
