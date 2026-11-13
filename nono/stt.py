"""Server-side speech-to-text (used when the browser has no Web Speech API, e.g. Firefox)."""
from __future__ import annotations

import httpx

from .config import settings


class STTUnavailable(RuntimeError):
    pass


async def transcribe(audio: bytes, filename: str, content_type: str, language: str | None = None) -> dict[str, str]:
    if settings.groq_api_key:
        url, key, model, engine = ("https://api.groq.com/openai/v1/audio/transcriptions", settings.groq_api_key,
                                   "whisper-large-v3-turbo", "groq-whisper")
    elif settings.openai_api_key:
        url, key, model, engine = ("https://api.openai.com/v1/audio/transcriptions", settings.openai_api_key,
                                   "whisper-1", "openai-whisper")
    else:
        raise STTUnavailable("Add GROQ_API_KEY (free) or OPENAI_API_KEY for server speech-to-text")
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language.split("-")[0]
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(url, headers={"Authorization": f"Bearer {key}"},
                         files={"file": (filename, audio, content_type)}, data=data)
    if r.status_code != 200:
        raise STTUnavailable(f"{engine} error {r.status_code}: {r.text[:160]}")
    return {"text": r.json().get("text", "").strip(), "engine": engine}
