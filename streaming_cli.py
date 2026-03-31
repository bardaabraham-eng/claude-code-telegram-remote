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


def find_latest_session_id(project_path: str) -> str | None:
    """
    Find the most recent Claude Code session ID for a project.
    Looks at ~/.claude/projects/<project-key>/*.jsonl files.
    Works for both IDE and CLI sessions.
    """
    try:
        home = os.path.expanduser("~")
        # Convert project path to Claude Code's key format
        norm = os.path.normpath(project_path).replace("\\", "-").replace("/", "-").replace(":", "")
        # Try lowercase version (Claude Code uses lowercase)
        projects_dir = os.path.join(home, ".claude", "projects")
        if not os.path.isdir(projects_dir):
            return None

        # Find matching project directory
        target_key = norm.lower()
        matched_dir = None
        for entry in os.listdir(projects_dir):
            if entry.lower() == target_key:
                matched_dir = os.path.join(projects_dir, entry)
                break

        if not matched_dir or not os.path.isdir(matched_dir):
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


class StreamingCLI:
    """Run Claude Code CLI with streaming output."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def run_streaming(self, prompt: str, cwd: str = None,
                      session_id: str = None,
                      on_text=None, on_done=None, on_error=None):
        """
        Run claude -p with streaming. Calls back:
          on_text(chunk: str) — each text chunk as it arrives
          on_done(full_text: str, session_id: str) — when complete
          on_error(error: str) — on failure

        Returns immediately, runs in background thread.
        """
        self._cancelled = False

        def _run():
            import tempfile

            MAX_CMD_PROMPT = 6000  # Safe limit for Windows command line

            prompt_path = None
            if len(prompt) > MAX_CMD_PROMPT:
                # Save long prompt to temp file
                prompt_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".md", delete=False,
                    encoding="utf-8", dir=cwd or None, prefix=".prompt_"
                )
                prompt_file.write(prompt)
                prompt_file.close()
                prompt_path = prompt_file.name
                cli_prompt = f"Read the file {prompt_path} and follow the instructions in it."
            else:
                cli_prompt = prompt

            cmd = [CLAUDE_CMD, "-p", cli_prompt,
                   "--output-format", "stream-json", "--verbose"]

            if session_id:
                cmd.extend(["--resume", session_id])
            else:
                cmd.append("-c")

            logger.info(f"Streaming CLI: cwd={cwd}, session={session_id or 'continue'}, prompt={prompt[:80]}...")

            try:
                # Build command string
                cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
                logger.info(f"CLI command: {cmd_str[:200]}")

                # Set env var + lock file so Stop hook knows not to send duplicate
                env = os.environ.copy()
                env["TELEGRAM_BOT_SESSION"] = "1"

                # Write lock file with session info
                lock_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), ".bot_active_session"
                )
                try:
                    with open(lock_path, "w") as lf:
                        lf.write(result_session_id or "active")
                except Exception:
                    pass

                self._process = subprocess.Popen(
                    cmd_str,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    shell=True,
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
                        self._process.terminate()
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

                    # The "system" event with subtype "init" marks the end of
                    # history replay. After this, assistant events are new.
                    if event_type == "system" and event.get("subtype") == "init":
                        got_init = True
                        continue

                    # Skip events before init (they are history replay)
                    if not got_init:
                        continue

                    if event_type == "assistant":
                        msg = event.get("message", {})
                        content = msg.get("content", [])
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        if text_parts:
                            new_text = "\n".join(text_parts)
                            if len(new_text) > len(last_new_assistant_text):
                                diff = new_text[len(last_new_assistant_text):]
                                if diff and on_text:
                                    on_text(diff)
                                last_new_assistant_text = new_text

                    elif event_type == "result":
                        result_text = event.get("result", "")
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
                if stderr and not full_text:
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
                try:
                    os.unlink(lock_path)
                except Exception:
                    pass

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def cancel(self):
        """Cancel the running process."""
        self._cancelled = True
        if self._process:
            try:
                self._process.terminate()
            except Exception:
                pass
