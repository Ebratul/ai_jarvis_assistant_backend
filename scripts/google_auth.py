"""One-time Google Calendar connection for NONO.

1. Google Cloud Console → APIs & Services → enable "Google Calendar API"
2. Credentials → Create OAuth client ID → "Desktop app" → download JSON
3. Save it as backend/data/credentials.json
4. Run:  .venv/bin/python scripts/google_auth.py   (opens a browser for consent)
This writes data/google_token.json; NONO then syncs events with your Google Calendar.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from nono.config import settings  # noqa: E402
from nono.gcal import SCOPES  # noqa: E402


def main() -> None:
    creds_file = settings.data_dir / "credentials.json"
    if not creds_file.exists():
        sys.exit(f"Missing {creds_file}. Download an OAuth 'Desktop app' client JSON from Google Cloud Console.")
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
    creds = flow.run_local_server(port=0)
    settings.google_token_path.write_text(creds.to_json())
    print(f"✅ Google Calendar connected. Token saved to {settings.google_token_path}")


if __name__ == "__main__":
    main()
