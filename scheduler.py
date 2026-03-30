"""
Task scheduler using APScheduler.
Allows scheduling recurring tasks that send results to Telegram.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Manages scheduled tasks that run via the Claude agent."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.tasks: dict[str, dict] = {}
        self._next_id = 1

    def start(self):
        """Start the scheduler."""
        self.scheduler.start()
        logger.info("Scheduler started.")

    def add_task(
        self,
        hour: int,
        minute: int,
        description: str,
        callback,
    ) -> str:
        """
        Schedule a daily recurring task.
        callback: async function(description) that processes and sends the result.
        Returns the task ID.
        """
        task_id = str(self._next_id)
        self._next_id += 1

        trigger = CronTrigger(hour=hour, minute=minute)

        self.scheduler.add_job(
            callback,
            trigger=trigger,
            args=[description],
            id=task_id,
            name=description,
        )

        self.tasks[task_id] = {
            "id": task_id,
            "time": f"{hour:02d}:{minute:02d}",
            "description": description,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

        logger.info(f"Task {task_id} scheduled at {hour:02d}:{minute:02d}: {description}")
        return task_id

    def remove_task(self, task_id: str) -> bool:
        """Remove a scheduled task. Returns True if found and removed."""
        if task_id in self.tasks:
            try:
                self.scheduler.remove_job(task_id)
            except Exception:
                pass
            del self.tasks[task_id]
            logger.info(f"Task {task_id} removed.")
            return True
        return False

    def get_tasks(self) -> list[dict]:
        """Return a list of all scheduled tasks."""
        return list(self.tasks.values())

    def stop(self):
        """Shut down the scheduler."""
        self.scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
