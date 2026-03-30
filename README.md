# Claude Code Telegram Remote

Control Claude Code running in VS Code from your phone via Telegram.

Send a prompt from Telegram, pick a project, and watch Claude Code work in your IDE. Get output summaries back in Telegram automatically.

## How It Works

```
Telegram Message
    |
    v
Bot detects open VS Code windows
    |
    v
"Which project?" (inline buttons)
    |
    v
Injects prompt into Claude Code in the selected VS Code window
    |
    v
Claude Code works (visible in IDE)
    |
    v
Stop Hook sends output summary back to Telegram
```

## Features

- **Remote prompt injection** — Send prompts from Telegram directly into Claude Code running in VS Code
- **Multi-project support** — Detects all open VS Code windows and lets you pick which project to work on
- **Automatic output** — Claude Code's Stop hook sends results back to Telegram when done
- **IDE-native** — Prompts run inside your actual Claude Code session with full project context (CLAUDE.md, files, git history)
- **Smart message batching** — Multiple rapid messages are combined into a single prompt
- **File attachments** — Send images (saved to project dir) and PDFs (text extracted and sent as prompt)
- **Long output handling** — Short responses as messages, long ones as `.md` files
- **Task scheduling** — Schedule daily recurring prompts with `/schedule HH:MM task`
- **Authorized access only** — Only your `CHAT_ID` can interact with the bot

## Prerequisites

- **Windows** (uses Win32 API for VS Code window detection)
- **Python 3.10+**
- **Claude Code** installed in VS Code ([install guide](https://claude.ai/code))
- **Telegram Bot** token from [@BotFather](https://t.me/BotFather)

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/claude-code-telegram-remote.git
cd claude-code-telegram-remote

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

## Configuration

### 1. Create your `.env` file

```bash
copy .env.example .env
```

Edit `.env` with your values:

```
TELEGRAM_TOKEN=your-bot-token-from-botfather
CHAT_ID=your-telegram-user-id
```

**Get your Chat ID:** Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram.

### 2. Set your Claude Code keyboard shortcut

The bot needs to know which keyboard shortcut focuses the Claude Code input in VS Code.

1. Open VS Code
2. Press `Ctrl+Shift+P` > search "Claude"
3. Find the command that focuses the chat input and note its keybinding
4. Edit `ide_bridge.py` line with `pyautogui.hotkey(...)` to match your shortcut

Default is `Ctrl+Shift+S`. Change it if yours differs.

### 3. Install the Stop Hook (optional but recommended)

This hook sends Claude Code's output back to Telegram automatically when it finishes working.

Add to your `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python /full/path/to/notify_telegram.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Replace `/full/path/to/` with the actual path to your clone.

## Usage

```bash
venv\Scripts\activate
python main.py
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show usage instructions |
| `/status` | Show open VS Code windows and agent status |
| `/clear` | Clear pending requests |
| `/schedule HH:MM task` | Schedule a daily recurring task |
| `/tasks` | List scheduled tasks |
| `/cancel ID` | Cancel a scheduled task |

### Sending Prompts

1. Send any text message to the bot
2. If multiple VS Code windows are open, pick a project from the buttons
3. The bot focuses the VS Code window, opens Claude Code, pastes your prompt, and presses Enter
4. Claude Code works in the IDE (you can watch it if you're at the screen)
5. When done, the Stop hook sends the summary back to Telegram

### Sending Files

- **Images** — Saved to a temp file, Claude Code is told the path
- **PDFs** — Text extracted with PyPDF2, sent as prompt text
- **Other files** — Content read as text and sent as prompt

## Architecture

```
telegram_agent/
├── main.py                 # Telegram bot — handlers, commands, message batching
├── claude_agent.py         # Delegates to ide_bridge for prompt injection
├── ide_bridge.py           # Windows automation — find VS Code, focus, paste, enter
├── workspace_detector.py   # Detect open VS Code windows via Win32 API
├── notify_telegram.py      # Stop hook — sends Claude Code output to Telegram
├── scheduler.py            # APScheduler for recurring tasks
├── config.py               # Loads .env, defines constants
├── memory.py               # Conversation memory (for future use)
├── tools.py                # Tool definitions (for future use)
├── .env.example            # Template for environment variables
├── .gitignore
├── requirements.txt
├── LICENSE
└── README.md
```

## How VS Code Window Detection Works

The bot uses the Win32 `EnumWindows` API to find all visible windows with titles ending in "Visual Studio Code". It extracts the project folder name from the window title and resolves it to a full path by checking common directories (`~/Desktop`, `~/Documents`, `~/Projects`, etc.) and VS Code's `storage.json`.

## Limitations

- **Windows only** — Uses Win32 API for window management and `pyautogui` for keyboard simulation
- **VS Code must be visible** — Minimized windows may not receive input correctly
- **Single monitor recommended** — Window focus can be unreliable across multiple displays
- **Keyboard shortcut dependency** — You need to configure the correct shortcut for Claude Code focus

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No VS Code window found" | Make sure VS Code is open with a folder (not just a file) |
| Prompt not appearing in IDE | Check your keyboard shortcut in `ide_bridge.py` |
| No output in Telegram | Verify the Stop hook is configured in `~/.claude/settings.json` |
| Bot not responding | Check `CHAT_ID` matches your Telegram user ID |
| "Unauthorized message" in logs | Your chat ID doesn't match — update `.env` |

## Contributing

Pull requests welcome. For major changes, open an issue first.

## License

[MIT](LICENSE)
