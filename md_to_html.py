"""
Convert Markdown text (from Claude Code output) to styled HTML for Telegram file attachments.
Handles: tables, headers, bold, code blocks, lists.
"""

import re


CSS = """
body { font-family: -apple-system, Arial, sans-serif; padding: 16px; max-width: 900px;
       margin: 0 auto; background: #1a1a2e; color: #e0e0e0; direction: rtl; }
h1, h2, h3 { color: #64b5f6; border-bottom: 1px solid #333; padding-bottom: 4px; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; direction: ltr; }
th, td { border: 1px solid #555; padding: 6px 10px; text-align: left; }
th { background: #2a2a4a; color: #90caf9; }
tr:nth-child(even) { background: #1e1e3a; }
tr:nth-child(odd) { background: #16162e; }
pre, code { background: #0d0d1a; padding: 2px 6px; border-radius: 3px; font-size: 13px;
            direction: ltr; text-align: left; }
pre { padding: 12px; overflow-x: auto; display: block; }
.emoji { font-size: 1.1em; }
blockquote { border-right: 3px solid #64b5f6; padding-right: 12px; color: #aaa; margin: 8px 0; }
hr { border: none; border-top: 1px solid #444; margin: 16px 0; }
"""


def _escape(text: str) -> str:
    """Escape HTML special chars."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _convert_table(lines: list[str]) -> str:
    """Convert markdown table lines to HTML table."""
    if len(lines) < 2:
        return "\n".join(_escape(l) for l in lines)

    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)

    # Skip separator row (the one with ---)
    header = rows[0]
    data_rows = [r for r in rows[1:] if not all(re.match(r'^[-:]+$', c) for c in r)]

    html = '<table>\n<tr>'
    for cell in header:
        html += f'<th>{_escape(cell)}</th>'
    html += '</tr>\n'
    for row in data_rows:
        html += '<tr>'
        for cell in row:
            html += f'<td>{_escape(cell)}</td>'
        html += '</tr>\n'
    html += '</table>'
    return html


def md_to_html(text: str) -> str:
    """Convert markdown text to a full styled HTML document."""
    lines = text.split("\n")
    html_parts = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Table detection: line starts with |
        if line.strip().startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            html_parts.append(_convert_table(table_lines))
            continue

        # Code block ```
        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            code_text = _escape("\n".join(code_lines))
            html_parts.append(f'<pre><code>{code_text}</code></pre>')
            continue

        # Headers
        if line.startswith("### "):
            html_parts.append(f'<h3>{_escape(line[4:])}</h3>')
            i += 1
            continue
        if line.startswith("## "):
            html_parts.append(f'<h2>{_escape(line[3:])}</h2>')
            i += 1
            continue
        if line.startswith("# "):
            html_parts.append(f'<h1>{_escape(line[2:])}</h1>')
            i += 1
            continue

        # Horizontal rule
        if re.match(r'^---+$', line.strip()):
            html_parts.append('<hr>')
            i += 1
            continue

        # Empty line
        if not line.strip():
            html_parts.append('')
            i += 1
            continue

        # Regular paragraph — apply inline formatting
        escaped = _escape(line)
        # Bold **text**
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
        # Inline code `text`
        escaped = re.sub(r'`(.+?)`', r'<code>\1</code>', escaped)
        # List items
        if re.match(r'^[-*]\s', line.strip()):
            escaped = '• ' + escaped.strip().lstrip('-*').strip()

        html_parts.append(f'<p>{escaped}</p>')
        i += 1

    body = "\n".join(html_parts)
    return (
        '<!DOCTYPE html>\n'
        '<html lang="he">\n'
        f'<head><meta charset="utf-8"><style>{CSS}</style></head>\n'
        f'<body>{body}</body>\n'
        '</html>'
    )
