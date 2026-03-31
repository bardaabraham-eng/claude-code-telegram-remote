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
            cmd = [CLAUDE_CMD, "-p", prompt, "--output-format", "stream-json", "--verbose"]

            if session_id:
                cmd.extend(["--resume", session_id])
            else:
                cmd.append("-c")  # continue last session

            logger.info(f"Streaming CLI: cwd={cwd}, session={session_id or 'continue'}, prompt={prompt[:80]}...")

            try:
                # Build command string for shell
                cmd_str = " ".join(f'"{c}"' if " " in c else c for c in cmd)
                logger.info(f"CLI command: {cmd_str[:200]}")

                self._process = subprocess.Popen(
                    cmd_str,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    shell=True,
                    encoding="utf-8",
                    errors="replace",
                )

                full_text = ""
                result_session_id = session_id or ""

                # Read line by line (each JSON event is one line)
                while True:
                    line = self._process.stdout.readline()
                    if not line:
                        # Process ended
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
                        logger.debug(f"Non-JSON line: {line[:100]}")
                        continue

                    event_type = event.get("type", "")
                    logger.info(f"Stream event: {event_type}")

                    # Track session ID from any event
                    sid = event.get("session_id", "")
                    if sid:
                        result_session_id = sid

                    if event_type == "assistant":
                        # Assistant message — extract text content
                        msg = event.get("message", {})
                        content = msg.get("content", [])
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text and len(text) > len(full_text):
                                    new_part = text[len(full_text):]
                                    if new_part and on_text:
                                        on_text(new_part)
                                    full_text = text

                    elif event_type == "result":
                        # Final result
                        result_text = event.get("result", "")
                        if result_text and not full_text:
                            full_text = result_text
                            if on_text:
                                on_text(result_text)

                    elif event_type == "error":
                        err = event.get("error", {})
                        err_msg = err.get("message", str(err))
                        if on_error:
                            on_error(f"❌ {err_msg}")
                        return

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
