"""
Hook script for Claude Code: sends a summary to Telegram when Claude finishes work.
Called by Claude Code's Stop hook via stdin with session JSON data.

Usage in settings.json:
  "hooks": { "Stop": [{ "matcher": "", "hooks": [{ "type": "command", "command": "python /path/to/notify_telegram.py" }] }] }
"""

import json
import os
import sys
import requests

# ---- Config: load from .env file next to this script ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

def _load_env():
    """Load key=value pairs from .env file."""
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

_load_env()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
TELEGRAM_MSG_LIMIT = 4096


FILE_THRESHOLD = 1000  # Send as file if output exceeds this many chars


def send_telegram(text: str, project_name: str = "output"):
    """Send a message to Telegram. Long messages are sent as a .md file."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("TELEGRAM_TOKEN or CHAT_ID not set, skipping notification.", file=sys.stderr)
        return

    base_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

    if len(text) <= FILE_THRESHOLD:
        # Short enough — send as regular message(s)
        chunks = _split_text(text)
        for chunk in chunks:
            try:
                requests.post(
                    f"{base_url}/sendMessage",
                    json={"chat_id": CHAT_ID, "text": chunk},
                    timeout=10,
                )
            except Exception as e:
                print(f"Failed to send Telegram message: {e}", file=sys.stderr)
    else:
        # Long output — send as .md file + short summary header
        try:
            # Send a short header message
            header = text[:300]
            if len(text) > 300:
                header += "\n\n📄 הפלט המלא בקובץ המצורף..."
            requests.post(
                f"{base_url}/sendMessage",
                json={"chat_id": CHAT_ID, "text": header},
                timeout=10,
            )

            # Send the full output as a file
            file_name = f"{project_name}_output.md"
            requests.post(
                f"{base_url}/sendDocument",
                data={"chat_id": CHAT_ID},
                files={"document": (file_name, text.encode("utf-8"), "text/markdown")},
                timeout=15,
            )
        except Exception as e:
            print(f"Failed to send Telegram file: {e}", file=sys.stderr)
            # Fallback: try sending as split messages
            for chunk in _split_text(text):
                try:
                    requests.post(
                        f"{base_url}/sendMessage",
                        json={"chat_id": CHAT_ID, "text": chunk},
                        timeout=10,
                    )
                except Exception:
                    pass


def _split_text(text: str) -> list[str]:
    """Split text into Telegram-sized chunks."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TELEGRAM_MSG_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            while len(line) > TELEGRAM_MSG_LIMIT:
                chunks.append(line[:TELEGRAM_MSG_LIMIT])
                line = line[TELEGRAM_MSG_LIMIT:]
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


def extract_summary_from_transcript(transcript_path: str) -> str:
    """Read the transcript file and extract the last assistant message as summary."""
    if not transcript_path or not os.path.exists(transcript_path):
        return ""

    try:
        last_assistant_text = ""
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Look for assistant messages with text content
                if entry.get("type") == "assistant":
                    message = entry.get("message", {})
                    content = message.get("content", [])
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    if text_parts:
                        last_assistant_text = "\n".join(text_parts)

        return last_assistant_text
    except Exception as e:
        return f"(Could not read transcript: {e})"


def main():
    # Skip if this is a session launched by the Telegram bot
    # (the bot already sends the response to Telegram directly)
    if os.environ.get("TELEGRAM_BOT_SESSION"):
        sys.exit(0)

    # Read hook input from stdin
    try:
        raw = sys.stdin.read()
        hook_data = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_data = {}

    # Don't send notification if this Stop was triggered by another Stop hook
    if hook_data.get("stop_hook_active"):
        sys.exit(0)

    session_id = hook_data.get("session_id", "unknown")
    cwd = hook_data.get("cwd", "unknown")
    transcript_path = hook_data.get("transcript_path", "")

    # Extract project name from cwd
    project_name = os.path.basename(cwd) if cwd else "unknown"

    # Get the last assistant message as summary
    summary = extract_summary_from_transcript(transcript_path)

    if not summary:
        send_telegram(f"✅ Claude Code סיים עבודה\n📁 {project_name}\n📂 {cwd}", project_name)
        sys.exit(0)

    message = f"✅ Claude Code סיים עבודה\n📁 {project_name}\n\n{summary}"
    send_telegram(message, project_name)
    sys.exit(0)


if __name__ == "__main__":
    main()
