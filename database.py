import json
import logging
from datetime import datetime, timezone
import aiosqlite
from config import Config

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DATABASE_PATH

    async def init_db(self):
        """Initializes tables in SQLite database if they do not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tracked_events (
                    event_id TEXT PRIMARY KEY,
                    calendar_id TEXT NOT NULL,
                    calendar_name TEXT NOT NULL,
                    task_id TEXT,
                    title TEXT NOT NULL,
                    start_iso TEXT NOT NULL,
                    end_iso TEXT NOT NULL,
                    status TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_dialogs (
                    user_id INTEGER PRIMARY KEY,
                    action TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    async def add_tracked_event(
        self,
        event_id: str,
        calendar_id: str,
        calendar_name: str,
        task_id: str | None,
        title: str,
        start_iso: str,
        end_iso: str,
        chat_id: int,
        status: str = "active"
    ):
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO tracked_events 
                (event_id, calendar_id, calendar_name, task_id, title, start_iso, end_iso, status, chat_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_id, calendar_id, calendar_name, task_id, title, start_iso, end_iso, status, chat_id, now_utc, now_utc))
            await db.commit()

    async def get_tracked_event(self, event_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM tracked_events WHERE event_id = ?", (event_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_events_due_for_review(self, current_iso: str) -> list[dict]:
        """
        Returns all events whose end_iso has passed and whose status is still 'active'.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM tracked_events 
                WHERE status = 'active' AND end_iso <= ?
                ORDER BY end_iso ASC
            """, (current_iso,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def update_event_status(self, event_id: str, status: str):
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tracked_events 
                SET status = ?, updated_at = ?
                WHERE event_id = ?
            """, (status, now_utc, event_id))
            await db.commit()

    async def update_event_times(self, event_id: str, start_iso: str, end_iso: str, status: str = "active"):
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tracked_events 
                SET start_iso = ?, end_iso = ?, status = ?, updated_at = ?
                WHERE event_id = ?
            """, (start_iso, end_iso, status, now_utc, event_id))
            await db.commit()

    async def update_event_calendar(
        self,
        event_id: str,
        new_calendar_id: str,
        new_calendar_name: str,
        new_title: str,
        status: str = "completed"
    ):
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE tracked_events 
                SET calendar_id = ?, calendar_name = ?, title = ?, status = ?, updated_at = ?
                WHERE event_id = ?
            """, (new_calendar_id, new_calendar_name, new_title, status, now_utc, event_id))
            await db.commit()

    async def set_pending_dialog(self, user_id: int, action: str, payload: dict):
        now_utc = datetime.now(timezone.utc).isoformat()
        payload_str = json.dumps(payload)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO pending_dialogs (user_id, action, payload, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, action, payload_str, now_utc))
            await db.commit()

    async def get_pending_dialog(self, user_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pending_dialogs WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    data = dict(row)
                    try:
                        data["payload"] = json.loads(data["payload"])
                    except Exception:
                        pass
                    return data
                return None

    async def clear_pending_dialog(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_dialogs WHERE user_id = ?", (user_id,))
            await db.commit()

    async def get_upcoming_events(self, chat_id: int, limit: int = 5) -> list[dict]:
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM tracked_events 
                WHERE chat_id = ? AND end_iso >= ? AND status NOT IN ('completed', 'deleted')
                ORDER BY start_iso ASC
                LIMIT ?
            """, (chat_id, now_utc, limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def search_events_by_title(self, chat_id: int, query: str, limit: int = 10) -> list[dict]:
        """Case-insensitive partial title search across active events."""
        now_utc = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM tracked_events
                WHERE chat_id = ? AND end_iso >= ?
                  AND status NOT IN ('completed', 'deleted')
                  AND LOWER(title) LIKE ?
                ORDER BY start_iso ASC
                LIMIT ?
            """, (chat_id, now_utc, f"%{query.lower()}%", limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

# Global DB instance
db_instance = Database()
