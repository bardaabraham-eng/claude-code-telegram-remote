"""
Streaming Claude Code CLI runner.
Runs `claude -p --output-format stream-json` and yields text chunks in real time.
"""

import json
import logging
import os
import subprocess
import threading

from config import CLI_TIMEOUT

logger = logging.getLogger(__name__)

CLAUDE_CMD = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")


def _find_project_dir(project_path: str) -> str | None:
    """Find the Claude Code project directory for a given project path.
    Uses fuzzy matching since Claude Code normalizes paths in various ways
    (replacing :, \\, /, _ with dashes, case changes, etc.).
    """
    home = os.path.expanduser("~")
    projects_dir = os.path.join(home, ".claude", "projects")
    if not os.path.isdir(projects_dir):
        return None

    # Build a simplified key from the path for matching
    norm = os.path.normpath(project_path)
    # Extract meaningful parts: drive + path segments
    parts = norm.replace(":", "").replace("\\", "/").split("/")
    parts = [p for p in parts if p]  # remove empty

    for entry in os.listdir(projects_dir):
        entry_path = os.path.join(projects_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        # Normalize the entry name for comparison
        entry_parts = entry.replace("-", " ").replace("_", " ").lower().split()
        path_check = " ".join(p.lower() for p in parts)
        entry_check = " ".join(entry_parts)
        # All path parts must appear in the entry (in order isn't required, just all present)
        if all(p.lower().replace("_", " ").replace("-", " ") in entry_check for p in parts):
            return entry_path

    return None


def find_latest_session_id(project_path: str) -> str | None:
    """
    Find the most recent Claude Code session ID for a project.
    Looks at ~/.claude/projects/<project-key>/*.jsonl files.
    Works for both IDE and CLI sessions.
    """
    try:
        matched_dir = _find_project_dir(project_path)
        if not matched_dir:
            return None

        # Find most recent .jsonl file
        jsonl_files = []
        for f in os.listdir(matched_dir):
            if f.endswith(".jsonl"):
                full = os.path.join(matched_dir, f)
                jsonl_files.append((os.path.getmtime(full), f.replace(".jsonl", "")))

        if not jsonl_files:
            return None

        jsonl_files.sort(reverse=True)
        session_id = jsonl_files[0][1]
        logger.info(f"Found latest session for {project_path}: {session_id}")
        return session_id

    except Exception as e:
        logger.warning(f"Could not find session: {e}")
        return None


def get_project_sessions(project_path: str, max_sessions: int = 5) -> list[dict]:
    """
    Get recent sessions for a project with their labels (last prompt).
    Returns [{"id": ..., "label": ..., "mtime": ...}, ...]
    """
    try:
        matched_dir = _find_project_dir(project_path)
        if not matched_dir:
            return []

        jsonl_files = []
        for f in os.listdir(matched_dir):
            if f.endswith(".jsonl"):
                full = os.path.join(matched_dir, f)
                jsonl_files.append((os.path.getmtime(full), f.replace(".jsonl", ""), full))

        jsonl_files.sort(reverse=True)
        results = []
        for mtime, sid, full_path in jsonl_files[:max_sessions]:
            label = _extract_session_label(full_path)
            results.append({"id": sid, "label": label, "mtime": mtime})
        return results

    except Exception as e:
        logger.warning(f"Could not get sessions for {project_path}: {e}")
        return []


def _extract_session_label(jsonl_path: str) -> str:
    """Extract a meaningful label from a session file.
    Tries: last-prompt from end of file, then first user message from start.
    """
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            # Try last-prompt from end of file
            f.seek(0, 2)
            file_size = f.tell()
            read_size = min(file_size, 10240)
            f.seek(file_size - read_size)
            tail = f.read()

        last_prompt = ""
        for line in tail.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "last-prompt":
                    lp = data.get("lastPrompt", "")
                    if lp:
                        last_prompt = lp
            except (json.JSONDecodeError, KeyError):
                pass

        if last_prompt:
            return last_prompt[:60]

        # Fallback: find first user message from start of file
        with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    msg = data.get("message", {})
                    if msg.get("role") != "user":
                        continue
                    content = msg.get("content", "")
                    text = ""
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                break
                    elif isinstance(content, str):
                        text = content
                    # Skip system/meta messages
                    if text and not text.startswith(("[Request", "<task", "<ide_", "<system", "This session")):
                        return text.replace("\n", " ")[:60]
                except (json.JSONDecodeError, KeyError):
                    pass

        return "(ללא כותרת)"
    except Exception:
        return "(ללא כותרת)"


TOOL_ICONS = {
    "Read": "📖",
    "Edit": "✏️",
    "Write": "📝",
    "Bash": "⚡",
    "Glob": "🔍",
    "Grep": "🔎",
    "WebSearch": "🌐",
    "WebFetch": "🌐",
    "TodoWrite": "📋",
    "Agent": "🤖",
}


def _format_tool_status(tool_name: str, tool_input: dict) -> str:
    """Format a tool_use event into a short status string."""
    icon = TOOL_ICONS.get(tool_name, "🔧")

    if tool_name == "Read":
        path = tool_input.get("file_path", "")
        short = os.path.basename(path) if path else ""
        return f"{icon} קורא: {short}" if short else f"{icon} קורא קובץ"

    if tool_name == "Edit":
        path = tool_input.get("file_path", "")
        short = os.path.basename(path) if path else ""
        return f"{icon} עורך: {short}" if short else f"{icon} עורך קובץ"

    if tool_name == "Write":
        path = tool_input.get("file_path", "")
        short = os.path.basename(path) if path else ""
        return f"{icon} כותב: {short}" if short else f"{icon} כותב קובץ"

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        short = cmd[:50] + "..." if len(cmd) > 50 else cmd
        return f"{icon} מריץ: `{short}`"

    if tool_name in ("Glob", "Grep"):
        pattern = tool_input.get("pattern", "")
        return f"{icon} מחפש: {pattern[:40]}"

    if tool_name in ("WebSearch", "WebFetch"):
        query = tool_input.get("query", tool_input.get("url", ""))
        return f"{icon} {query[:40]}"

    if tool_name == "Agent":
        desc = tool_input.get("description", tool_input.get("prompt", ""))
        return f"{icon} סוכן: {desc[:40]}"

    return f"{icon} {tool_name}"


class StreamingCLI:
    """Run Claude Code CLI with streaming output."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def run_streaming(self, prompt: str, cwd: str = None,
                      session_id: str = None, model: str = None,
                      on_text=None, on_status=None, on_done=None, on_error=None):
        """
        Run claude -p with streaming. Calls back:
          on_text(chunk: str) — each text chunk as it arrives
          on_status(status: str) — tool use status updates (e.g. "📖 Read config.py")
          on_done(full_text: str, session_id: str) — when complete
          on_error(error: str) — on failure

        Returns immediately, runs in background thread.
        """
        self._cancelled = False

        def _run():
            import tempfile

            prompt_path = None
            # SECURITY: ALWAYS route through a temp file. Previously short ASCII
            # prompts were embedded directly into a shell=True command line —
            # any " / & / | / ^ / % in a Telegram message broke quoting and
            # could execute arbitrary commands. Temp-file path is uniform now.
            prompt_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False,
                encoding="utf-8", dir=cwd or None, prefix=".prompt_"
            )
            prompt_file.write(prompt)
            prompt_file.close()
            prompt_path = prompt_file.name
            cli_prompt = f"Read the file {prompt_path} and follow the instructions in it."
            logger.info(f"Saved prompt to temp file: {prompt_path}")

            cmd = [CLAUDE_CMD, "-p", cli_prompt,
                   "--output-format", "stream-json", "--verbose",
                   "--dangerously-skip-permissions"]

            if model:
                cmd.extend(["--model", model])

            if session_id:
                cmd.extend(["--resume", session_id])

            logger.info(f"Streaming CLI: cwd={cwd}, session={session_id or 'continue'}, prompt={prompt[:80]}...")

            try:
                # Set env var so Stop hook knows not to send duplicate notification
                env = os.environ.copy()
                env["TELEGRAM_BOT_SESSION"] = "1"
                # NOTE: lock-file logic removed 2026-06-10 — a stale file blocked the
                # Stop hook silently for 3 weeks. Env var inherited by child is enough.

                # Pass arg list (no shell=True). Windows resolves .cmd via Popen.
                # stderr → STDOUT to avoid two-pipe deadlock when claude is verbose.
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )

                full_text = ""
                result_session_id = session_id or ""
                got_init = False  # Track if we've seen the init event (history done)
                last_new_assistant_text = ""

                # Read line by line (each JSON event is one line)
                while True:
                    line = self._process.stdout.readline()
                    if not line:
                        break
                    if self._cancelled:
                        self._kill_tree(self._process.pid)
                        if on_error:
                            on_error("⛔ בוטל.")
                        return

                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Track session ID
                    sid = event.get("session_id", "")
                    if sid:
                        result_session_id = sid

                    # Log every event type for debugging
                    subtype = event.get("subtype", "")
                    logger.debug(f"Event: type={event_type} subtype={subtype} got_init={got_init}")

                    # The "system" event with subtype "init" marks the end of
                    # history replay. After this, assistant events are new.
                    if event_type == "system" and event.get("subtype") == "init":
                        got_init = True
                        logger.info("Got system:init — history replay done, will capture new events")
                        continue

                    # result and error events are always processed (not history)
                    if event_type in ("result", "error"):
                        pass  # fall through to handling below
                    elif not got_init:
                        # Skip history-replay events (assistant/tool events before init)
                        logger.debug(f"Skipping pre-init event: {event_type}")
                        continue

                    if event_type == "assistant":
                        msg = event.get("message", {})
                        content = msg.get("content", [])
                        text_parts = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use" and on_status:
                                tool_name = block.get("name", "")
                                tool_input = block.get("input", {})
                                status = _format_tool_status(tool_name, tool_input)
                                if status:
                                    on_status(status)
                        if text_parts:
                            new_text = "\n".join(text_parts)
                            if len(new_text) > len(last_new_assistant_text):
                                diff = new_text[len(last_new_assistant_text):]
                                if diff and on_text:
                                    on_text(diff)
                                last_new_assistant_text = new_text

                    elif event_type == "result":
                        result_text = event.get("result", "")
                        stop_reason = event.get("stop_reason", "")
                        is_error = event.get("is_error", False)
                        logger.info(f"Result event: stop_reason={stop_reason} is_error={is_error} got_init={got_init} result_len={len(result_text)} assistant_text_len={len(last_new_assistant_text)}")

                        if is_error or stop_reason in ("refusal", "content_filtered"):
                            err_msg = result_text or f"נעצר: {stop_reason}"
                            if on_error:
                                on_error(f"🚫 {err_msg}")
                            return

                        if result_text and not last_new_assistant_text:
                            last_new_assistant_text = result_text
                            if on_text:
                                on_text(result_text)

                    elif event_type == "error":
                        err = event.get("error", {})
                        err_msg = err.get("message", str(err))
                        if on_error:
                            on_error(f"❌ {err_msg}")
                        return

                full_text = last_new_assistant_text

                # Wait for process to finish
                self._process.wait(timeout=10)

                # Check stderr
                stderr = self._process.stderr.read().strip()
                if stderr:
                    # Check for usage policy / terms of service violations
                    stderr_lower = stderr.lower()
                    if any(term in stderr_lower for term in
                           ["usage policy", "terms of service", "content policy",
                            "safety", "refusal", "blocked", "filtered"]):
                        if on_error:
                            on_error(f"🚫 Claude נעצר — חריגה מתנאי שימוש:\n{stderr[:500]}")
                        return
                    if not full_text:
                        full_text = stderr

                if not full_text:
                    full_text = "(אין פלט)"

                if on_done:
                    on_done(full_text, result_session_id)

            except subprocess.TimeoutExpired:
                if on_error:
                    on_error(f"⏰ timeout ({CLI_TIMEOUT}s)")
            except FileNotFoundError:
                if on_error:
                    on_error("❌ Claude Code CLI לא נמצא")
            except Exception as e:
                if on_error:
                    on_error(f"❌ {e}")
            finally:
                self._process = None
                # Cleanup temp files
                if prompt_path:
                    try:
                        os.unlink(prompt_path)
                    except Exception:
                        pass
                # lock-file cleanup removed 2026-06-10 — see Popen comment above

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def cancel(self):
        """Cancel the running process — kills the full process tree, not just the shell."""
        self._cancelled = True
        if self._process:
            self._kill_tree(self._process.pid)

    @staticmethod
    def _kill_tree(pid: int):
        """Kill a process and all children. Without /T, killing cmd.exe leaves
        the node/claude grandchild orphaned consuming tokens forever."""
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        except Exception:
            try:
                # Fallback: at least kill the direct child
                import os as _os
                _os.kill(pid, 9)
            except Exception:
                pass
