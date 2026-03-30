"""
Session manager — tracks Claude Code CLI sessions per project.
Stores session IDs, names, and thread message IDs for Telegram.
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

SESSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sessions.json")


class SessionManager:
    """Manages Claude Code CLI sessions per project."""

    def __init__(self):
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.exists(SESSIONS_FILE):
                with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load sessions: {e}")
        return {"projects": {}}

    def _save(self):
        try:
            with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Could not save sessions: {e}")

    def get_sessions(self, project_path: str) -> list[dict]:
        """Get all sessions for a project. Most recent first."""
        key = self._project_key(project_path)
        sessions = self._data.get("projects", {}).get(key, {}).get("sessions", [])
        return sorted(sessions, key=lambda s: s.get("last_used", 0), reverse=True)

    def get_last_session(self, project_path: str) -> dict | None:
        """Get the most recent session for a project."""
        sessions = self.get_sessions(project_path)
        return sessions[0] if sessions else None

    def save_session(self, project_path: str, session_id: str, label: str = "",
                     thread_msg_id: int = None):
        """Save or update a session."""
        key = self._project_key(project_path)
        if key not in self._data.get("projects", {}):
            self._data.setdefault("projects", {})[key] = {
                "path": project_path,
                "sessions": [],
            }

        sessions = self._data["projects"][key]["sessions"]

        # Update existing or add new
        existing = next((s for s in sessions if s["id"] == session_id), None)
        if existing:
            existing["last_used"] = time.time()
            if label:
                existing["label"] = label
            if thread_msg_id:
                existing["thread_msg_id"] = thread_msg_id
        else:
            sessions.append({
                "id": session_id,
                "label": label or self._auto_label(session_id),
                "created": time.time(),
                "last_used": time.time(),
                "thread_msg_id": thread_msg_id,
            })

        # Keep max 10 sessions per project
        if len(sessions) > 10:
            sessions.sort(key=lambda s: s.get("last_used", 0), reverse=True)
            self._data["projects"][key]["sessions"] = sessions[:10]

        self._save()

    def get_thread_msg_id(self, project_path: str, session_id: str) -> int | None:
        """Get the Telegram thread message ID for a session."""
        sessions = self.get_sessions(project_path)
        session = next((s for s in sessions if s["id"] == session_id), None)
        return session.get("thread_msg_id") if session else None

    def set_thread_msg_id(self, project_path: str, session_id: str, msg_id: int):
        """Set the Telegram thread message ID for a session."""
        key = self._project_key(project_path)
        sessions = self._data.get("projects", {}).get(key, {}).get("sessions", [])
        session = next((s for s in sessions if s["id"] == session_id), None)
        if session:
            session["thread_msg_id"] = msg_id
            self._save()

    def _project_key(self, path: str) -> str:
        """Normalize project path to a consistent key."""
        return os.path.normpath(path).lower()

    def _auto_label(self, session_id: str) -> str:
        """Generate a short label from session ID."""
        return session_id[:8] if session_id else "new"
