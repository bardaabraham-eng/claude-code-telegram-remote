"""
Bridge to inject prompts into Claude Code running inside VS Code.
Uses Windows API to find the correct VS Code window, focus it,
and send keystrokes via keybd_event.
"""

import ctypes
import ctypes.wintypes
import logging
import time

import pyperclip

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# Key codes
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_F1 = 0x70
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def _key_down(vk):
    user32.keybd_event(vk, 0, 0, 0)

def _key_up(vk):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

def _press(vk, delay=0.05):
    _key_down(vk)
    time.sleep(delay)
    _key_up(vk)
    time.sleep(delay)

def _hotkey(*vks, delay=0.05):
    for vk in vks:
        _key_down(vk)
        time.sleep(delay)
    for vk in reversed(vks):
        _key_up(vk)
        time.sleep(delay)


def _find_vscode_window(project_name: str) -> int | None:
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

    logger.warning(f"No VS Code window found for project '{project_name}'")
    return None


def _force_focus_window(hwnd: int) -> bool:
    """Force bring a window to foreground."""
    try:
        # Restore if minimized
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.3)

        # Find and minimize the bot's own console window to clear the way
        console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if console_hwnd and console_hwnd != hwnd:
            user32.ShowWindow(console_hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.3)

        # Now set target window to foreground
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.2)

        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        user32.AttachThreadInput(current_thread, target_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(current_thread, target_thread, False)

        time.sleep(0.5)

        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            logger.info("Window focused successfully")
        else:
            logger.warning(f"Focus check: fg={fg}, target={hwnd}")

        return True
    except Exception as e:
        logger.error(f"Failed to focus window: {e}")
        return False


def send_prompt_to_ide(project_name: str, prompt: str) -> tuple[bool, str]:
    """
    Send a prompt to Claude Code in the VS Code window for the given project.

    Returns (success: bool, message: str)
    """
    try:
        # 1. Find the VS Code window
        hwnd = _find_vscode_window(project_name)
        if hwnd is None:
            return False, f"❌ לא נמצא חלון VS Code עבור '{project_name}'"

        # 2. Force focus
        if not _force_focus_window(hwnd):
            return False, "❌ לא הצלחתי לפוקס את חלון VS Code"

        # 3. Ctrl+Shift+F1 to focus Claude Code input
        time.sleep(0.3)
        _hotkey(VK_CONTROL, VK_SHIFT, VK_F1)
        time.sleep(1.0)

        # 4. Copy prompt to clipboard and Ctrl+V to paste
        pyperclip.copy(prompt)
        time.sleep(0.1)
        _hotkey(VK_CONTROL, VK_V)
        time.sleep(0.5)

        # 5. Press Enter to submit
        _press(VK_RETURN)

        logger.info(f"Prompt sent to IDE for project '{project_name}': {prompt[:80]}...")
        return True, f"הפרומפט נשלח ל-Claude Code בפרויקט {project_name}"

    except Exception as e:
        logger.error(f"Failed to send prompt to IDE: {e}")
        return False, f"שגיאה בשליחת פרומפט ל-IDE: {e}"
