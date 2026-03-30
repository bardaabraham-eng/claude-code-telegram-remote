"""
Conversation memory management.
Keeps the last N messages for context.
"""

from config import MAX_HISTORY


class Memory:
    """Simple conversation memory with a rolling window."""

    def __init__(self):
        self.messages: list[dict] = []

    def add(self, role: str, content):
        """Add a message to history. Content can be str or list (for images)."""
        self.messages.append({"role": role, "content": content})
        # Trim to keep only the last MAX_HISTORY messages
        if len(self.messages) > MAX_HISTORY:
            self.messages = self.messages[-MAX_HISTORY:]

    def get_messages(self) -> list[dict]:
        """Return the current conversation history."""
        return list(self.messages)

    def clear(self):
        """Clear all conversation history."""
        self.messages.clear()

    def count(self) -> int:
        """Return the number of messages in memory."""
        return len(self.messages)
