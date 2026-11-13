"""Instant intent router: handles control commands without spending LLM quota (English + Bangla)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SWITCH = re.compile(r"\b(switch|change|use|set|select|swap|go)\b|পরিবর্তন|বদলাও|বদলে|চেঞ্জ|ব্যবহার", re.I)
MODEL_WORD = re.compile(r"\b(model|provider|llm|ai engine)\b|মডেল", re.I)
PROVIDER_WORD = re.compile(r"\b(openrouter|open router|groq|hugging ?face|hf|gemini|google|openai|chatgpt|gpt|ollama|local|llama|gemma|nemotron|qwen|deepseek|mistral|claude)\b", re.I)
VOICE_WORD = re.compile(r"\b(voice|accent)\b|ভয়েস|ভয়েস|কণ্ঠ|গলা", re.I)
WHICH_MODEL = re.compile(r"\b(which|what|current)\b.{0,20}\b(model|ai)\b.{0,20}\b(using|use|running|are you|is this)\b|কোন মডেল", re.I)
LIST_VOICES = re.compile(r"\b(list|show|what|which)\b.{0,15}\bvoices\b|কি কি ভয়েস", re.I)
MUTE = re.compile(r"^\W*(nono\W+)?(mute|be quiet|silence|stop talking|quiet|চুপ কর[োো]?|মিউট)\W*$", re.I)
UNMUTE = re.compile(r"^\W*(nono\W+)?(unmute|speak again|talk again|আনমিউট|কথা বল[োো]?)\W*$", re.I)
SHOW_BRAIN = re.compile(r"\b(show|open|display)\b.{0,20}\b(brain|memory|memories|neural|network)\b|ব্রেইন দেখাও|মেমোরি দেখাও", re.I)
SHOW_HUD = re.compile(r"^\W*(nono\W+)?(go )?(back|home|main screen|dashboard|hud)\W*$", re.I)


@dataclass
class Intent:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


def route(text: str) -> Intent | None:
    t = text.strip()
    low = t.lower()
    words = re.findall(r"\w+", low)
    if MUTE.match(low):
        return Intent("set_mute", {"muted": True})
    if UNMUTE.match(low):
        return Intent("set_mute", {"muted": False})
    if SHOW_BRAIN.search(low) and len(words) <= 9:
        return Intent("show_view", {"view": "brain"})
    if SHOW_HUD.match(low):
        return Intent("show_view", {"view": "hud"})
    if LIST_VOICES.search(low):
        return Intent("list_voices")
    if WHICH_MODEL.search(low):
        return Intent("which_model")
    if VOICE_WORD.search(low) and SWITCH.search(low) and len(words) <= 12:
        return Intent("switch_voice", {"voice": low})
    if SWITCH.search(low) and len(words) <= 12 and (MODEL_WORD.search(low) or (PROVIDER_WORD.search(low) and len(words) <= 6)):
        target = re.sub(r"^.*?\b(to|into|with)\b", "", low, count=1) if re.search(r"\b(to|into|with)\b", low) else low
        target = re.sub(r"\b(switch|change|use|set|select|swap|go|nono|please|now|the|your|model|provider)\b|পরিবর্তন করো|পরিবর্তন|বদলাও|মডেল|ব্যবহার করো", " ", target)
        return Intent("switch_model", {"query": re.sub(r"\s+", " ", target).strip()})
    return None
