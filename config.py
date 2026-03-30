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

# Limits
TELEGRAM_MSG_LIMIT = 4096

# CLI fallback
CLI_TIMEOUT = 300  # 5 minutes for claude -p
