"""Five NONO voice personas (ElevenLabs default voices verified on the free plan) + TTS proxy."""
from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("nono.voices")

VOICES: list[dict[str, Any]] = [
    {"id": "prime", "name": "Nono Prime", "eleven_id": "JBFqnCBsd6RMkjVDRZzb", "base": "George",
     "gender": "male", "style": "Warm British butler, the classic assistant", "color": "#38bdf8",
     "aliases": ["prime", "default", "jarvis", "george", "butler", "british male", "male", "one", "1"],
     "browser": {"pitch": 0.9, "rate": 1.0, "lang": "en-GB", "prefer": ["Daniel", "Google UK English Male", "Male"]}},
    {"id": "aria", "name": "Aria", "eleven_id": "Xb7hH8MSUJpSbSDYk0k2", "base": "Alice",
     "gender": "female", "style": "Confident, clear British female", "color": "#f472b6",
     "aliases": ["aria", "alice", "female", "woman", "girl", "british female", "lady", "two", "2"],
     "browser": {"pitch": 1.1, "rate": 1.0, "lang": "en-GB", "prefer": ["Google UK English Female", "Serena", "Female"]}},
    {"id": "titan", "name": "Titan", "eleven_id": "pNInz6obpgDQGcFmaJgB", "base": "Adam",
     "gender": "male", "style": "Deep, powerful, cinematic", "color": "#f59e0b",
     "aliases": ["titan", "adam", "deep", "bass", "heavy", "three", "3"],
     "browser": {"pitch": 0.6, "rate": 0.92, "lang": "en-US", "prefer": ["Google US English", "Alex", "Male"]}},
    {"id": "nova", "name": "Nova", "eleven_id": "cgSgspJ2msm6clMCkdW9", "base": "Jessica",
     "gender": "female", "style": "Bright, playful, energetic", "color": "#a78bfa",
     "aliases": ["nova", "jessica", "playful", "young", "cute", "four", "4"],
     "browser": {"pitch": 1.3, "rate": 1.06, "lang": "en-US", "prefer": ["Samantha", "Google US English", "Female"]}},
    {"id": "echo", "name": "Echo", "eleven_id": "onwK4e9ZLuTAKqWW03F9", "base": "Daniel",
     "gender": "male", "style": "Calm news-anchor delivery", "color": "#34d399",
     "aliases": ["echo", "daniel", "anchor", "news", "calm", "five", "5"],
     "browser": {"pitch": 1.0, "rate": 0.96, "lang": "en-GB", "prefer": ["Arthur", "Google UK English Male"]}},
]
VOICE_BY_ID = {v["id"]: v for v in VOICES}


def public_voices() -> list[dict[str, Any]]:
    return [{k: v[k] for k in ("id", "name", "base", "gender", "style", "color", "browser")} for v in VOICES]


def resolve_voice(phrase: str) -> dict[str, Any] | None:
    t = phrase.lower()
    words = set(re.findall(r"[a-z0-9]+", t))
    for v in VOICES:  # exact names first
        if v["id"] in words or v["name"].lower() in t:
            return v
    for v in VOICES:
        for a in v["aliases"]:
            if (" " in a and a in t) or a in words:
                return v
    if "মেয়ে" in t or "মহিলা" in t:
        return VOICE_BY_ID["aria"]
    if "ছেলে" in t or "পুরুষ" in t:
        return VOICE_BY_ID["prime"]
    return None


class TTSUnavailable(RuntimeError):
    pass


class TTS:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=8))
        self._cache: OrderedDict[str, bytes] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return bool(settings.elevenlabs_api_key)

    async def close(self) -> None:
        await self._client.aclose()

    async def synthesize(self, text: str, voice_id: str) -> bytes:
        if not self.enabled:
            raise TTSUnavailable("ELEVENLABS_API_KEY not set")
        voice = VOICE_BY_ID.get(voice_id, VOICES[0])
        text = text.strip()[:2500]
        key = hashlib.sha1(f"{voice['id']}|{settings.elevenlabs_model}|{text}".encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        r = await self._client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice['eleven_id']}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": settings.elevenlabs_api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": settings.elevenlabs_model,
                  "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15, "use_speaker_boost": True}},
        )
        if r.status_code != 200:
            log.warning("ElevenLabs %s: %s", r.status_code, r.text[:200])
            raise TTSUnavailable(f"ElevenLabs error {r.status_code}")
        audio = r.content
        self._cache[key] = audio
        while len(self._cache) > 64:
            self._cache.popitem(last=False)
        return audio
