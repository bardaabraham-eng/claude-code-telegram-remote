"""
Convert Markdown text (from Claude Code output) to styled HTML for Telegram file attachments.
Handles: tables, headers, bold, code blocks, lists.
"""

import re


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700;900&family=Inter:wght@300;400;500;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

body { font-family: 'Heebo', 'Inter', Arial, sans-serif; padding: 24px; max-width: 960px;
       margin: 0 auto; background: #0A0A0A; color: #F8F9FA; direction: rtl; line-height: 1.7;
       font-size: 15px; }
h1 { color: #FFB300; border-bottom: 2px solid rgba(255,179,0,0.30); padding-bottom: 10px;
     font-weight: 900; font-size: 1.7em; margin-top: 28px; letter-spacing: -0.02em; }
h2 { color: #FFB300; border-bottom: 1px solid #2C3E50; padding-bottom: 8px;
     font-weight: 700; font-size: 1.3em; margin-top: 24px; }
h3 { color: #F8F9FA; font-weight: 700; font-size: 1.1em; margin-top: 18px; }
p { margin: 6px 0; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; direction: ltr; border-radius: 2px;
        overflow: hidden; border: 1px solid #2C3E50; }
th, td { border: 1px solid #2C3E50; padding: 10px 14px; text-align: left; font-size: 0.9em; }
th { background: #1A1D24; color: #FFB300; font-weight: 700; text-transform: uppercase; font-size: 0.8em;
     letter-spacing: 0.5px; }
td { background: #111318; }
tr:nth-child(even) td { background: #0A0A0A; }
tr:hover td { background: rgba(255,179,0,0.05); }
pre, code { font-family: 'IBM Plex Mono', 'Courier New', monospace; background: #111318;
            padding: 2px 6px; border-radius: 2px; font-size: 13px; direction: ltr; text-align: left;
            color: #63B3ED; border: 1px solid #2C3E50; }
pre { padding: 16px; overflow-x: auto; display: block; margin: 10px 0; }
blockquote { border-right: 3px solid #FFB300; padding-right: 16px; color: #8899AA; margin: 14px 0;
             background: rgba(255,179,0,0.04); padding: 12px 16px; border-radius: 2px; }
hr { border: none; border-top: 1px solid #2C3E50; margin: 24px 0; }
a { color: #FFB300; text-decoration: none; }
a:hover { color: #FFC940; text-decoration: underline; }
b, strong { color: #F8F9FA; font-weight: 700; }
.score-critical { color: #E53E3E; font-weight: 900; }
.score-high { color: #FFB300; font-weight: 700; }
.score-notable { color: #63B3ED; }
.badge { display: inline-block; padding: 3px 10px; border-radius: 2px; font-size: 0.75em;
         font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
.badge-critical { background: rgba(229,62,62,0.10); color: #E53E3E; border: 1px solid rgba(229,62,62,0.20); }
.badge-high { background: rgba(255,179,0,0.10); color: #FFB300; border: 1px solid rgba(255,179,0,0.30); }
.badge-success { background: rgba(0,200,100,0.10); color: #00C864; border: 1px solid rgba(0,200,100,0.20); }
.badge-info { background: rgba(99,179,237,0.10); color: #63B3ED; border: 1px solid rgba(99,179,237,0.20); }
.header-bar { background: #111318; border: 1px solid #2C3E50; border-radius: 2px;
              padding: 18px; margin-bottom: 24px; }
.header-bar p { margin: 4px 0; }
.footer { margin-top: 32px; padding-top: 18px; border-top: 1px solid #2C3E50;
          color: #4A5568; font-size: 0.8em; text-align: center; }
.ayit-logo { display: inline-block; color: #FFB300; font-weight: 900; font-size: 0.9em;
             letter-spacing: 2px; text-transform: uppercase; }
.section-he { direction: rtl; text-align: right; }
.section-en { direction: ltr; text-align: left; font-family: 'Inter', 'IBM Plex Sans', Arial, sans-serif;
              border-top: 2px solid #2C3E50; margin-top: 32px; padding-top: 24px; }
.section-en h1 { border-bottom-color: #2C3E50; }
.section-en table { direction: ltr; }
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
            heading_text = line[2:]
            # Switch to LTR section when hitting English-only heading
            if re.match(r'^Part B\b', heading_text, re.IGNORECASE):
                html_parts.append('</div>')
                html_parts.append('<div class="section-en" dir="ltr" style="direction:ltr; text-align:left;">')
                html_parts.append(f'<h1>{_escape(heading_text)}</h1>')
            else:
                html_parts.append(f'<h1>{_escape(heading_text)}</h1>')
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
    has_part_b = '<div class="section-en"' in body
    if has_part_b:
        body = '<div class="section-he">' + body + '</div>'
    else:
        body = '<div class="section-he">' + body + '</div>'
    return (
        '<!DOCTYPE html>\n'
        '<html dir="rtl" lang="he">\n'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>Ayit Brief</title>\n'
        f'<style>{CSS}</style></head>\n'
        f'<body>{body}\n'
        '<div class="footer"><span class="ayit-logo">AYIT</span> &mdash; Sovereign Hybrid Security Operating System</div>\n'
        '</body>\n</html>'
    )
