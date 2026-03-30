"""
Claude agent that injects prompts into Claude Code running in VS Code.
Uses Windows automation to send text to the IDE's Claude Code input.
Output is sent back via the Stop hook (notify_telegram.py).
"""

import logging

from ide_bridge import send_prompt_to_ide

logger = logging.getLogger(__name__)


class ClaudeAgent:
    """Sends prompts to Claude Code inside VS Code."""

    def process_text(self, text: str, project_name: str = None) -> str:
        """Send a text prompt to Claude Code in the IDE."""
        if not project_name:
            return "❌ לא נבחר פרויקט. שלח שוב ובחר פרויקט מהרשימה."

        success, message = send_prompt_to_ide(project_name, text)
        return message

    def process_image(self, image_bytes: bytes, caption: str = "", project_name: str = None) -> str:
        """Save image and tell Claude Code about it."""
        import os
        import tempfile

        if not project_name:
            return "❌ לא נבחר פרויקט."

        try:
            # Save image to project directory so Claude Code can see it
            tmp_dir = os.path.join(os.path.expanduser("~"), "Desktop")
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=tmp_dir, prefix="telegram_") as f:
                f.write(image_bytes)
                tmp_path = f.name

            prompt = f"שמרתי תמונה בנתיב: {tmp_path}"
            if caption:
                prompt += f"\n{caption}"
            else:
                prompt += "\nתאר מה יש בתמונה."

            success, message = send_prompt_to_ide(project_name, prompt)
            return message
        except Exception as e:
            return f"שגיאה: {e}"

    def process_pdf(self, pdf_bytes: bytes, caption: str = "", project_name: str = None) -> str:
        """Extract PDF text and send to Claude Code."""
        import io
        if not project_name:
            return "❌ לא נבחר פרויקט."

        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text_parts = []
            for i, page in enumerate(reader.pages[:10]):  # limit to 10 pages
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
            pdf_text = "\n".join(text_parts)[:3000]
        except Exception as e:
            pdf_text = f"(שגיאה בקריאת PDF: {e})"

        prompt = f"תוכן PDF:\n{pdf_text}"
        if caption:
            prompt += f"\n\n{caption}"

        success, message = send_prompt_to_ide(project_name, prompt)
        return message
