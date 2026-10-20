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

    # OpenRouter Models (Primary -> Secondary -> Fallback, max 3 models)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    default_models_str = "nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free,openrouter/free"
    raw_models = os.getenv("OPENROUTER_MODELS", "").strip() or os.getenv("OPENROUTER_MODEL", "").strip() or default_models_str
    OPENROUTER_MODELS: list[str] = [m.strip() for m in raw_models.split(",") if m.strip()][:3]
    OPENROUTER_MODEL: str = OPENROUTER_MODELS[0] if OPENROUTER_MODELS else "openrouter/free"

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
        1. GOOGLE_SERVICE_ACCOUNT_JSON (or common aliases: GOOGLE_SERVICE_ACCOUNT, SERVICE_ACCOUNT_JSON)
        2. Local file path GOOGLE_SERVICE_ACCOUNT_PATH
        """
        # Dynamic check in os.environ for cloud runtime
        raw = (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
            or os.getenv("GOOGLE_SERVICE_ACCOUNT", "").strip()
            or os.getenv("SERVICE_ACCOUNT_JSON", "").strip()
            or cls.GOOGLE_SERVICE_ACCOUNT_JSON_RAW
        )

        if raw:
            # Strip wrapping single or double quotes
            if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
                raw = raw[1:-1].strip()

            # 1. Try direct JSON parse
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and ("type" in data or "client_email" in data):
                    if "private_key" in data and isinstance(data["private_key"], str):
                        data["private_key"] = data["private_key"].replace("\\n", "\n")
                    return data
            except Exception as e_json:
                logger.debug(f"Direct JSON parse of service account failed: {e_json}")

            # 2. Try unescaping double-escaped strings
            try:
                unescaped = raw.encode().decode("unicode_escape")
                data = json.loads(unescaped)
                if isinstance(data, dict) and ("type" in data or "client_email" in data):
                    if "private_key" in data and isinstance(data["private_key"], str):
                        data["private_key"] = data["private_key"].replace("\\n", "\n")
                    return data
            except Exception:
                pass

            # 3. Try Base64 decode
            try:
                padded = raw + "=" * (-len(raw) % 4)
                decoded = base64.b64decode(padded).decode("utf-8")
                data = json.loads(decoded)
                if isinstance(data, dict) and ("type" in data or "client_email" in data):
                    if "private_key" in data and isinstance(data["private_key"], str):
                        data["private_key"] = data["private_key"].replace("\\n", "\n")
                    return data
            except Exception as e_b64:
                logger.debug(f"Base64 parse of service account failed: {e_b64}")

            logger.error("GOOGLE_SERVICE_ACCOUNT_JSON is set but could not be parsed as valid Service Account JSON or Base64.")

        # Check file path fallback (for local development)
        paths_to_check = [
            os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "").strip(),
            cls.GOOGLE_SERVICE_ACCOUNT_PATH,
            "service_account.json",
            "secrets/calendar-bot-512217-7d6917888104-cal-api.json"
        ]

        for p in paths_to_check:
            if p and os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and ("type" in data or "client_email" in data):
                            if "private_key" in data and isinstance(data["private_key"], str):
                                data["private_key"] = data["private_key"].replace("\\n", "\n")
                            return data
                except Exception as e:
                    logger.error(f"Failed to read service account JSON at {p}: {e}")

        logger.warning(
            "Service account credentials not found. In cloud environments (Render/Railway), "
            "ensure 'GOOGLE_SERVICE_ACCOUNT_JSON' environment variable contains the JSON text."
        )
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
            errors.append("Google Service Account credentials not found (set GOOGLE_SERVICE_ACCOUNT_JSON in Render).")

        if not cls.GOOGLE_CALENDAR_IDS:
            errors.append("GOOGLE_CALENDAR_IDS is empty. Provide a valid JSON map of calendar names to IDs.")
        else:
            missing_cals = [name for name in EXPECTED_CALENDARS if not cls.get_calendar_id(name)]
            if missing_cals:
                errors.append(f"GOOGLE_CALENDAR_IDS is missing expected calendars: {missing_cals}")

        return errors
