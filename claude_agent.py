"""
Claude agent that sends prompts to Claude Code.
Two modes:
  1. IDE mode: inject prompt into VS Code via keyboard automation
  2. CLI mode: run `claude -p` directly when VS Code is not available
"""

import logging
import os
import subprocess

from config import CLI_TIMEOUT
from ide_bridge import send_prompt_to_ide

logger = logging.getLogger(__name__)

# Full path to Claude Code CLI
CLAUDE_CMD = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")


class ClaudeAgent:
    """Sends prompts to Claude Code via IDE or CLI."""

    def process_text(self, text: str, project_name: str = None, cwd: str = None, mode: str = "ide") -> str:
        """Send a text prompt. mode='ide' for VS Code injection, mode='cli' for claude -p."""
        if mode == "cli":
            return self._run_cli(text, cwd)

        if not project_name:
            return "❌ לא נבחר פרויקט. שלח שוב ובחר פרויקט מהרשימה."

        success, message = send_prompt_to_ide(project_name, text)
        return message

    def process_image(self, image_bytes: bytes, caption: str = "", project_name: str = None, cwd: str = None, mode: str = "ide") -> str:
        """Save image and send to Claude Code."""
        import tempfile

        save_dir = cwd or os.path.join(os.path.expanduser("~"), "Desktop")
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=save_dir, prefix="telegram_") as f:
                f.write(image_bytes)
                tmp_path = f.name

            prompt = f"שמרתי תמונה בנתיב: {tmp_path}"
            if caption:
                prompt += f"\n{caption}"
            else:
                prompt += "\nתאר מה יש בתמונה."

            if mode == "cli":
                return self._run_cli(prompt, cwd)

            if not project_name:
                return "❌ לא נבחר פרויקט."
            success, message = send_prompt_to_ide(project_name, prompt)
            return message
        except Exception as e:
            return f"שגיאה: {e}"

    def process_pdf(self, pdf_bytes: bytes, caption: str = "", project_name: str = None, cwd: str = None, mode: str = "ide") -> str:
        """Extract PDF text and send to Claude Code."""
        import io
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text_parts = []
            for i, page in enumerate(reader.pages[:10]):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            pdf_text = "\n".join(text_parts)[:3000]
        except Exception as e:
            pdf_text = f"(שגיאה בקריאת PDF: {e})"

        prompt = f"תוכן PDF:\n{pdf_text}"
        if caption:
            prompt += f"\n\n{caption}"

        if mode == "cli":
            return self._run_cli(prompt, cwd)

        if not project_name:
            return "❌ לא נבחר פרויקט."
        success, message = send_prompt_to_ide(project_name, prompt)
        return message

    def _run_cli(self, prompt: str, cwd: str = None) -> str:
        """Run Claude Code CLI with `claude -p` for non-interactive mode."""
        cmd = [CLAUDE_CMD, "-p", prompt, "-c", "--output-format", "text"]

        logger.info(f"Running Claude CLI in {cwd or 'default dir'}, prompt: {prompt[:100]}...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT,
                cwd=cwd,
                encoding="utf-8",
                errors="replace",
                shell=True,
            )

            logger.info(f"CLI exit code: {result.returncode}")

            output = ""
            if result.stdout:
                output = result.stdout.strip()
            if result.stderr:
                stderr = result.stderr.strip()
                if stderr and not output:
                    output = stderr
                elif stderr:
                    logger.warning(f"CLI stderr: {stderr[:200]}")

            if not output:
                output = "(אין פלט מ-Claude Code)"

            # Truncate very long output
            if len(output) > 15000:
                output = output[:15000] + f"\n\n... [קוצר, {len(output)} תווים סה\"כ]"

            return output

        except subprocess.TimeoutExpired:
            return f"⏰ Claude Code לא הגיב תוך {CLI_TIMEOUT} שניות."
        except FileNotFoundError:
            return (
                "❌ Claude Code CLI לא נמצא. וודא שהוא מותקן:\n"
                "npm install -g @anthropic-ai/claude-code"
            )
        except Exception as e:
            return f"❌ שגיאה: {e}"
