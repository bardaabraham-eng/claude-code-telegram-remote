# Claude Code Telegram Remote

Control Claude Code from your phone via Telegram — in both VS Code (IDE mode) and CLI mode.

Send a prompt from Telegram, pick a project, and watch Claude Code work. Get output summaries back in Telegram automatically. Supports dual-mode operation: IDE injection for VS Code and direct CLI streaming.

## How It Works

```
You send a message on Telegram
        |
        v
Bot detects the project
(Forum Topic auto-detect / inline buttons / CLI mode)
        |
        +-----------+-----------+
        |                       |
   IDE Mode                 CLI Mode
        |                       |
Injects prompt into       Runs claude CLI
Claude Code in VS Code    as subprocess
        |                       |
Claude Code works         Streams output
(visible in IDE)          back in real-time
        |                       |
Stop Hook sends           Result sent as
output to Telegram        message or HTML file
```

## Features

- **Dual mode** - IDE mode (VS Code injection) and CLI mode (direct `claude` subprocess with streaming)
- **Forum Topic auto-detection** - Messages in a Telegram Forum Topic auto-route to the associated project
- **Remote prompt injection** - Send prompts from Telegram directly into Claude Code running in VS Code
- **Multi-project support** - Detects all open VS Code windows and lets you pick which project
- **Automatic output** - Claude Code's Stop hook sends results back to Telegram when done
- **CLI streaming** - Real-time progress updates from Claude CLI with live message editing
- **IDE-native** - Prompts run inside your actual Claude Code session with full project context (CLAUDE.md, files, git history)
- **Smart message batching** - Multiple rapid messages are combined into a single prompt
- **File support** - Send images (saved to project dir) and PDFs (text extracted and sent as prompt)
- **Long output as HTML** - Short responses as messages, long ones as styled dark-theme `.html` files
- **Unicode prompt support** - Non-ASCII prompts (Hebrew, etc.) handled via temp file to avoid Windows encoding issues
- **Task scheduling** - Schedule daily recurring prompts with `/schedule HH:MM task`
- **Auto-start on boot** - VBS launcher for Windows Startup folder
- **Authorized access only** - Only your `CHAT_ID` can interact with the bot

## Prerequisites

- **Windows 10/11** (uses Win32 API for VS Code window detection and keyboard simulation)
- **Python 3.10+**
- **Claude Code** installed in VS Code ([marketplace](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code))
- **Telegram Bot** token from [@BotFather](https://t.me/BotFather)
- **Your Telegram User ID** from [@userinfobot](https://t.me/userinfobot)

## Installation

### 1. Clone and set up

```bash
git clone https://github.com/bardaabraham-eng/claude-code-telegram-remote.git
cd claude-code-telegram-remote

python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Choose a name (e.g. "My Dev Bot")
4. Choose a username (e.g. "mydev_agent_bot")
5. Copy the **API token** (looks like `123456789:ABCdefGHI...`)

### 3. Get your Chat ID

1. Open Telegram and search for [@userinfobot](https://t.me/userinfobot)
2. Send any message to it
3. It will reply with your **user ID** (a number like `178766456`)

### 4. Configure environment

```bash
copy .env.example .env
```

Edit `.env`:

```
TELEGRAM_TOKEN=your-bot-token-from-botfather
CHAT_ID=your-telegram-user-id
```

### 5. Set up the Claude Code keyboard shortcut

The bot needs a keyboard shortcut to focus the Claude Code input in VS Code.

1. Open VS Code
2. Press `Ctrl+K` then `Ctrl+S` to open Keyboard Shortcuts
3. Search for `claude-vscode.focus`
4. Assign it a shortcut (recommended: `Ctrl+Shift+F1`)
5. Edit `ide_bridge.py` and update the `_hotkey` call in `send_prompt_to_ide()` to match your shortcut

Default in code:
```python
_hotkey(VK_CONTROL, VK_SHIFT, VK_F1)  # Ctrl+Shift+F1
```

### 6. Install the Stop Hook (recommended)

This hook automatically sends Claude Code's output back to Telegram when it finishes working.

Add to your `~/.claude/settings.json` (merge with existing hooks if you have them):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python C:/full/path/to/notify_telegram.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Replace `C:/full/path/to/` with the actual path to your installation.

## Usage

### Start the bot

```bash
venv\Scripts\activate
python main.py
```

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show usage instructions |
| `/status` | Show open VS Code windows and agent status |
| `/ide` | Open VS Code on the current project |
| `/clear` | Clear pending requests |
| `/schedule HH:MM task` | Schedule a daily recurring task |
| `/tasks` | List scheduled tasks |
| `/cancel ID` | Cancel a scheduled task |

### Sending Prompts

1. Send any text message to the bot
2. If multiple VS Code windows are open, pick a project from the inline buttons
3. The bot focuses the VS Code window, opens Claude Code, pastes your prompt, and presses Enter
4. Claude Code works in the IDE (you can watch it if you're at the screen)
5. When done, the Stop hook sends the summary back to Telegram

### Sending Files

- **Images** - Saved to project directory, Claude Code is told the file path
- **PDFs** - Text extracted with PyPDF2, sent as prompt text
- **Other files** - Content read as text and sent as prompt

## Architecture

```
claude-code-telegram-remote/
├── main.py                 # Telegram bot - handlers, commands, message batching
├── claude_agent.py         # Delegates to ide_bridge for prompt injection
├── ide_bridge.py           # Win32 automation - find VS Code, focus, paste, enter
├── streaming_cli.py        # CLI mode - runs claude as subprocess, streams JSON output
├── workspace_detector.py   # Detect open VS Code windows and resolve paths
├── notify_telegram.py      # Stop hook - sends Claude Code output to Telegram
├── md_to_html.py           # Markdown to styled dark-theme HTML converter
├── scheduler.py            # APScheduler for recurring tasks
├── config.py               # Loads .env, defines constants
├── memory.py               # Conversation memory (reserved for future use)
├── tools.py                # Tool definitions (reserved for future use)
├── start_bot.vbs           # Silent Windows Startup launcher
├── .env.example            # Template for environment variables
├── .gitignore
├── requirements.txt
├── LICENSE                 # MIT
└── README.md
```

## How VS Code Window Detection Works

The bot uses the Win32 `EnumWindows` API to find all visible windows with titles ending in "Visual Studio Code". It extracts the project folder name from the window title and resolves it to a full path by searching common directories (`~/Desktop`, `~/Documents`, `~/Projects`, etc.) and reading VS Code's `storage.json`.

## Limitations

- **Windows only** - Uses Win32 API for window management and `keybd_event` for keyboard simulation
- **VS Code must be open** - At least one VS Code window with a project folder
- **Claude Code must be installed** - The VS Code extension must be active
- **Single keyboard shortcut** - You need to configure `claude-vscode.focus` keybinding
- **Screen required** - The window focus mechanism needs a display (won't work headless)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No VS Code window found" | Make sure VS Code is open with a folder (not just a file) |
| Prompt not appearing in Claude Code | Verify your keyboard shortcut matches `ide_bridge.py` |
| No output in Telegram | Check the Stop hook is configured in `~/.claude/settings.json` |
| Bot not responding | Check `CHAT_ID` matches your Telegram user ID |
| "Unauthorized message" in logs | Your chat ID doesn't match - update `.env` |
| Window focuses but nothing happens | The keybinding may conflict - try a different shortcut |

## Contributing

Pull requests welcome. For major changes, open an issue first.

## License

[MIT](LICENSE)
