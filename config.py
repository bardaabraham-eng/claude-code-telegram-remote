"""
Configuration for the Telegram Agent.
Load from .env file and environment variables.
"""

import os

# Load .env file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

# Telegram
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.environ.get("CHAT_ID", "0"))

# Allowed Telegram user IDs (comma-separated in env). Empty set = chat-id-only auth (legacy, INSECURE).
_allowed_raw = os.environ.get("ALLOWED_USERS", "").strip()
ALLOWED_USERS = {int(x) for x in _allowed_raw.split(",") if x.strip().isdigit()} if _allowed_raw else set()

# Limits
TELEGRAM_MSG_LIMIT = 4096

# CLI fallback
CLI_TIMEOUT = 300  # 5 minutes for claude -p
