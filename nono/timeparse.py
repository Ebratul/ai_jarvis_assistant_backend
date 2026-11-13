"""Turn "in 10 minutes", "tomorrow 5pm", "2026-11-15T17:00", "১০ মিনিট পরে" into aware datetimes."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as dparser

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_UNITS = {
    "s": "seconds", "sec": "seconds", "second": "seconds", "সেকেন্ড": "seconds",
    "m": "minutes", "min": "minutes", "mins": "minutes", "minute": "minutes", "মিনিট": "minutes",
    "h": "hours", "hr": "hours", "hrs": "hours", "hour": "hours", "ঘণ্টা": "hours", "ঘন্টা": "hours",
    "d": "days", "day": "days", "দিন": "days", "week": "weeks", "w": "weeks", "সপ্তাহ": "weeks",
}
_REL = re.compile(r"(?:in|after)?\s*(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|সেকেন্ড|মিনিট|ঘণ্টা|ঘন্টা|দিন|সপ্তাহ|[smhdw])\b",
                  re.IGNORECASE)


def parse_when(value: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    now = (now or datetime.now(tz)).astimezone(tz)
    text = (value or "").strip().translate(_BN_DIGITS)
    if not text:
        raise ValueError("empty time")
    low = text.lower()
    m = _REL.search(low)
    if m and not re.search(r"\d{4}-\d{2}-\d{2}", low):
        raw = m.group(2).lower()
        unit = _UNITS.get(raw) or _UNITS.get(raw.rstrip("s")) or "minutes"
        return now + timedelta(**{unit: float(m.group(1))})
    day_shift = 0
    for word, shift in (("day after tomorrow", 2), ("tomorrow", 1), ("tonight", 0), ("today", 0), ("পরশু", 2), ("কাল", 1), ("আজ", 0)):
        if word in low:
            day_shift = shift
            low = low.replace(word, " ")
            break
    if "tonight" in text.lower() and not re.search(r"\d", low):
        low += " 9pm"
    low = low.replace("সকাল", "am").replace("বিকাল", "pm").replace("সন্ধ্যা", "pm").replace("রাত", "pm").replace("টায়", "").replace("টা", "")
    low = re.sub(r"\b(at|on|by|e)\b", " ", low).strip()
    base = now.replace(second=0, microsecond=0)
    if not low:
        dt = base.replace(hour=9, minute=0) if day_shift else base + timedelta(minutes=1)
    else:
        dt = dparser.parse(low, default=base.replace(tzinfo=None), fuzzy=True)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    dt = dt.astimezone(tz)
    if day_shift:
        dt = dt + timedelta(days=day_shift)
    elif dt < now - timedelta(minutes=1) and not re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", text):
        dt = dt + timedelta(days=1)  # "at 5pm" when it's already 6pm → tomorrow
    return dt
