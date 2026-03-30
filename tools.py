"""
Tool definitions and implementations for the Claude agent.
Each tool has a schema (for the API) and an execute function.
"""

import json
import os
import subprocess
import shutil
import tempfile

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from config import MAX_TOOL_OUTPUT, PYTHON_TIMEOUT, TERMINAL_TIMEOUT

# ---------------------------------------------------------------------------
# Tool schemas (sent to Claude)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS = [
    {
        "name": "run_python",
        "description": "Execute Python code and return the output. Use for calculations, data processing, scripts, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                }
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_terminal",
        "description": "Run a shell command (git, npm, pip, ls, curl, etc.) and return the output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "install_package",
        "description": "Install a Python package using pip.",
        "input_schema": {
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": "Package name (e.g. 'requests' or 'flask==2.0.0')",
                }
            },
            "required": ["package"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to read",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates directories if needed. Overwrites existing files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories at the given path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path to list (default: current directory)",
                    "default": ".",
                }
            },
            "required": [],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file or directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file or directory to delete",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo. Returns top results with titles, URLs, and snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default: 5)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_url",
        "description": "Fetch and read the text content of a web page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to read",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "http_request",
        "description": "Make an HTTP request (GET, POST, PUT, DELETE) to an API endpoint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                    "description": "HTTP method",
                },
                "url": {
                    "type": "string",
                    "description": "Request URL",
                },
                "headers": {
                    "type": "object",
                    "description": "Request headers (optional)",
                    "default": {},
                },
                "body": {
                    "type": "string",
                    "description": "Request body as JSON string (optional for POST/PUT)",
                    "default": "",
                },
            },
            "required": ["method", "url"],
        },
    },
    {
        "name": "git_operation",
        "description": "Perform a git operation: status, add, commit, push, pull, log, diff.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["status", "add", "commit", "push", "pull", "log", "diff"],
                    "description": "Git operation to perform",
                },
                "args": {
                    "type": "string",
                    "description": "Additional arguments (e.g., file path for add, message for commit)",
                    "default": "",
                },
            },
            "required": ["operation"],
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _truncate(text: str) -> str:
    """Truncate output to MAX_TOOL_OUTPUT characters."""
    text = str(text)
    if len(text) > MAX_TOOL_OUTPUT:
        return text[:MAX_TOOL_OUTPUT] + f"\n\n... [truncated, {len(text)} total chars]"
    return text


def run_python(code: str) -> str:
    """Execute Python code in a subprocess."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name
        result = subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            timeout=PYTHON_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
        os.unlink(tmp_path)
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- STDERR ---\n" + result.stderr) if output else result.stderr
        return _truncate(output or "(no output)")
    except subprocess.TimeoutExpired:
        return f"Error: execution timed out after {PYTHON_TIMEOUT} seconds."
    except Exception as e:
        return f"Error: {e}"


def run_terminal(command: str) -> str:
    """Run a shell command."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=TERMINAL_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n--- STDERR ---\n" + result.stderr) if output else result.stderr
        return _truncate(output or "(no output)")
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {TERMINAL_TIMEOUT} seconds."
    except Exception as e:
        return f"Error: {e}"


def install_package(package: str) -> str:
    """Install a pip package."""
    return run_terminal(f"pip install {package}")


def read_file(path: str) -> str:
    """Read a file and return its contents."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return _truncate(f.read())
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written successfully: {path} ({len(content)} chars)"
    except Exception as e:
        return f"Error writing file: {e}"


def list_files(path: str = ".") -> str:
    """List files in a directory."""
    try:
        entries = os.listdir(path)
        result_lines = []
        for entry in sorted(entries):
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                result_lines.append(f"📁 {entry}/")
            else:
                size = os.path.getsize(full)
                result_lines.append(f"📄 {entry} ({size} bytes)")
        return _truncate("\n".join(result_lines) or "(empty directory)")
    except Exception as e:
        return f"Error listing files: {e}"


def delete_file(path: str) -> str:
    """Delete a file or directory."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
            return f"Directory deleted: {path}"
        else:
            os.remove(path)
            return f"File deleted: {path}"
    except Exception as e:
        return f"Error deleting: {e}"


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo."""
    try:
        results = DDGS().text(query, max_results=max_results)
        if not results:
            return "No results found."
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', 'No title')}")
            lines.append(f"   URL: {r.get('href', 'N/A')}")
            lines.append(f"   {r.get('body', '')}")
            lines.append("")
        return _truncate("\n".join(lines))
    except Exception as e:
        return f"Error searching: {e}"


def read_url(url: str) -> str:
    """Fetch and extract text from a web page."""
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts and styles
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return _truncate(text)
    except Exception as e:
        return f"Error reading URL: {e}"


def http_request(method: str, url: str, headers: dict = None, body: str = "") -> str:
    """Make an HTTP request."""
    try:
        kwargs = {
            "method": method,
            "url": url,
            "headers": headers or {},
            "timeout": 30,
        }
        if body and method in ("POST", "PUT"):
            kwargs["headers"].setdefault("Content-Type", "application/json")
            kwargs["data"] = body
        resp = requests.request(**kwargs)
        result = f"Status: {resp.status_code}\n"
        try:
            result += json.dumps(resp.json(), indent=2, ensure_ascii=False)
        except Exception:
            result += resp.text
        return _truncate(result)
    except Exception as e:
        return f"Error: {e}"


def git_operation(operation: str, args: str = "") -> str:
    """Perform a git operation."""
    cmd_map = {
        "status": "git status",
        "add": f"git add {args}" if args else "git add .",
        "commit": f'git commit -m "{args}"' if args else 'git commit -m "auto commit"',
        "push": f"git push {args}" if args else "git push",
        "pull": f"git pull {args}" if args else "git pull",
        "log": f"git log --oneline -20 {args}",
        "diff": f"git diff {args}",
    }
    cmd = cmd_map.get(operation)
    if not cmd:
        return f"Unknown git operation: {operation}"
    return run_terminal(cmd)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "run_python": lambda inp: run_python(inp["code"]),
    "run_terminal": lambda inp: run_terminal(inp["command"]),
    "install_package": lambda inp: install_package(inp["package"]),
    "read_file": lambda inp: read_file(inp["path"]),
    "write_file": lambda inp: write_file(inp["path"], inp["content"]),
    "list_files": lambda inp: list_files(inp.get("path", ".")),
    "delete_file": lambda inp: delete_file(inp["path"]),
    "web_search": lambda inp: web_search(inp["query"], inp.get("max_results", 5)),
    "read_url": lambda inp: read_url(inp["url"]),
    "http_request": lambda inp: http_request(
        inp["method"], inp["url"], inp.get("headers"), inp.get("body", "")
    ),
    "git_operation": lambda inp: git_operation(inp["operation"], inp.get("args", "")),
}


def execute_tool(name: str, input_data: dict) -> str:
    """Execute a tool by name with the given input. Returns the result string."""
    func = TOOL_FUNCTIONS.get(name)
    if not func:
        return f"Unknown tool: {name}"
    try:
        return func(input_data)
    except Exception as e:
        return f"Tool execution error: {e}"
