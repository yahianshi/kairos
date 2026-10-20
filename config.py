import os
import json
import base64
import logging
import pytz
from dotenv import load_dotenv

# Load local .env if available
load_dotenv()

logger = logging.getLogger(__name__)

# Expected calendar routing names
EXPECTED_CALENDARS = [
    "Personal",
    "Assistant work",
    "Quizzez / FInals",
    "Self Studying",
    "Uni Classes",
    "University Studying",
    "Completed Tasks"
]

class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    
    # Allowed Telegram User IDs
    raw_user_ids = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    TELEGRAM_ALLOWED_USER_IDS: list[int] = []
    if raw_user_ids:
        for uid in raw_user_ids.split(","):
            uid = uid.strip()
            if uid.isdigit():
                TELEGRAM_ALLOWED_USER_IDS.append(int(uid))

    # OpenRouter
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-exp:free").strip()

    # Timezone
    USER_TIMEZONE_STR: str = os.getenv("USER_TIMEZONE", "UTC").strip()
    try:
        USER_TIMEZONE = pytz.timezone(USER_TIMEZONE_STR)
    except pytz.UnknownTimeZoneError:
        logger.warning(f"Unknown timezone '{USER_TIMEZONE_STR}'. Falling back to UTC.")
        USER_TIMEZONE = pytz.UTC
        USER_TIMEZONE_STR = "UTC"

    # Google Auth
    GOOGLE_SERVICE_ACCOUNT_PATH: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "service_account.json").strip()
    GOOGLE_SERVICE_ACCOUNT_JSON_RAW: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    GOOGLE_DELEGATED_EMAIL: str | None = os.getenv("GOOGLE_DELEGATED_EMAIL", "").strip() or None

    # Optional OAuth2 credentials for Tasks fallback (Personal @gmail.com)
    GOOGLE_CLIENT_ID: str | None = os.getenv("GOOGLE_CLIENT_ID", "").strip() or None
    GOOGLE_CLIENT_SECRET: str | None = os.getenv("GOOGLE_CLIENT_SECRET", "").strip() or None
    GOOGLE_REFRESH_TOKEN: str | None = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip() or None

    # Google Calendar Routing Dictionary
    raw_calendars = os.getenv("GOOGLE_CALENDAR_IDS", "{}").strip()
    try:
        GOOGLE_CALENDAR_IDS: dict[str, str] = json.loads(raw_calendars) if raw_calendars else {}
    except Exception as e:
        logger.error(f"Failed to parse GOOGLE_CALENDAR_IDS as JSON: {e}")
        GOOGLE_CALENDAR_IDS = {}

    GOOGLE_TASKS_LIST_ID: str = os.getenv("GOOGLE_TASKS_LIST_ID", "@default").strip()

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot_scheduler.db").strip()

    @classmethod
    def get_calendar_id(cls, name: str) -> str | None:
        """Retrieves calendar ID with case-insensitive and normalized fallback."""
        if not name:
            return None
        if name in cls.GOOGLE_CALENDAR_IDS:
            return cls.GOOGLE_CALENDAR_IDS[name]
        norm = name.strip().lower()
        for k, v in cls.GOOGLE_CALENDAR_IDS.items():
            if k.strip().lower() == norm:
                return v
        return None

    @classmethod
    def get_service_account_info(cls) -> dict | None:
        """
        Retrieves service account dict from:
        1. Base64 or raw JSON env var GOOGLE_SERVICE_ACCOUNT_JSON
        2. Local file path GOOGLE_SERVICE_ACCOUNT_PATH
        """
        if cls.GOOGLE_SERVICE_ACCOUNT_JSON_RAW:
            raw = cls.GOOGLE_SERVICE_ACCOUNT_JSON_RAW
            # Try raw JSON first
            try:
                return json.loads(raw)
            except Exception:
                pass
            # Try Base64 decode
            try:
                decoded = base64.b64decode(raw).decode("utf-8")
                return json.loads(decoded)
            except Exception as e:
                logger.error(f"Failed to decode GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
                return None

        if os.path.exists(cls.GOOGLE_SERVICE_ACCOUNT_PATH):
            try:
                with open(cls.GOOGLE_SERVICE_ACCOUNT_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to read service account JSON at {cls.GOOGLE_SERVICE_ACCOUNT_PATH}: {e}")
                return None

        return None

    @classmethod
    def validate(cls) -> list[str]:
        """Validates critical config values and returns list of missing items or warnings."""
        errors = []
        if not cls.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is missing.")
        if not cls.TELEGRAM_ALLOWED_USER_IDS:
            errors.append("TELEGRAM_ALLOWED_USER_IDS is empty or invalid.")
        if not cls.OPENROUTER_API_KEY:
            errors.append("OPENROUTER_API_KEY is missing.")
        
        sa_info = cls.get_service_account_info()
        if not sa_info:
            errors.append("Google Service Account credentials not found (provide GOOGLE_SERVICE_ACCOUNT_JSON or valid GOOGLE_SERVICE_ACCOUNT_PATH).")

        if not cls.GOOGLE_CALENDAR_IDS:
            errors.append("GOOGLE_CALENDAR_IDS is empty. Provide a valid JSON map of calendar names to IDs.")
        else:
            missing_cals = [name for name in EXPECTED_CALENDARS if name not in cls.GOOGLE_CALENDAR_IDS]
            if missing_cals:
                errors.append(f"GOOGLE_CALENDAR_IDS is missing expected calendars: {missing_cals}")

        return errors
