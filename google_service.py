import logging
from datetime import datetime, timedelta
import pytz
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import Config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks"
]

class GoogleService:
    def __init__(self):
        self.calendar_service = None
        self.tasks_service = None
        self._init_services()

    def _init_services(self):
        sa_info = Config.get_service_account_info()
        if not sa_info:
            logger.warning("No Google Service Account credentials found.")
            return

        try:
            creds = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=SCOPES
            )
            if Config.GOOGLE_DELEGATED_EMAIL:
                logger.info(f"Using domain-wide delegation for {Config.GOOGLE_DELEGATED_EMAIL}")
                creds = creds.with_subject(Config.GOOGLE_DELEGATED_EMAIL)

            self.calendar_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            logger.info("Google Calendar service initialized successfully.")

            # Initialize Tasks Service
            # If OAuth2 user credentials provided (for personal @gmail.com accounts), use them for tasks
            if Config.GOOGLE_REFRESH_TOKEN and Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET:
                logger.info("Initializing Google Tasks service with OAuth2 refresh token...")
                tasks_creds = OAuthCredentials(
                    None,
                    refresh_token=Config.GOOGLE_REFRESH_TOKEN,
                    client_id=Config.GOOGLE_CLIENT_ID,
                    client_secret=Config.GOOGLE_CLIENT_SECRET,
                    token_uri="https://oauth2.googleapis.com/token",
                )
                self.tasks_service = build("tasks", "v1", credentials=tasks_creds, cache_discovery=False)
            else:
                # Default to service account credentials
                self.tasks_service = build("tasks", "v1", credentials=creds, cache_discovery=False)
            logger.info("Google Tasks service initialized.")
        except Exception as e:
            logger.error(f"Failed to initialize Google services: {e}", exc_info=True)

    def check_availability(self, start_iso: str, end_iso: str, calendar_ids: list[str]) -> tuple[bool, list[dict]]:
        """
        Uses freebusy.query to check if the proposed [start_iso, end_iso] window
        is available across all specified calendar IDs.
        Returns (is_free, list_of_busy_intervals)
        """
        if not self.calendar_service:
            raise RuntimeError("Google Calendar service is not initialized.")

        body = {
            "timeMin": start_iso,
            "timeMax": end_iso,
            "timeZone": Config.USER_TIMEZONE_STR,
            "items": [{"id": cid} for cid in calendar_ids]
        }

        try:
            response = self.calendar_service.freebusy().query(body=body).execute()
            calendars = response.get("calendars", {})
            conflicts = []

            for cid, data in calendars.items():
                busy_list = data.get("busy", [])
                for b in busy_list:
                    conflicts.append({
                        "calendar_id": cid,
                        "start": b["start"],
                        "end": b["end"]
                    })

            is_free = len(conflicts) == 0
            return is_free, conflicts
        except Exception as e:
            logger.error(f"Error querying freebusy: {e}", exc_info=True)
            raise

    def find_next_free_slot(
        self,
        duration_minutes: int,
        calendar_ids: list[str],
        start_search_dt: datetime = None
    ) -> tuple[str, str]:
        """
        Searches forward across the specified calendars to find the next available gap
        of duration_minutes during active hours (08:00 - 23:00 local time).
        Returns (start_iso, end_iso).
        """
        tz = Config.USER_TIMEZONE
        if start_search_dt is None:
            now = datetime.now(tz)
        else:
            now = start_search_dt.astimezone(tz)

        # Round up to next 15-minute mark
        minutes_to_add = (15 - (now.minute % 15)) % 15
        if minutes_to_add == 0:
            minutes_to_add = 15
        candidate_start = (now + timedelta(minutes=minutes_to_add)).replace(second=0, microsecond=0)

        # Query freebusy for next 4 days
        search_window_end = candidate_start + timedelta(days=4)
        body = {
            "timeMin": candidate_start.isoformat(),
            "timeMax": search_window_end.isoformat(),
            "timeZone": Config.USER_TIMEZONE_STR,
            "items": [{"id": cid} for cid in calendar_ids]
        }

        response = self.calendar_service.freebusy().query(body=body).execute()
        busy_spans = []
        for cid, data in response.get("calendars", {}).items():
            for b in data.get("busy", []):
                b_start = datetime.fromisoformat(b["start"]).astimezone(tz)
                b_end = datetime.fromisoformat(b["end"]).astimezone(tz)
                busy_spans.append((b_start, b_end))

        # Sort busy intervals
        busy_spans.sort(key=lambda x: x[0])

        # Step forward in 15-minute increments
        current_cursor = candidate_start
        target_delta = timedelta(minutes=duration_minutes)

        while current_cursor + target_delta <= search_window_end:
            # Respect waking hours 08:00 - 23:00
            hour = current_cursor.hour
            if hour < 8:
                current_cursor = current_cursor.replace(hour=8, minute=0)
                continue
            if hour >= 23 or (current_cursor + target_delta).hour > 23:
                # Jump to tomorrow 08:00
                current_cursor = (current_cursor + timedelta(days=1)).replace(hour=8, minute=0)
                continue

            candidate_end = current_cursor + target_delta

            # Check overlap with any busy span
            overlap = False
            for b_start, b_end in busy_spans:
                if not (candidate_end <= b_start or current_cursor >= b_end):
                    overlap = True
                    # Jump cursor to end of this busy span
                    if b_end > current_cursor:
                        current_cursor = b_end
                    break

            if not overlap:
                # Found a free slot!
                return current_cursor.isoformat(), candidate_end.isoformat()

            # Increment cursor if not already moved
            current_cursor += timedelta(minutes=15)

        # Fallback if no slot found in 4 days: 1 day later same time
        fallback_start = candidate_start + timedelta(days=1)
        fallback_end = fallback_start + target_delta
        return fallback_start.isoformat(), fallback_end.isoformat()

    def create_calendar_event(
        self,
        calendar_id: str,
        title: str,
        start_iso: str,
        end_iso: str,
        metadata: dict = None
    ) -> dict:
        """
        Creates an event prefixed with ⏳ in the target calendar.
        Attaches bot metadata in extendedProperties.
        """
        if not self.calendar_service:
            raise RuntimeError("Calendar service not initialized.")

        # Ensure title has ⏳ prefix
        clean_title = title.strip()
        if not clean_title.startswith("⏳"):
            clean_title = f"⏳ {clean_title}"

        body = {
            "summary": clean_title,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "extendedProperties": {
                "private": {
                    "bot_managed": "true",
                    **(metadata or {})
                }
            }
        }

        event = self.calendar_service.events().insert(
            calendarId=calendar_id,
            body=body
        ).execute()

        logger.info(f"Created event '{clean_title}' in calendar {calendar_id} (ID: {event.get('id')})")
        return event

    def create_google_task(self, title: str, due_iso: str, notes: str = "") -> str | None:
        """
        Creates a linked Google Task due at due_iso.
        Returns task_id if successful, or None.
        """
        if not self.tasks_service:
            logger.warning("Tasks service not initialized; skipping task creation.")
            return None

        # Format due time for Google Tasks API: RFC 3339 timestamp (e.g. 2026-10-20T20:30:00Z)
        try:
            dt = datetime.fromisoformat(due_iso)
            # Ensure UTC for Tasks API
            utc_dt = dt.astimezone(pytz.UTC)
            formatted_due = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

            clean_title = title.replace("⏳", "").replace("✅", "").strip()
            body = {
                "title": clean_title,
                "due": formatted_due,
                "notes": notes
            }

            task = self.tasks_service.tasks().insert(
                tasklist=Config.GOOGLE_TASKS_LIST_ID,
                body=body
            ).execute()

            task_id = task.get("id")
            logger.info(f"Created linked Google Task '{clean_title}' (ID: {task_id})")
            return task_id
        except HttpError as e:
            logger.error(f"Google Tasks API error: {e}. Note: personal @gmail.com accounts require OAuth2 refresh token for Tasks.")
            return None
        except Exception as e:
            logger.error(f"Failed to create Google Task: {e}", exc_info=True)
            return None

    def move_calendar_event(self, source_cal_id: str, dest_cal_id: str, event_id: str) -> dict:
        """
        Moves an event from source calendar to destination calendar (e.g. Completed Tasks).
        """
        if not self.calendar_service:
            raise RuntimeError("Calendar service not initialized.")

        moved_event = self.calendar_service.events().move(
            calendarId=source_cal_id,
            eventId=event_id,
            destination=dest_cal_id
        ).execute()

        logger.info(f"Moved event {event_id} from {source_cal_id} to {dest_cal_id}")
        return moved_event

    def patch_calendar_event_title(self, calendar_id: str, event_id: str, new_title: str) -> dict:
        """
        Patches event title (e.g. replacing ⏳ with ✅).
        """
        if not self.calendar_service:
            raise RuntimeError("Calendar service not initialized.")

        patched = self.calendar_service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body={"summary": new_title}
        ).execute()

        logger.info(f"Patched event {event_id} title to: {new_title}")
        return patched

    def patch_calendar_event_times(self, calendar_id: str, event_id: str, start_iso: str, end_iso: str) -> dict:
        """
        Patches event start and end time.
        """
        if not self.calendar_service:
            raise RuntimeError("Calendar service not initialized.")

        body = {
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso}
        }
        patched = self.calendar_service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=body
        ).execute()

        logger.info(f"Patched event {event_id} timing: {start_iso} -> {end_iso}")
        return patched

    def complete_google_task(self, task_id: str) -> bool:
        """
        Marks linked Google Task as completed.
        """
        if not self.tasks_service or not task_id:
            return False

        try:
            self.tasks_service.tasks().patch(
                tasklist=Config.GOOGLE_TASKS_LIST_ID,
                task=task_id,
                body={"status": "completed"}
            ).execute()
            logger.info(f"Marked task {task_id} as completed.")
            return True
        except Exception as e:
            logger.error(f"Failed to complete task {task_id}: {e}")
            return False

    def update_google_task_due(self, task_id: str, new_due_iso: str) -> bool:
        """
        Updates due date of linked Google Task.
        """
        if not self.tasks_service or not task_id:
            return False

        try:
            dt = datetime.fromisoformat(new_due_iso)
            utc_dt = dt.astimezone(pytz.UTC)
            formatted_due = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

            self.tasks_service.tasks().patch(
                tasklist=Config.GOOGLE_TASKS_LIST_ID,
                task=task_id,
                body={"due": formatted_due}
            ).execute()
            logger.info(f"Updated task {task_id} due to {formatted_due}")
            return True
        except Exception as e:
            logger.error(f"Failed to update task {task_id} due date: {e}")
            return False

# Global instance
google_service_instance = GoogleService()
