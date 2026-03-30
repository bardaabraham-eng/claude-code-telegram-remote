"""
Detect open VS Code windows and their working directories on Windows.
Uses window titles which follow the pattern: "filename - FolderName - Visual Studio Code"
"""

import ctypes
import ctypes.wintypes
import logging
import os
import re

logger = logging.getLogger(__name__)


def _enum_windows() -> list[dict]:
    """Enumerate all visible windows and return their titles + PIDs."""
    results = []

    EnumWindows = ctypes.windll.user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )
    GetWindowTextW = ctypes.windll.user32.GetWindowTextW
    GetWindowTextLengthW = ctypes.windll.user32.GetWindowTextLengthW
    IsWindowVisible = ctypes.windll.user32.IsWindowVisible

    def callback(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        results.append({"hwnd": hwnd, "title": buf.value})
        return True

    EnumWindows(EnumWindowsProc(callback), 0)
    return results


def get_vscode_workspaces() -> list[dict]:
    """
    Detect all open VS Code windows and extract their working directories.
    Returns a list of dicts: [{"name": "project-name", "path": "C:\\...\\project-name"}, ...]
    """
    windows = _enum_windows()
    workspaces = []
    seen_paths = set()

    for win in windows:
        title = win["title"]
        # VS Code window titles end with "Visual Studio Code"
        if not title.endswith("Visual Studio Code"):
            continue

        # Parse the title to extract folder info
        # Patterns:
        #   "FolderName - Visual Studio Code"
        #   "filename.py - FolderName - Visual Studio Code"
        #   "filename.py - path/to/folder - Visual Studio Code" (with remote)
        cleaned = re.sub(r"\s*[-—]\s*Visual Studio Code$", "", title).strip()

        if not cleaned:
            continue

        # The last segment after " - " is usually the folder/workspace name
        parts = cleaned.rsplit(" - ", 1)
        folder_name = parts[-1].strip() if parts else cleaned.strip()

        # Try to resolve to a real path
        folder_path = _resolve_folder_path(folder_name)

        if folder_path and folder_path not in seen_paths:
            seen_paths.add(folder_path)
            display_name = os.path.basename(folder_path)
            workspaces.append({
                "name": display_name,
                "path": folder_path,
                "title": title,
            })

    logger.info(f"Detected {len(workspaces)} VS Code workspace(s): {[w['name'] for w in workspaces]}")
    return workspaces


def _resolve_folder_path(folder_name: str) -> str | None:
    """
    Try to resolve a folder name from VS Code title to an actual path.
    VS Code may show just the folder name or a full path.
    """
    # If it's already an absolute path
    if os.path.isabs(folder_name) and os.path.isdir(folder_name):
        return os.path.normpath(folder_name)

    # Check common parent directories (including one level of subdirs)
    roots = [
        os.path.expanduser("~"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Projects"),
        os.path.expanduser("~/repos"),
        os.path.expanduser("~/source/repos"),
    ]

    for root in roots:
        # Direct match
        candidate = os.path.join(root, folder_name)
        if os.path.isdir(candidate):
            return os.path.normpath(candidate)
        # One level deeper (e.g., ~/Desktop/SU/Ayit)
        if os.path.isdir(root):
            try:
                for sub in os.listdir(root):
                    candidate = os.path.join(root, sub, folder_name)
                    if os.path.isdir(candidate):
                        return os.path.normpath(candidate)
            except PermissionError:
                pass

    # Try to find via recent VS Code storage
    storage_path = _check_vscode_storage(folder_name)
    if storage_path:
        return storage_path

    return None


def _check_vscode_storage(folder_name: str) -> str | None:
    """Check VS Code's recent storage for the folder path."""
    try:
        import json
        storage_file = os.path.join(
            os.environ.get("APPDATA", ""),
            "Code", "storage.json"
        )
        if not os.path.exists(storage_file):
            return None

        with open(storage_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Look in recently opened paths
        opened = data.get("openedPathsList", {})
        entries = opened.get("workspaces3", []) + opened.get("entries", [])

        for entry in entries:
            if isinstance(entry, str):
                path = entry.replace("file:///", "").replace("/", os.sep)
            elif isinstance(entry, dict):
                path = entry.get("folderUri", entry.get("configPath", ""))
                path = path.replace("file:///", "").replace("/", os.sep)
            else:
                continue

            # URL decode
            try:
                from urllib.parse import unquote
                path = unquote(path)
            except Exception:
                pass

            if path and os.path.basename(path.rstrip(os.sep)) == folder_name:
                path = os.path.normpath(path)
                if os.path.isdir(path):
                    return path

    except Exception as e:
        logger.debug(f"Could not read VS Code storage: {e}")

    return None
