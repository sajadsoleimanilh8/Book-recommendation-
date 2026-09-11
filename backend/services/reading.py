"""Reading progress and reminders — the ReminderEngine.

Extracted verbatim from `engine.py` in the Phase C restructure, along
with NOTIFY_AT, the module constant only this class uses (grep
confirmed no other reference in engine.py).

Unlike the other sub-engines, this one takes no Recommender reference —
its state (reminders, progress, sessions) is entirely its own, so no
TYPE_CHECKING import is needed here.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from domain.entities import Reminder, UserProfile

log = logging.getLogger(__name__)

NOTIFY_AT = [25, 50, 75, 100]


class ReminderEngine:
    def __init__(self):
        self._reminders: Dict[str, Dict[int, Reminder]] = {}
        self._progress: Dict[str, Dict[int, float]] = {}
        self._sessions: Dict[str, Dict[int, Tuple[float, float]]] = {}
        self._daemon = threading.Thread(target=self._loop, daemon=True)
        self._daemon.start()
        log.info("Reminder daemon started.")

    def update_progress(
        self,
        user_id: str,
        book_id: int,
        progress: float,
        total_pages: int = 0,
        profile: Optional[UserProfile] = None,
    ) -> Dict[str, Any]:
        progress = max(0.0, min(1.0, progress))
        store = self._progress.setdefault(user_id, {})
        store[book_id] = progress
        pct = int(progress * 100)

        return {
            "user_id": user_id,
            "book_id": book_id,
            "progress": progress,
            "percent": pct,
        }

    def set_reminder(self, user_id: str, book_id: int, title: str,
                     enabled: bool = True) -> Optional[Reminder]:
        store = self._reminders.setdefault(user_id, {})
        if not enabled:
            store.pop(book_id, None)
            return None

        reminder = Reminder(
            book_id=book_id, title=title, user_id=user_id,
            enabled=True, last_notified=0, last_message="",
            last_fired_at="", created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        store[book_id] = reminder
        log.info(f"Reminder set: {user_id} → '{title}'")
        return reminder

    def get_user_reminders(self, user_id: str) -> List[Dict]:
        reminders = self._reminders.get(user_id, {})
        progress = self._progress.get(user_id, {})
        result = []
        for book_id, r in reminders.items():
            pct = int(progress.get(book_id, 0.0) * 100)
            result.append({
                "book_id": book_id,
                "title": r.title,
                "enabled": r.enabled,
                "progress_pct": pct,
                "next_milestone": next((p for p in NOTIFY_AT if p > pct), None),
                "last_message": r.last_message,
                "last_fired_at": r.last_fired_at,
                "created_at": r.created_at,
            })
        return result

    def get_progress(self, user_id: str) -> List[Dict]:
        return [
            {"book_id": bid, "progress": prog, "percent": int(prog * 100)}
            for bid, prog in self._progress.get(user_id, {}).items()
        ]

    def _loop(self):
        while True:
            try:
                self._check_all()
            except Exception as e:
                log.warning(f"Reminder loop error: {e}")
            time.sleep(30)

    def _check_all(self):
        for user_id, reminders in list(self._reminders.items()):
            progress = self._progress.get(user_id, {})
            for book_id, reminder in list(reminders.items()):
                if not reminder.enabled:
                    continue

                pct = int(progress.get(book_id, 0.0) * 100)
                last = reminder.last_notified
                triggered = [p for p in NOTIFY_AT if last < p <= pct]

                if not triggered:
                    continue

                reminder.last_notified = pct

                if pct >= 100:
                    msg = f"You finished '{reminder.title}'! Amazing work!"
                elif pct >= 75:
                    msg = f"'{reminder.title}' — {pct}% done. Almost there!"
                elif pct >= 50:
                    msg = f"'{reminder.title}' — halfway through!"
                else:
                    msg = f"'{reminder.title}' — {pct}% complete. Keep reading!"

                reminder.last_message = msg
                reminder.last_fired_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fire(msg)

    @staticmethod
    def _fire(msg: str):
        log.info(f"[REMINDER] {msg}")
        try:
            from plyer import notification
            notification.notify(title="DigiKitab", message=msg, timeout=6)
        except Exception:
            pass
