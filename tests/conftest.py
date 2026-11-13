import os
import sys
import tempfile
from pathlib import Path

os.environ["NONO_DATA_DIR"] = tempfile.mkdtemp(prefix="nono-test-")
for k in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "HF_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY",
          "ELEVENLABS_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NONO_API_KEY"):
    os.environ[k] = ""
os.environ["OPENROUTER_API_KEY"] = "test-key"
os.environ["TIMEZONE"] = "Asia/Dhaka"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
