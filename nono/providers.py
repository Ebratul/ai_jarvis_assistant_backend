"""LLM providers. Every provider speaks the OpenAI-compatible chat API, so one client fits all.

Free-first: OpenRouter free models (+ the `openrouter/free` auto-router), Groq free tier,
Hugging Face Inference Providers, Gemini free tier, local Ollama — plus OpenAI (ChatGPT).
Calls automatically fall back to the next available model when one is rate limited or down.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("nono.providers")


@dataclass
class Provider:
    id: str
    label: str
    base_url: str
    key: str
    default_model: str
    free: bool
    signup: str
    preferred: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.key) or self.id == "ollama"

    def headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = f"Bearer {self.key}"
        if self.id == "openrouter":
            h["HTTP-Referer"] = "https://github.com/Ebratul/ai_jarvis_assistant_backend"
            h["X-Title"] = "NONO Assistant"
        return h


def build_providers() -> dict[str, Provider]:
    s = settings
    items = [
        Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", s.openrouter_api_key,
                 "openrouter/free", True, "https://openrouter.ai/keys"),
        Provider("groq", "Groq", "https://api.groq.com/openai/v1", s.groq_api_key,
                 "llama-3.3-70b-versatile", True, "https://console.groq.com/keys",
                 ("llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b", "llama-3.1-8b-instant")),
        Provider("huggingface", "Hugging Face", "https://router.huggingface.co/v1", s.hf_token,
                 "meta-llama/Llama-3.3-70B-Instruct", True, "https://huggingface.co/settings/tokens",
                 ("meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct", "meta-llama/Llama-3.1-8B-Instruct")),
        Provider("gemini", "Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", s.gemini_api_key,
                 "gemini-2.5-flash", True, "https://aistudio.google.com/apikey",
                 ("gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash")),
        Provider("openai", "OpenAI (ChatGPT)", "https://api.openai.com/v1", s.openai_api_key,
                 "gpt-4o-mini", False, "https://platform.openai.com/api-keys",
                 ("gpt-4o-mini", "gpt-4.1-mini", "gpt-4o")),
        Provider("ollama", "Ollama (local)", s.ollama_base_url.rstrip("/") + "/v1", "",
                 "llama3.2", True, "https://ollama.com/download"),
    ]
    return {p.id: p for p in items}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    attempts: list[dict[str, Any]]
    latency_ms: int


class ProviderHub:
    def __init__(self) -> None:
        self.providers = build_providers()
        self._models: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=8))
        self._ollama_ok: bool | None = None

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ catalogs
    async def list_models(self, provider_id: str, refresh: bool = False) -> list[dict[str, Any]]:
        p = self.providers[provider_id]
        cached = self._models.get(provider_id)
        if cached and not refresh and time.time() - cached[0] < 600:
            return cached[1]
        models: list[dict[str, Any]] = []
        try:
            models = await self._fetch_models(p)
        except Exception as exc:
            log.info("model list for %s unavailable: %s", provider_id, exc)
        if provider_id == "ollama":
            self._ollama_ok = bool(models)
        if not models and provider_id != "ollama":
            models = [{"id": m, "name": m, "free": p.free} for m in (p.preferred or (p.default_model,))]
        self._models[provider_id] = (time.time(), models)
        return models

    async def _fetch_models(self, p: Provider) -> list[dict[str, Any]]:
        if p.id == "openrouter":
            r = await self._client.get("https://openrouter.ai/api/v1/models", timeout=15)
            r.raise_for_status()
            out = [{"id": "openrouter/free", "name": "Auto · best available free model", "free": True, "context": None}]
            for m in r.json()["data"]:
                pricing = m.get("pricing") or {}
                is_free = m["id"].endswith(":free") or (pricing.get("prompt") == "0" and pricing.get("completion") == "0")
                if is_free and m["id"] != "openrouter/free":
                    out.append({"id": m["id"], "name": m.get("name", m["id"]), "free": True,
                                "context": m.get("context_length")})
            return out
        if p.id == "ollama":
            r = await self._client.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=3)
            r.raise_for_status()
            return [{"id": m["name"], "name": m["name"], "free": True} for m in r.json().get("models", [])]
        if not p.key:
            return []
        r = await self._client.get(f"{p.base_url}/models", headers=p.headers(), timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        skip = re.compile(r"whisper|tts|embed|guard|moderation|dall-e|image|audio|realtime|transcribe|search|vision-preview|imagen|veo|aqa", re.I)
        out = []
        for m in data:
            mid = m.get("id", "").removeprefix("models/")
            if not mid or skip.search(mid):
                continue
            if p.id == "openai" and not re.match(r"^(gpt-|o\d|chatgpt)", mid):
                continue
            out.append({"id": mid, "name": mid, "free": p.free})
        pref = {m: i for i, m in enumerate(p.preferred)}
        out.sort(key=lambda m: (pref.get(m["id"], 999), m["id"]))
        return out

    async def default_model_for(self, provider_id: str) -> str:
        p = self.providers[provider_id]
        models = await self.list_models(provider_id)
        ids = [m["id"] for m in models]
        if not ids or p.default_model in ids:
            return p.default_model
        for m in p.preferred:
            if m in ids:
                return m
        return ids[0]

    async def available_providers(self) -> list[str]:
        out = [pid for pid, p in self.providers.items() if p.key]
        if self._ollama_ok is None:
            await self.list_models("ollama", refresh=True)
        if self._ollama_ok:
            out.append("ollama")
        return out

    async def summary(self) -> list[dict[str, Any]]:
        avail = set(await self.available_providers())
        return [
            {"id": p.id, "label": p.label, "configured": p.id in avail, "free": p.free,
             "default_model": p.default_model, "signup": p.signup}
            for p in self.providers.values()
        ]

    # ------------------------------------------------------------------ model resolution
    async def resolve(self, query: str, current_provider: str) -> tuple[str, str] | None:
        """Turn a spoken phrase ("groq", "gemma", "nemotron lightning", "gpt 4o") into (provider, model)."""
        q = query.lower().strip()
        q = re.sub(r"\b(model|the|to|use|switch|change|please|nono|provider|set|using)\b", " ", q)
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            return None
        avail = await self.available_providers()
        aliases = {"chatgpt": "openai", "gpt": "openai", "open ai": "openai", "hugging face": "huggingface",
                   "hf": "huggingface", "google": "gemini", "local": "ollama", "free": "openrouter",
                   "open router": "openrouter"}
        for pid, p in self.providers.items():
            names = {pid, p.label.lower()} | {a for a, t in aliases.items() if t == pid}
            if q in names and pid in avail:
                return pid, await self.default_model_for(pid)
        tokens = [t for t in re.split(r"[\s/:_.-]+", q) if t]
        best: tuple[float, str, str] | None = None
        order = [current_provider] + [p for p in avail if p != current_provider]
        for pid in order:
            if pid not in avail:
                continue
            provider_hint = any(t in (pid, self.providers[pid].label.lower()) for t in tokens)
            for m in await self.list_models(pid):
                mid = m["id"].lower()
                hits = sum(1 for t in tokens if t in mid)
                if not hits:
                    continue
                score = hits / len(tokens) + (0.3 if provider_hint else 0) + (0.05 if pid == current_provider else 0) - len(mid) / 1000
                if best is None or score > best[0]:
                    best = (score, pid, m["id"])
            if provider_hint and best and best[1] == pid:
                break
        if best and best[0] >= 0.5:
            return best[1], best[2]
        for t in tokens:  # provider name inside a longer phrase
            for pid in avail:
                if t == pid or t in aliases and aliases[t] == pid:
                    return pid, await self.default_model_for(pid)
        return None

    # ------------------------------------------------------------------ chat
    async def _candidates(self, provider_id: str, model: str) -> list[tuple[str, str]]:
        avail = await self.available_providers()
        cands: list[tuple[str, str]] = []
        if provider_id in self.providers:
            cands.append((provider_id, model or await self.default_model_for(provider_id)))
        if "openrouter" in avail:
            cands.append(("openrouter", "openrouter/free"))
            free = [m["id"] for m in await self.list_models("openrouter") if m["id"] != "openrouter/free"]
            cands += [("openrouter", m) for m in free[:2]]
        for pid in avail:
            if pid != provider_id and pid != "openrouter":
                cands.append((pid, await self.default_model_for(pid)))
        seen, out = set(), []
        for c in cands:
            if c not in seen and (c[0] in avail):
                seen.add(c)
                out.append(c)
        return out[:6]

    async def chat(self, messages: list[dict[str, str]], provider_id: str, model: str, *,
                   temperature: float = 0.6, max_tokens: int = 900) -> LLMResult:
        start = time.perf_counter()
        attempts: list[dict[str, Any]] = []
        candidates = await self._candidates(provider_id, model)
        if not candidates:
            raise LLMError("No LLM provider is configured. Add a free key (OpenRouter/Groq/Gemini/HF) in backend .env or run Ollama.")
        dead_providers: set[str] = set()
        for pid, mid in candidates:
            if pid in dead_providers:
                continue
            p = self.providers[pid]
            try:
                r = await self._client.post(
                    f"{p.base_url}/chat/completions", headers=p.headers(),
                    json={"model": mid, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                    timeout=httpx.Timeout(75, connect=8),
                )
                if r.status_code in (401, 403):
                    dead_providers.add(pid)
                    attempts.append({"provider": pid, "model": mid, "error": f"auth {r.status_code}"})
                    continue
                if r.status_code >= 400:
                    attempts.append({"provider": pid, "model": mid, "error": f"{r.status_code}: {r.text[:160]}"})
                    continue
                data = r.json()
                if "error" in data:
                    attempts.append({"provider": pid, "model": mid, "error": str(data["error"])[:160]})
                    continue
                msg = (data.get("choices") or [{}])[0].get("message") or {}
                content = (msg.get("content") or "").strip()
                if not content:
                    attempts.append({"provider": pid, "model": mid, "error": "empty response"})
                    continue
                attempts.append({"provider": pid, "model": data.get("model", mid), "ok": True})
                return LLMResult(content, pid, data.get("model", mid), attempts,
                                 int((time.perf_counter() - start) * 1000))
            except (httpx.HTTPError, ValueError) as exc:
                attempts.append({"provider": pid, "model": mid, "error": f"{type(exc).__name__}: {exc}"[:160]})
                await asyncio.sleep(0)
        log.warning("all LLM attempts failed: %s", attempts)
        raise LLMError("All models are busy or unavailable right now. " + "; ".join(
            f"{a['provider']}/{a['model']}: {a.get('error')}" for a in attempts[-3:]))
