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
    """Check that the message is from the authorized chat."""
    return update.effective_chat.id == CHAT_ID


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


def _build_project_tree(workspaces: list[dict], cli_dirs: list[dict]) -> tuple[str, list[dict]]:
    """
    Build a directory tree display and a flat list of all projects.
    Returns (tree_text, all_projects_list).
    Each project in the list has: name, path, mode ('ide' or 'cli'), display_name.
    """
    all_projects = []
    tree_lines = []

    # Group CLI dirs by parent for tree display
    from collections import defaultdict
    parent_groups = defaultdict(list)

    # IDE windows first
    if workspaces:
        tree_lines.append("🖥️ *VS Code (IDE):*")
        for ws in workspaces:
            all_projects.append({
                "name": ws["name"], "path": ws["path"],
                "mode": "ide", "display_name": ws["name"],
            })
            tree_lines.append(f"  ├ `{ws['name']}`")
        tree_lines.append("")

    # CLI dirs grouped by parent
    cli_only = []
    for cd in cli_dirs:
        if any(ws.get("path") == cd["path"] for ws in workspaces):
            continue
        cli_only.append(cd)
        parent = os.path.dirname(cd["path"])
        parent_groups[parent].append(cd)

    if cli_only:
        tree_lines.append("💻 *CLI:*")
        for parent, dirs in sorted(parent_groups.items()):
            parent_short = parent.replace(os.path.expanduser("~"), "~")
            tree_lines.append(f"  📂 `{parent_short}`")
            for d in dirs:
                all_projects.append({
                    "name": d["name"], "path": d["path"],
                    "mode": "cli", "display_name": d["name"],
                    "_cli": True,
                })
                tree_lines.append(f"    ├ `{d['name']}`")
        tree_lines.append("")

    return "\n".join(tree_lines), all_projects


def _find_project_by_name(name: str, projects: list[dict]) -> list[dict]:
    """Find projects matching a name (case insensitive)."""
    name_lower = name.lower().strip()
    exact = [p for p in projects if p["name"].lower() == name_lower]
    if exact:
        return exact
    # Partial match
    partial = [p for p in projects if name_lower in p["name"].lower()]
    return partial


async def ask_project_selection(update: Update, prompt_data: dict) -> bool:
    """
    Detect VS Code workspaces and CLI projects.
    Shows a directory tree and asks user to type project name.
    Returns True if selection was shown.
    """
    workspaces = await asyncio.to_thread(get_vscode_workspaces)

    from workspace_detector import find_project_dirs
    cli_dirs = await asyncio.to_thread(find_project_dirs)
    cli_history = _load_cli_history()

    # Merge history
    cli_paths = {d["path"] for d in cli_dirs}
    for h in cli_history:
        if h["path"] not in cli_paths and os.path.isdir(h["path"]):
            cli_dirs.insert(0, h)
            cli_paths.add(h["path"])

    if not workspaces and not cli_dirs:
        await update.message.reply_text(
            "❌ לא נמצאו חלונות VS Code ולא נמצאו תיקיות פרויקט.\n\n"
            "פתח VS Code או צור תיקיית פרויקט."
        )
        return True

    # Single VS Code window, no CLI — use directly
    if len(workspaces) == 1 and not cli_dirs:
        prompt_data["project"] = workspaces[0]
        return False

    # Build tree
    tree_text, all_projects = _build_project_tree(workspaces, cli_dirs)

    # If only IDE windows (no CLI), use buttons for quick selection
    if not cli_dirs and workspaces:
        buttons = []
        for i, ws in enumerate(workspaces):
            buttons.append(
                [InlineKeyboardButton(
                    f"🖥️ {ws['name']}",
                    callback_data=f"project:{i}",
                )]
            )
        buttons.append(
            [InlineKeyboardButton("📝 נתיב ידני...", callback_data="project:custom")]
        )
        keyboard = InlineKeyboardMarkup(buttons)
        prompt_data["workspaces"] = [
            {"name": ws["name"], "path": ws["path"], "mode": "ide"}
            for ws in workspaces
        ]
        msg = await update.message.reply_text(
            f"📂 *באיזה פרויקט?*\n\n{tree_text}",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        pending_prompts[msg.message_id] = prompt_data
        return True

    # Mixed or CLI only — show tree and ask to type name
    prompt_data["_all_projects"] = all_projects
    pending_prompts["awaiting_project_name"] = prompt_data

    await update.message.reply_text(
        f"📂 *באיזה פרויקט לעבוד?*\n\n"
        f"{tree_text}"
        f"✏️ כתוב את *שם הפרויקט* או שלח *נתיב מלא*:",
        parse_mode="Markdown",
    )
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
        "/status — חלונות פתוחים + סטטוס\n"
        "/open — פתיחת VS Code על פרויקט\n"
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

    buttons = []
    for i, pd in enumerate(project_dirs):
        buttons.append(
            [InlineKeyboardButton(
                f"📂 {pd['name']}  ({pd['path']})",
                callback_data=f"open:{i}",
            )]
        )

    keyboard = InlineKeyboardMarkup(buttons)

    msg = await update.message.reply_text(
        "🖥️ *איזה פרויקט לפתוח ב-VS Code?*",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    # Store project list for the callback
    pending_prompts[msg.message_id] = {"type": "open", "workspaces": project_dirs}


async def handle_open_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button press for /open project selection."""
    query = update.callback_query
    await query.answer()

    msg_id = query.message.message_id
    prompt_data = pending_prompts.pop(msg_id, None)

    if not prompt_data or prompt_data.get("type") != "open":
        await query.edit_message_text("❌ הבקשה פגה. שלח /open שוב.")
        return

    workspaces = prompt_data.get("workspaces", [])
    try:
        idx = int(query.data.split(":")[1])
        project = workspaces[idx]
    except (ValueError, IndexError):
        await query.edit_message_text("❌ בחירה לא תקינה.")
        return

    path = project["path"]
    name = project["name"]

    await query.edit_message_text(f"⏳ פותח VS Code על *{name}*...", parse_mode="Markdown")

    import subprocess
    try:
        subprocess.Popen(["code", path], shell=True)
        await query.edit_message_text(f"✅ VS Code נפתח על *{name}*\n📂 `{path}`", parse_mode="Markdown")
    except Exception as e:
        await query.edit_message_text(f"❌ שגיאה בפתיחת VS Code: {e}")


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

    # Check if we're awaiting a custom path
    if "awaiting_path" in pending_prompts:
        prompt_data = pending_prompts.pop("awaiting_path")
        path = text.strip().strip('"').strip("'")
        if os.path.isdir(path):
            name = os.path.basename(path)
            prompt_data["project"] = {"name": name, "path": path}
            prompt_data["mode"] = "cli"
            _save_cli_history({"name": name, "path": path})
            await update.message.reply_text(
                f"💻 CLI: *{name}*\n⏳ מריץ Claude Code...",
                parse_mode="Markdown",
            )
            await process_prompt(update, prompt_data)
        else:
            await update.message.reply_text(f"❌ הנתיב לא נמצא: `{path}`", parse_mode="Markdown")
        return

    # Check if we're awaiting a project name from tree selection
    if "awaiting_project_name" in pending_prompts:
        prompt_data = pending_prompts.pop("awaiting_project_name")
        input_text = text.strip().strip('"').strip("'")
        all_projects = prompt_data.pop("_all_projects", [])

        # Check if it's a full path
        if os.path.isdir(input_text):
            name = os.path.basename(input_text)
            prompt_data["project"] = {"name": name, "path": input_text}
            prompt_data["mode"] = "cli"
            _save_cli_history({"name": name, "path": input_text})
            await update.message.reply_text(
                f"💻 CLI: *{name}*\n⏳ מריץ Claude Code...",
                parse_mode="Markdown",
            )
            await process_prompt(update, prompt_data)
            return

        # Search by name
        matches = _find_project_by_name(input_text, all_projects)

        if len(matches) == 1:
            project = matches[0]
            prompt_data["project"] = project
            if project.get("_cli") or project.get("mode") == "cli":
                prompt_data["mode"] = "cli"
                _save_cli_history({"name": project["name"], "path": project["path"]})
                icon = "💻"
            else:
                icon = "🖥️"
            await update.message.reply_text(
                f"{icon} *{project['name']}*\n⏳ שולח...",
                parse_mode="Markdown",
            )
            await process_prompt(update, prompt_data)

        elif len(matches) > 1:
            # Multiple matches — show buttons to disambiguate
            buttons = []
            prompt_data["workspaces"] = matches
            for i, m in enumerate(matches):
                icon = "🖥️" if m.get("mode") == "ide" else "💻"
                parent = os.path.basename(os.path.dirname(m["path"]))
                buttons.append(
                    [InlineKeyboardButton(
                        f"{icon} {m['name']} ({parent})",
                        callback_data=f"project:{i}",
                    )]
                )
            keyboard = InlineKeyboardMarkup(buttons)
            msg = await update.message.reply_text(
                f"🔍 נמצאו {len(matches)} פרויקטים עם השם *{input_text}*:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            pending_prompts[msg.message_id] = prompt_data

        else:
            await update.message.reply_text(
                f"❌ לא נמצא פרויקט בשם `{input_text}`\nשלח שם מדויק או נתיב מלא.",
                parse_mode="Markdown",
            )
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
    mode = prompt_data.get("mode", "ide")

    try:
        if prompt_type == "text":
            response = await asyncio.to_thread(
                agent.process_text, prompt_data["content"], project_name, cwd_path, mode
            )

        elif prompt_type == "photo":
            response = await asyncio.to_thread(
                agent.process_image, prompt_data["image_bytes"],
                prompt_data.get("caption", ""), project_name, cwd_path, mode
            )

        elif prompt_type == "document":
            file_name = prompt_data["file_name"]
            file_bytes = prompt_data["file_bytes"]
            mime_type = prompt_data.get("mime_type", "")
            caption = prompt_data.get("caption", "")

            if file_name.lower().endswith(".pdf"):
                response = await asyncio.to_thread(
                    agent.process_pdf, file_bytes, caption, project_name, cwd_path, mode
                )
            elif mime_type.startswith("image/"):
                response = await asyncio.to_thread(
                    agent.process_image, file_bytes, caption, project_name, cwd_path, mode
                )
            else:
                try:
                    file_text = file_bytes.decode("utf-8", errors="replace")
                except Exception:
                    file_text = "(Could not decode file)"
                prompt = f"קיבלתי קובץ: {file_name}\n\nתוכן:\n{file_text[:5000]}"
                if caption:
                    prompt += f"\n\nבקשת המשתמש: {caption}"
                response = await asyncio.to_thread(
                    agent.process_text, prompt, project_name, cwd_path, mode
                )
        else:
            response = "❌ סוג הודעה לא מוכר."

        if mode == "cli":
            # CLI mode — response is the actual output, send it directly
            await reply_func(f"{project_header}{response}")
        else:
            # IDE mode — actual output comes via Stop hook
            await reply_func(f"{project_header}{response}\n\n💡 הפלט ישלח בהודעה נפרדת כש-Claude Code יסיים.")

    except Exception as e:
        logger.error(f"Error processing prompt: {e}")
        await reply_func(f"❌ שגיאה: {e}")


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
        # Ask user to type a path — store prompt and wait for next message
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
    app.add_handler(CallbackQueryHandler(handle_open_callback, pattern=r"^open:"))
    app.add_handler(CallbackQueryHandler(handle_project_callback, pattern=r"^project:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
