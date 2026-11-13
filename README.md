# NONO · Backend

The brain stem of **NONO**, a personal voice AI assistant. NONO understands your voice instructions, thinks with free AI models, takes real actions (reminders, calendar, Telegram, weather, web search) and stores every instruction in a permanent 3D neural memory.

| Repo | Role |
|---|---|
| [ai_jarvis_assistant_frontend](https://github.com/Ebratul/ai_jarvis_assistant_frontend) | 3D HUD, voice I/O, neural-memory explorer |
| **ai_jarvis_assistant_backend** (this repo) | agent loop, model providers, tools, voices, Telegram, scheduler |
| [ai_jarvis_assistant_rag_agent](https://github.com/Ebratul/ai_jarvis_assistant_rag_agent) | permanent memory, semantic search, neural graph |

## Architecture
```
Frontend :5173 ──REST/WS──► Backend :8000 ──HTTP──► RAG agent :8100
                              │
   ┌──────────────────────────┼─────────────────────────────────────┐
   │ 1. store instruction in RAG (permanent)                         │
   │ 2. instant intent router: switch model/voice, mute, views (EN+BN)│
   │ 3. recall related past instructions (RAG) + recent conversation │
   │ 4. LLM → JSON {reply, actions} (works on ANY model, even free)  │
   │ 5. run tools → second pass for info tools → final spoken reply  │
   │ 6. attach reply/tools/model back onto the memory                │
   └──────────────────────────────────────────────────────────────────┘
```

## Features
* **Free models with automatic fallback:** OpenRouter (`openrouter/free` plus every `:free` model, listed live), Groq, Hugging Face, Google Gemini, OpenAI (ChatGPT) and local Ollama. If a model is rate-limited, NONO retries the next one on its own.
* **Model switching by voice:** "Nono, switch to groq", "use gemma", "change the model to nemotron lightning", "মডেল পরিবর্তন করো gemini". These are handled instantly and cost no LLM quota.
* **Five voices** (ElevenLabs, all verified on the free plan):

  | # | Persona | Voice | Style |
  |---|---|---|---|
  | 1 | Nono Prime | George | warm British butler (default) |
  | 2 | Aria | Alice | confident British female |
  | 3 | Titan | Adam | deep, cinematic |
  | 4 | Nova | Jessica | bright, playful |
  | 5 | Echo | Daniel | calm news anchor |

  Switch with "change your voice to Aria", "use a deep voice" or "voice 3".
* **Tools:** reminders (one-off or hourly/daily/weekly), calendar (Google Calendar or built-in), Telegram send and receive, weather (Open-Meteo), web and Wikipedia search, time, system status, memory recall, notes, open URL, mute, and switching views.
* **Telegram remote control:** chat with NONO from your phone. Reminders are also delivered to Telegram.
* **Live events over WebSocket:** reminders firing, Telegram messages and state changes all show up in the HUD immediately.
* **Bangla + English + Banglish:** NONO replies in whatever language you use. Times like "১০ মিনিট পরে" and "কাল সকাল ৯টায়" are understood.

## Quick start
```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt   # or python -m venv + pip
cp .env.example .env        # add your keys (see below)
.venv/bin/python -m nono    # → http://localhost:8000/docs
```
Run the whole stack (RAG + backend + frontend side by side): `./scripts/run_all.sh`.
Docker: `docker compose up --build` (with the three repos cloned next to each other).

### Keys (all optional, free tiers available)
| Variable | What it unlocks | Where |
|---|---|---|
| `OPENROUTER_API_KEY` | free models (50 requests/day on the free tier, 1000/day after a one-time $10 credit) | openrouter.ai/keys |
| `GROQ_API_KEY` | very fast free Llama/Qwen/GPT-OSS **and** Whisper speech-to-text | console.groq.com/keys |
| `GEMINI_API_KEY` | Gemini 2.5 Flash free tier | aistudio.google.com/apikey |
| `HF_TOKEN` | Hugging Face Inference Providers | huggingface.co/settings/tokens |
| `OPENAI_API_KEY` | ChatGPT models | platform.openai.com |
| *(none)* Ollama | 100% local and offline: `ollama pull llama3.2` | ollama.com |
| `ELEVENLABS_API_KEY` | premium voices (free plan ≈ 10k credits/month; falls back to the browser voice when they run out) | elevenlabs.io |
| `TELEGRAM_BOT_TOKEN` | Telegram control. Create a bot with @BotFather, then send `/pair <code>` (the code is shown in NONO → Settings → Integrations) | t.me/BotFather |

> **Tip:** add a free Groq key. With OpenRouter plus Groq plus Gemini, NONO almost never runs out of free requests.

### Google Calendar (optional)
1. In Google Cloud Console, enable the Calendar API and create an OAuth client ID of type **Desktop app**.
2. Save the downloaded JSON as `data/credentials.json`.
3. Install the extra dependency and run the auth script: `uv pip install google-auth-oauthlib && .venv/bin/python scripts/google_auth.py`

Without this step, NONO uses its own local calendar, so everything still works.

## API
| | |
|---|---|
| `POST /api/chat` `{text, source, session}` | main agent entry (voice/text) |
| `GET/PATCH /api/state` | provider, model, voice, language, muted, city |
| `GET /api/providers` · `GET /api/models?provider=` | catalog (live) |
| `GET /api/voices` · `POST /api/tts` · `POST /api/stt` | voice I/O |
| `GET/POST/DELETE /api/reminders` · `/api/events` | agenda |
| `GET /api/telegram` · `POST /api/telegram/send` | Telegram |
| `GET /api/system` · `/api/integrations` · `/api/alerts` | HUD data |
| `GET /api/brain/{graph,stats,memories,export}` | proxy to the RAG agent |
| `WS /ws` | live events |

Set `NONO_API_KEY` to require an `X-Nono-Key` header (use this before exposing NONO beyond localhost).

## Tests
```bash
uv pip install -p .venv/bin/python -r requirements-dev.txt && .venv/bin/python -m pytest -q
```
