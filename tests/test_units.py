from datetime import datetime
from zoneinfo import ZoneInfo

from nono.agent import parse_llm_json
from nono.router import route
from nono.timeparse import parse_when
from nono.voices import resolve_voice

TZ = ZoneInfo("Asia/Dhaka")
NOW = datetime(2026, 11, 14, 10, 0, tzinfo=TZ)


def test_router_control_commands():
    assert route("nono switch to groq").name == "switch_model"
    assert route("change the model to gemma").args["query"] == "gemma"
    assert route("মডেল পরিবর্তন করো groq").name == "switch_model"
    assert route("change your voice to Aria").name == "switch_voice"
    assert route("ভয়েস পরিবর্তন করো").name == "switch_voice"
    assert route("mute").args == {"muted": True}
    assert route("show my brain").args == {"view": "brain"}
    assert route("which model are you using?").name == "which_model"
    assert route("remind me to call mom at 5pm") is None
    assert route("use google to find the best biryani in dhaka") is None


def test_time_parsing():
    assert parse_when("in 10 minutes", TZ, NOW) == datetime(2026, 11, 14, 10, 10, tzinfo=TZ)
    assert parse_when("১০ মিনিট পরে", TZ, NOW) == datetime(2026, 11, 14, 10, 10, tzinfo=TZ)
    assert parse_when("tomorrow 5pm", TZ, NOW) == datetime(2026, 11, 15, 17, 0, tzinfo=TZ)
    assert parse_when("2026-11-20T08:30", TZ, NOW) == datetime(2026, 11, 20, 8, 30, tzinfo=TZ)
    assert parse_when("9am", TZ, NOW) == datetime(2026, 11, 15, 9, 0, tzinfo=TZ)  # already past → tomorrow
    assert parse_when("in 2 hours", TZ, NOW).hour == 12


def test_llm_json_parsing():
    r, a = parse_llm_json('```json\n{"reply": "Hi", "actions": [{"tool": "get_time", "args": {}}]}\n```')
    assert r == "Hi" and a[0]["tool"] == "get_time"
    r, a = parse_llm_json('<think>hmm</think>Sure! {"reply": "Ok", "actions": [{"tool": "nope"}]}')
    assert r == "Ok" and a == []
    r, a = parse_llm_json("Just plain text.")
    assert r == "Just plain text." and a == []


def test_voice_resolution():
    assert resolve_voice("switch to aria")["id"] == "aria"
    assert resolve_voice("use a female voice")["id"] == "aria"
    assert resolve_voice("voice 3")["id"] == "titan"
    assert resolve_voice("deep voice please")["id"] == "titan"
    assert resolve_voice("blah") is None
