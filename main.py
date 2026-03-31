"""
Main entry point: Telegram bot that connects to the Claude agent.
"""

import asyncio
import io
import json
import logging
import os
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import TELEGRAM_TOKEN, CHAT_ID, TELEGRAM_MSG_LIMIT
from claude_agent import ClaudeAgent
from scheduler import TaskScheduler
from session_manager import SessionManager
from streaming_cli import StreamingCLI
from workspace_detector import get_vscode_workspaces

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
agent = ClaudeAgent()
scheduler = TaskScheduler()
sessions = SessionManager()

# Active streaming sessions: project_path -> StreamingCLI
active_streams: dict[str, StreamingCLI] = {}

# Pending prompts waiting for project selection
pending_prompts: dict[int, dict] = {}

# CLI history file — remember directories used in CLI mode
CLI_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cli_history.json")


def _load_cli_history() -> list[dict]:
    """Load CLI directory history."""
    try:
        if os.path.exists(CLI_HISTORY_FILE):
            with open(CLI_HISTORY_FILE, "r", encoding="utf-8") as f:
                import json
                return json.load(f)
    except Exception:
        pass
    return []


def _save_cli_history(entry: dict):
    """Add a directory to CLI history (most recent first, max 10)."""
    import json
    history = _load_cli_history()
    # Remove duplicates
    history = [h for h in history if h["path"] != entry["path"]]
    # Add to front
    history.insert(0, entry)
    # Keep max 10
    history = history[:10]
    try:
        with open(CLI_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# Message batching: accumulate rapid messages into one prompt
# Maps chat_id -> {"parts": [str, ...], "task": asyncio.Task, "update": Update}
_message_buffer: dict[int, dict] = {}
MESSAGE_BATCH_DELAY = 1.5  # seconds to wait for more messages before sending

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def authorized(update: Update) -> bool:
    """Check that the message is from the authorized chat and not from the bot itself."""
    if update.effective_chat.id != CHAT_ID:
        return False
    # Ignore messages from the bot itself (important in group chats)
    if update.effective_user and update.effective_user.is_bot:
        return False
    return True


async def send_long_message(update: Update, text: str):
    """Send a message, splitting if it exceeds Telegram's limit."""
    if not text:
        text = "(empty response)"

    # Split by paragraphs first, then by hard limit
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TELEGRAM_MSG_LIMIT:
            if current:
                chunks.append(current)
                current = ""
            # If a single line is too long, split it
            while len(line) > TELEGRAM_MSG_LIMIT:
                chunks.append(line[:TELEGRAM_MSG_LIMIT])
                line = line[TELEGRAM_MSG_LIMIT:]
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        await update.message.reply_text(chunk)


async def send_long_message_to_chat(bot, chat_id: int, text: str):
    """Send a long message directly to a chat, splitting if needed."""
    if not text:
        text = "(empty response)"
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
    for chunk in chunks:
        await bot.send_message(chat_id=chat_id, text=chunk)


async def send_file_if_needed(update: Update, text: str):
    """If the response is very long, also send it as a text file."""
    if len(text) > TELEGRAM_MSG_LIMIT * 3:
        buf = io.BytesIO(text.encode("utf-8"))
        buf.name = "response.txt"
        await update.message.reply_document(document=buf)



def _normalize_name(name: str) -> str:
    """Normalize project name for fuzzy matching: lowercase, remove separators."""
    return name.lower().replace("_", "").replace("-", "").replace(" ", "").replace(".", "")


def _find_project_by_name(name: str, projects: list[dict]) -> list[dict]:
    """Find projects matching a name (fuzzy: ignores case, underscores, hyphens, spaces)."""
    norm_input = _normalize_name(name)
    # Exact normalized match
    exact = [p for p in projects if _normalize_name(p["name"]) == norm_input]
    if exact:
        return exact
    # Partial normalized match
    partial = [p for p in projects if norm_input in _normalize_name(p["name"])]
    return partial


async def ask_project_selection(update: Update, prompt_data: dict) -> bool:
    """
    Always CLI mode. Show project directories as buttons.
    Returns True if selection was shown.
    """
    prompt_data["mode"] = "cli"

    from workspace_detector import find_project_dirs
    cli_dirs = await asyncio.to_thread(find_project_dirs)
    cli_history = _load_cli_history()
    cli_paths = {d["path"] for d in cli_dirs}
    for h in cli_history:
        if h["path"] not in cli_paths and os.path.isdir(h["path"]):
            cli_dirs.insert(0, h)
            cli_paths.add(h["path"])

    if not cli_dirs:
        await update.message.reply_text("❌ לא נמצאו תיקיות פרויקט.")
        return True

    # Single project — use directly
    if len(cli_dirs) == 1:
        prompt_data["project"] = {**cli_dirs[0], "mode": "cli", "_cli": True}
        return False

    msg = await update.message.reply_text("⏳ מזהה פרויקטים...")
    buttons = []
    for i, cd in enumerate(cli_dirs):
        buttons.append(
            [InlineKeyboardButton(f"💻 {cd['name']}", callback_data=f"project:{i}")]
        )
    buttons.append(
        [InlineKeyboardButton("📝 נתיב ידני...", callback_data="project:custom")]
    )
    prompt_data["workspaces"] = [{**cd, "mode": "cli", "_cli": True} for cd in cli_dirs]
    keyboard = InlineKeyboardMarkup(buttons)
    sent = await msg.edit_text(
        "📂 *באיזה פרויקט?*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    pending_prompts[sent.message_id] = prompt_data
    return True


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if not authorized(update):
        return
    await update.message.reply_text(
        "🤖 *שלט רחוק ל-Claude Code!*\n\n"
        "שלח פרומפט ואני אריץ אותו דרך Claude Code CLI "
        "ישירות על הפרויקט שתבחר.\n\n"
        "*איך זה עובד:*\n"
        "1. שלח הודעה עם מה שאתה רוצה\n"
        "2. אם יש כמה חלונות VS Code — תבחר פרויקט\n"
        "3. Claude Code ירוץ עם כל ההקשר של הפרויקט\n"
        "4. התוצאה תחזור אליך לטלגרם\n\n"
        "*פקודות:*\n"
        "/status — סטטוס\n"
        "/ide — פתיחת VS Code עם session אחרון\n"
        "/open — פתיחת VS Code על פרויקט\n"
        "/stop — עצירת Claude Code שרץ\n"
        "/clear — ניקוי בקשות ממתינות\n"
        "/schedule HH:MM משימה — תזמון משימה יומית\n"
        "/tasks — רשימת משימות מתוזמנות\n"
        "/cancel ID — ביטול משימה מתוזמנת",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clear command."""
    if not authorized(update):
        return
    pending_prompts.clear()
    await update.message.reply_text(
        "🗑️ בקשות ממתינות נמחקו.\n"
        "💡 Claude Code מנהל את הזיכרון שלו בעצמו לכל פרויקט."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    if not authorized(update):
        return
    tasks = scheduler.get_tasks()
    workspaces = await asyncio.to_thread(get_vscode_workspaces)

    ws_lines = ""
    if workspaces:
        ws_lines = "\n".join(f"  📁 {ws['name']} — {ws['path']}" for ws in workspaces)
    else:
        ws_lines = "  (אין חלונות VS Code פתוחים)"

    status = (
        f"🟢 *סטטוס סוכן*\n\n"
        f"🖥️ *חלונות VS Code:*\n{ws_lines}\n\n"
        f"📋 משימות מתוזמנות: {len(tasks)}\n"
        f"🤖 מנוע: Claude Code CLI\n"
    )
    await update.message.reply_text(status, parse_mode="Markdown")


async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /open command — open VS Code on a project directory."""
    if not authorized(update):
        return

    from workspace_detector import find_project_dirs
    project_dirs = await asyncio.to_thread(find_project_dirs)

    if not project_dirs:
        await update.message.reply_text("❌ לא נמצאו תיקיות פרויקט.")
        return

    # Build tree display
    from collections import defaultdict
    parent_groups = defaultdict(list)
    for pd in project_dirs:
        parent = os.path.dirname(pd["path"])
        parent_groups[parent].append(pd)

    tree_lines = []
    for parent, dirs in sorted(parent_groups.items()):
        parent_short = parent.replace(os.path.expanduser("~"), "~")
        tree_lines.append(f"📂 `{parent_short}`")
        for d in dirs:
            tree_lines.append(f"  ├ `{d['name']}`")

    pending_prompts["awaiting_open_name"] = {"projects": project_dirs}

    await update.message.reply_text(
        f"🖥️ *איזה פרויקט לפתוח ב-VS Code?*\n\n"
        f"{chr(10).join(tree_lines)}\n\n"
        f"✏️ כתוב את *שם הפרויקט* או שלח *נתיב מלא*:",
        parse_mode="Markdown",
    )


async def cmd_ide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ide — open VS Code and load a recent CLI session."""
    if not authorized(update):
        return

    import time as _time
    cutoff = _time.time() - 86400  # 24 hours

    # Collect recent sessions from all projects
    all_recent = []
    for proj_key, proj_data in sessions._data.get("projects", {}).items():
        path = proj_data.get("path", "")
        for s in proj_data.get("sessions", []):
            if s.get("last_used", 0) > cutoff:
                all_recent.append({
                    "session_id": s["id"],
                    "label": s.get("label", s["id"][:8]),
                    "project_path": path,
                    "project_name": os.path.basename(path),
                    "last_used": s["last_used"],
                })

    if not all_recent:
        await update.message.reply_text("❌ אין sessions מהן24 שעות האחרונות.\nשלח הודעה רגילה כדי להתחיל session חדש.")
        return

    all_recent.sort(key=lambda x: x["last_used"], reverse=True)

    buttons = []
    for i, s in enumerate(all_recent[:10]):
        import datetime
        ts = datetime.datetime.fromtimestamp(s["last_used"]).strftime("%H:%M")
        buttons.append(
            [InlineKeyboardButton(
                f"🖥️ {s['project_name']} — {s['label'][:30]} ({ts})",
                callback_data=f"ide:{i}",
            )]
        )

    keyboard = InlineKeyboardMarkup(buttons)
    pending_prompts["ide_sessions"] = all_recent[:10]

    await update.message.reply_text(
        "🖥️ *טעינת session ב-VS Code*\n\n"
        "בחר session לפתוח:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def handle_ide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard for /ide session selection."""
    query = update.callback_query
    await query.answer()

    recent = pending_prompts.pop("ide_sessions", None)
    if not recent:
        await query.edit_message_text("❌ הבקשה פגה. שלח /ide שוב.")
        return

    try:
        idx = int(query.data.split(":")[1])
        session = recent[idx]
    except (ValueError, IndexError):
        await query.edit_message_text("❌ בחירה לא תקינה.")
        return

    path = session["project_path"]
    sid = session["session_id"]
    name = session["project_name"]

    await query.edit_message_text(f"⏳ פותח VS Code על *{name}* וטוען session...", parse_mode="Markdown")

    import subprocess
    try:
        # Open VS Code on the project
        subprocess.Popen(["code", path], shell=True)

        await query.edit_message_text(
            f"✅ VS Code נפתח על *{name}*\n\n"
            f"📋 להמשך ה-session, הרץ ב-Claude Code:\n"
            f"`claude --resume {sid}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        await query.edit_message_text(f"❌ שגיאה: {e}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop — cancel running CLI stream."""
    if not authorized(update):
        return
    if not active_streams:
        await update.message.reply_text("🤷 אין Claude Code רץ כרגע.")
        return
    for path, streamer in list(active_streams.items()):
        streamer.cancel()
        project_name = os.path.basename(path)
        await update.message.reply_text(f"⛔ Claude Code בוטל: *{project_name}*", parse_mode="Markdown")
    active_streams.clear()


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule HH:MM task description."""
    if not authorized(update):
        return

    text = update.message.text
    match = re.match(r"/schedule\s+(\d{1,2}):(\d{2})\s+(.+)", text)
    if not match:
        await update.message.reply_text(
            "❌ פורמט: /schedule HH:MM תיאור המשימה\n"
            "דוגמה: /schedule 09:00 בדוק את סטטוס השרת"
        )
        return

    hour, minute, description = int(match.group(1)), int(match.group(2)), match.group(3)

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("❌ שעה לא תקינה. השתמש בפורמט HH:MM (00:00 עד 23:59)")
        return

    async def task_callback(task_description: str):
        """Called by the scheduler — runs the agent and sends results."""
        try:
            response = await asyncio.to_thread(agent.process_text, task_description)
            # Send directly to the chat
            from telegram import Bot

            bot = Bot(token=TELEGRAM_TOKEN)
            header = f"⏰ *משימה מתוזמנת:* {task_description}\n\n"
            full = header + response
            # Split if needed
            if len(full) <= TELEGRAM_MSG_LIMIT:
                await bot.send_message(chat_id=CHAT_ID, text=full, parse_mode="Markdown")
            else:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=header + full[:TELEGRAM_MSG_LIMIT - len(header)],
                )
                remaining = full[TELEGRAM_MSG_LIMIT - len(header) :]
                while remaining:
                    chunk = remaining[:TELEGRAM_MSG_LIMIT]
                    remaining = remaining[TELEGRAM_MSG_LIMIT:]
                    await bot.send_message(chat_id=CHAT_ID, text=chunk)
        except Exception as e:
            logger.error(f"Scheduled task error: {e}")

    task_id = scheduler.add_task(hour, minute, description, task_callback)
    await update.message.reply_text(
        f"✅ משימה #{task_id} תוזמנה ל-{hour:02d}:{minute:02d} כל יום.\n"
        f"📝 {description}"
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /tasks command."""
    if not authorized(update):
        return
    tasks = scheduler.get_tasks()
    if not tasks:
        await update.message.reply_text("📋 אין משימות מתוזמנות.")
        return
    lines = ["📋 *משימות מתוזמנות:*\n"]
    for t in tasks:
        lines.append(f"🔹 #{t['id']} | ⏰ {t['time']} | {t['description']}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel ID command."""
    if not authorized(update):
        return
    text = update.message.text
    match = re.match(r"/cancel\s+(\d+)", text)
    if not match:
        await update.message.reply_text("❌ פורמט: /cancel ID\nדוגמה: /cancel 1")
        return
    task_id = match.group(1)
    if scheduler.remove_task(task_id):
        await update.message.reply_text(f"✅ משימה #{task_id} בוטלה.")
    else:
        await update.message.reply_text(f"❌ משימה #{task_id} לא נמצאה.")


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------


async def _flush_message_buffer(chat_id: int):
    """Wait for batch delay, then combine all buffered messages and process."""
    await asyncio.sleep(MESSAGE_BATCH_DELAY)

    buf = _message_buffer.pop(chat_id, None)
    if not buf:
        return

    combined_text = "\n".join(buf["parts"])
    update = buf["update"]

    logger.info(f"Flushed {len(buf['parts'])} message(s) as one prompt: {combined_text[:100]}...")

    prompt_data = {"type": "text", "content": combined_text, "chat_id": chat_id}

    asked = await ask_project_selection(update, prompt_data)
    if asked:
        return

    await process_prompt(update, prompt_data)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages. Batches rapid sequential messages into one prompt."""
    logger.info(f"Incoming message from chat_id={update.effective_chat.id}, authorized CHAT_ID={CHAT_ID}")
    if not authorized(update):
        logger.warning(f"Unauthorized message from {update.effective_chat.id}, ignoring.")
        return

    text = update.message.text
    chat_id = update.effective_chat.id
    logger.info(f"Text from {chat_id}: {text[:100]}...")

    # Check if we're awaiting a project name for /open
    if "awaiting_open_name" in pending_prompts:
        open_data = pending_prompts.pop("awaiting_open_name")
        input_text = text.strip().strip('"').strip("'")
        projects = open_data["projects"]

        # Full path?
        if os.path.isdir(input_text):
            path = input_text
            name = os.path.basename(path)
        else:
            matches = _find_project_by_name(input_text, [{"name": p["name"], "path": p["path"]} for p in projects])
            if len(matches) == 1:
                path = matches[0]["path"]
                name = matches[0]["name"]
            elif len(matches) > 1:
                lines = [f"🔍 נמצאו {len(matches)}:"]
                for m in matches:
                    lines.append(f"  ├ `{m['name']}` ({m['path']})")
                lines.append("\nכתוב שם מדויק יותר או נתיב מלא.")
                pending_prompts["awaiting_open_name"] = open_data
                await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
                return
            else:
                await update.message.reply_text(f"❌ לא נמצא: `{input_text}`", parse_mode="Markdown")
                return

        import subprocess
        try:
            subprocess.Popen(["code", path], shell=True)
            await update.message.reply_text(f"✅ VS Code נפתח על *{name}*\n📂 `{path}`", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה: {e}")
        return

    # Check if we're awaiting a custom path
    if "awaiting_path" in pending_prompts:
        prompt_data = pending_prompts.pop("awaiting_path")
        input_path = text.strip().strip('"').strip("'")
        # Normalize: fix backslashes from Telegram, handle both / and \
        input_path = input_path.replace("\\", os.sep).replace("/", os.sep)

        # Try as full path first
        if os.path.isdir(input_path):
            path = input_path
        else:
            # Try as folder name under common parent directories
            path = None
            roots = [
                os.path.expanduser("~/Desktop"),
                os.path.expanduser("~/Desktop/SU"),
                os.path.expanduser("~/Documents"),
                os.path.expanduser("~/Projects"),
            ]
            for root in roots:
                candidate = os.path.join(root, input_path)
                if os.path.isdir(candidate):
                    path = candidate
                    break

            # Still not found — offer to create it
            if not path:
                default_parent = os.path.expanduser("~/Desktop/SU")
                new_path = os.path.join(default_parent, input_path)
                pending_prompts["awaiting_create_confirm"] = {
                    **prompt_data,
                    "new_path": new_path,
                    "new_name": input_path,
                }
                buttons = [
                    [InlineKeyboardButton(f"✅ צור ב-{new_path}", callback_data="create:yes")],
                    [InlineKeyboardButton("❌ ביטול", callback_data="create:no")],
                ]
                await update.message.reply_text(
                    f"📁 `{input_path}` לא נמצא.\n\nליצור תיקייה חדשה?",
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="Markdown",
                )
                return

        name = os.path.basename(path)
        prompt_data["project"] = {"name": name, "path": path}
        prompt_data["mode"] = "cli"
        _save_cli_history({"name": name, "path": path})
        await update.message.reply_text(
            f"💻 CLI: *{name}*\n⏳ מריץ Claude Code...",
            parse_mode="Markdown",
        )
        await process_prompt(update, prompt_data)
        return

    # Add to buffer
    if chat_id in _message_buffer:
        # Cancel the previous flush timer and append
        _message_buffer[chat_id]["task"].cancel()
        _message_buffer[chat_id]["parts"].append(text)
        _message_buffer[chat_id]["update"] = update
    else:
        _message_buffer[chat_id] = {"parts": [text], "update": update, "task": None}

    # Schedule a new flush
    task = asyncio.create_task(_flush_message_buffer(chat_id))
    _message_buffer[chat_id]["task"] = task


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photos."""
    if not authorized(update):
        return

    logger.info("Photo received.")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""

        prompt_data = {
            "type": "photo",
            "image_bytes": bytes(image_bytes),
            "caption": caption,
            "chat_id": update.effective_chat.id,
        }

        asked = await ask_project_selection(update, prompt_data)
        if asked:
            return

        await process_prompt(update, prompt_data)
    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        await update.message.reply_text(f"❌ שגיאה בעיבוד תמונה: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming documents (PDF, images, text files)."""
    if not authorized(update):
        return

    doc = update.message.document
    file_name = doc.file_name or "unknown"
    logger.info(f"Document received: {file_name}")

    try:
        file = await doc.get_file()
        file_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""

        prompt_data = {
            "type": "document",
            "file_name": file_name,
            "file_bytes": bytes(file_bytes),
            "mime_type": doc.mime_type or "",
            "caption": caption,
            "chat_id": update.effective_chat.id,
        }

        asked = await ask_project_selection(update, prompt_data)
        if asked:
            return

        await process_prompt(update, prompt_data)
    except Exception as e:
        logger.error(f"Error processing document: {e}")
        await update.message.reply_text(f"❌ שגיאה בעיבוד קובץ: {e}")


# ---------------------------------------------------------------------------
# Forum Topics management
# ---------------------------------------------------------------------------

# Cache of project_path -> thread_id
_topic_cache: dict[str, int] = {}


async def _get_or_create_topic(source, project_name: str, project_path: str) -> int | None:
    """Get or create a Forum Topic for this project. Returns thread_id or None."""
    # Check cache
    if project_path in _topic_cache:
        return _topic_cache[project_path]

    # Check session manager for saved thread_id
    last_session = sessions.get_last_session(project_path)
    if last_session and last_session.get("thread_msg_id"):
        _topic_cache[project_path] = last_session["thread_msg_id"]
        return last_session["thread_msg_id"]

    # Create new topic
    try:
        import requests as _requests
        resp = _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/createForumTopic",
            json={"chat_id": CHAT_ID, "name": f"📁 {project_name}"},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            thread_id = data["result"]["message_thread_id"]
            _topic_cache[project_path] = thread_id
            logger.info(f"Created topic '{project_name}' with thread_id={thread_id}")
            return thread_id
        else:
            logger.warning(f"Failed to create topic: {data}")
            return None
    except Exception as e:
        logger.error(f"Error creating topic: {e}")
        return None


# ---------------------------------------------------------------------------
# Process prompt (after project selection)
# ---------------------------------------------------------------------------


async def process_prompt(source, prompt_data: dict):
    """
    Process a prompt after project selection.
    source: Update (direct) or CallbackQuery (from button press).
    """
    project = prompt_data.get("project")  # None or {"name": ..., "path": ...}
    prompt_type = prompt_data["type"]

    # Build project context prefix
    project_header = ""
    cwd_path = None
    if project:
        project_header = f"📁 *{project['name']}*\n\n"
        cwd_path = project["path"]

    # Determine how to send messages back
    if isinstance(source, Update):
        reply_func = source.message.reply_text
        reply_doc_func = source.message.reply_document
    else:
        # CallbackQuery — use the bot to send to chat
        bot = source.get_bot()
        chat_id = prompt_data["chat_id"]

        async def reply_func(text, **kwargs):
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)

        async def reply_doc_func(document, **kwargs):
            return await bot.send_document(chat_id=chat_id, document=document, **kwargs)

    project_name = project["name"] if project else None

    # Create or reuse a Forum Topic for this project
    thread_id = None
    if project and cwd_path:
        thread_id = await _get_or_create_topic(source, project_name, cwd_path)

    # Override reply_func to send into the topic thread
    if thread_id:
        if isinstance(source, Update):
            bot = source.message.get_bot()
        else:
            bot = source.get_bot()
        chat_id = prompt_data.get("chat_id", CHAT_ID)

        async def reply_func(text, **kwargs):
            kwargs["message_thread_id"] = thread_id
            return await bot.send_message(chat_id=chat_id, text=text, **kwargs)

        async def reply_doc_func(document, **kwargs):
            kwargs["message_thread_id"] = thread_id
            return await bot.send_document(chat_id=chat_id, document=document, **kwargs)

    # Build the actual prompt text
    if prompt_type == "text":
        prompt_text = prompt_data["content"]
    elif prompt_type == "photo":
        # Save image, build prompt
        import tempfile
        save_dir = cwd_path or os.path.join(os.path.expanduser("~"), "Desktop")
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=save_dir, prefix="telegram_") as f:
                f.write(prompt_data["image_bytes"])
                tmp_path = f.name
            prompt_text = f"שמרתי תמונה בנתיב: {tmp_path}"
            if prompt_data.get("caption"):
                prompt_text += f"\n{prompt_data['caption']}"
            else:
                prompt_text += "\nתאר מה יש בתמונה."
        except Exception as e:
            await reply_func(f"❌ שגיאה בשמירת תמונה: {e}")
            return
    elif prompt_type == "document":
        file_name = prompt_data["file_name"]
        file_bytes = prompt_data["file_bytes"]
        caption = prompt_data.get("caption", "")
        mime_type = prompt_data.get("mime_type", "")

        if file_name.lower().endswith(".pdf"):
            import io as _io
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(_io.BytesIO(file_bytes))
                parts = [p.extract_text() for p in reader.pages[:10] if p.extract_text()]
                pdf_text = "\n".join(parts)[:3000]
            except Exception as e:
                pdf_text = f"(שגיאה: {e})"
            prompt_text = f"תוכן PDF:\n{pdf_text}"
            if caption:
                prompt_text += f"\n\n{caption}"
        elif mime_type.startswith("image/"):
            import tempfile
            save_dir = cwd_path or os.path.join(os.path.expanduser("~"), "Desktop")
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=save_dir, prefix="telegram_") as f:
                f.write(file_bytes)
                tmp_path = f.name
            prompt_text = f"שמרתי תמונה: {tmp_path}"
            if caption:
                prompt_text += f"\n{caption}"
        else:
            try:
                file_text = file_bytes.decode("utf-8", errors="replace")
            except Exception:
                file_text = "(Could not decode)"
            prompt_text = f"קובץ: {file_name}\n\nתוכן:\n{file_text[:5000]}"
            if caption:
                prompt_text += f"\n\n{caption}"
    else:
        await reply_func("❌ סוג הודעה לא מוכר.")
        return

    try:
        # Find the latest session (IDE or CLI) for this project
        from streaming_cli import find_latest_session_id
        latest_sid = find_latest_session_id(cwd_path) if cwd_path else None

        # CLI streaming mode (default)
        await _run_streaming_cli(reply_func, project_header, prompt_text,
                                 cwd_path, project_name, latest_sid)

    except Exception as e:
        logger.error(f"Error processing prompt: {e}")
        await reply_func(f"❌ שגיאה: {e}")


async def _run_streaming_cli(reply_func, project_header: str, prompt: str,
                             cwd: str, project_name: str, session_id: str = None):
    """Run CLI with streaming and update Telegram message in real time."""
    # Get existing session or start new
    if not session_id and cwd:
        last = sessions.get_last_session(cwd)
        if last:
            session_id = last["id"]

    # Send initial "working" message — this becomes the thread anchor
    status_msg = await reply_func(f"{project_header}⏳ Claude Code עובד...")

    # We'll update this message as text streams in
    streamer = StreamingCLI()
    if cwd:
        active_streams[cwd] = streamer

    collected_text = ""
    last_update_time = 0
    update_interval = 2.0  # Update Telegram every 2 seconds
    loop = asyncio.get_event_loop()

    done_event = asyncio.Event()
    result_holder = {"text": "", "session_id": "", "error": ""}

    def on_text(chunk):
        nonlocal collected_text, last_update_time
        collected_text += chunk
        import time
        now = time.time()
        if now - last_update_time >= update_interval:
            last_update_time = now
            # Schedule a Telegram message update
            asyncio.run_coroutine_threadsafe(
                _update_streaming_msg(status_msg, project_header, collected_text),
                loop,
            )

    def on_done(full_text, sid):
        result_holder["text"] = full_text
        result_holder["session_id"] = sid
        loop.call_soon_threadsafe(done_event.set)

    def on_error(err):
        result_holder["error"] = err
        loop.call_soon_threadsafe(done_event.set)

    # Start streaming in background thread
    streamer.run_streaming(
        prompt=prompt,
        cwd=cwd,
        session_id=session_id,
        on_text=on_text,
        on_done=on_done,
        on_error=on_error,
    )

    # Wait for completion
    await done_event.wait()

    # Clean up
    if cwd and cwd in active_streams:
        del active_streams[cwd]

    if result_holder["error"]:
        await _update_streaming_msg(status_msg, project_header,
                                    result_holder["error"], final=True)
        return

    final_text = result_holder["text"]
    sid = result_holder["session_id"]

    # Save session with topic thread_id
    if sid and cwd:
        label = prompt[:40].replace("\n", " ")
        topic_id = _topic_cache.get(cwd)
        sessions.save_session(cwd, sid, label=label, thread_msg_id=topic_id)

    # Final update
    await _update_streaming_msg(status_msg, project_header, final_text, final=True)


async def _update_streaming_msg(msg, header: str, text: str, final: bool = False):
    """Update the streaming status message in Telegram."""
    icon = "✅" if final else "⏳"
    # Truncate for Telegram limit
    max_len = TELEGRAM_MSG_LIMIT - len(header) - 20
    display = text
    if len(display) > max_len:
        display = display[:max_len] + "\n\n... [קוצר]"

    full = f"{header}{icon} {'סיים' if final else 'עובד...'}\n\n{display}"

    try:
        await msg.edit_text(full)
    except Exception as e:
        # Message might be too similar or other Telegram error
        if "not modified" not in str(e).lower():
            logger.warning(f"Could not update streaming msg: {e}")


async def handle_create_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle create directory confirmation."""
    query = update.callback_query
    await query.answer()

    data = pending_prompts.pop("awaiting_create_confirm", None)
    if not data:
        await query.edit_message_text("❌ הבקשה פגה.")
        return

    if query.data == "create:no":
        await query.edit_message_text("❌ בוטל.")
        return

    # Create the directory
    new_path = data["new_path"]
    new_name = data["new_name"]
    try:
        os.makedirs(new_path, exist_ok=True)
        data["project"] = {"name": new_name, "path": new_path}
        data["mode"] = "cli"
        _save_cli_history({"name": new_name, "path": new_path})
        await query.edit_message_text(
            f"✅ תיקייה נוצרה: `{new_path}`\n⏳ מריץ Claude Code...",
            parse_mode="Markdown",
        )
        await process_prompt(query, data)
    except Exception as e:
        await query.edit_message_text(f"❌ שגיאה ביצירת תיקייה: {e}")


async def handle_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button press for project selection."""
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id
    prompt_data = pending_prompts.pop(msg_id, None)

    if not prompt_data:
        await query.edit_message_text("❌ הבקשה פגה. שלח שוב.")
        return

    data = query.data  # "project:0", "project:1", "project:none", "project:custom"
    workspaces = prompt_data.get("workspaces", [])

    if data == "project:custom":
        await query.edit_message_text("📝 שלח את הנתיב לתיקיית הפרויקט:")
        pending_prompts["awaiting_path"] = prompt_data
        return

    if data == "project:none":
        prompt_data["project"] = None
    else:
        try:
            idx = int(data.split(":")[1])
            prompt_data["project"] = workspaces[idx]
        except (ValueError, IndexError):
            await query.edit_message_text("❌ בחירה לא תקינה.")
            return

    project = prompt_data.get("project")
    if project:
        # Check if this is a CLI project
        is_cli = project.get("_cli", False)
        if is_cli:
            prompt_data["mode"] = "cli"
            _save_cli_history({"name": project["name"], "path": project["path"]})
            await query.edit_message_text(
                f"💻 CLI: *{project['name']}*\n⏳ מריץ Claude Code...",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"🖥️ נבחר: *{project['name']}*",
                parse_mode="Markdown",
            )
    else:
        await query.edit_message_text("🌐 עובד ללא פרויקט ספציפי.")

    await process_prompt(query, prompt_data)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    """Start the bot."""
    if not TELEGRAM_TOKEN:
        print("ERROR: Set the TELEGRAM_TOKEN environment variable.")
        return
    if not CHAT_ID:
        print("ERROR: Set the CHAT_ID environment variable.")
        return

    logger.info("Starting Telegram Agent...")

    async def post_init(application):
        """Start scheduler once the event loop is running."""
        scheduler.start()

    # Build application
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("schedule", cmd_schedule))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("ide", cmd_ide))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CallbackQueryHandler(handle_ide_callback, pattern=r"^ide:"))
    app.add_handler(CallbackQueryHandler(handle_create_callback, pattern=r"^create:"))
    app.add_handler(CallbackQueryHandler(handle_project_callback, pattern=r"^project:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
