"""
Bridge between Telegram bot and VS Code.
- Find VS Code windows by project name
- Close VS Code window (to free the session for CLI)
- Open VS Code on a project directory
"""

import ctypes
import ctypes.wintypes
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

WM_CLOSE = 0x0010


def find_vscode_window(project_name: str) -> int | None:
    """Find a VS Code window handle (hwnd) that contains the project name in its title."""
    results = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
    )

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title.endswith("Visual Studio Code") and project_name.lower() in title.lower():
            results.append((hwnd, title))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)

    if results:
        hwnd, title = results[0]
        logger.info(f"Found VS Code window: '{title}' (hwnd={hwnd})")
        return hwnd

    return None


def close_vscode_window(project_name: str) -> bool:
    """Close the VS Code window for the given project. Returns True if found and closed."""
    hwnd = find_vscode_window(project_name)
    if not hwnd:
        logger.info(f"No VS Code window to close for '{project_name}'")
        return False

    logger.info(f"Closing VS Code window for '{project_name}' (hwnd={hwnd})")
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    # Wait for window to close
    for _ in range(20):  # up to 10 seconds
        time.sleep(0.5)
        if not user32.IsWindow(hwnd):
            logger.info(f"VS Code window closed for '{project_name}'")
            return True

    logger.warning(f"VS Code window for '{project_name}' did not close in time")
    return True  # Proceed anyway


def is_vscode_open(project_name: str) -> bool:
    """Check if VS Code is open for the given project."""
    return find_vscode_window(project_name) is not None


def open_vscode(project_path: str) -> bool:
    """Open VS Code on a project directory."""
    try:
        subprocess.Popen(["code", project_path], shell=True)
        logger.info(f"Opened VS Code on '{project_path}'")
        return True
    except Exception as e:
        logger.error(f"Failed to open VS Code: {e}")
        return False
