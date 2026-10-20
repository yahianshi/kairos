import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from config import Config
from database import db_instance

logger = logging.getLogger(__name__)

class AccountabilityScheduler:
    def __init__(self, bot=None):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone=Config.USER_TIMEZONE)

    def set_bot(self, bot):
        self.bot = bot

    def start(self):
        """Starts the periodic accountability loop."""
        self.scheduler.add_job(
            self.check_ended_events,
            "interval",
            seconds=60,
            id="accountability_check",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info("Accountability loop (APScheduler) started, running every 60s.")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Accountability loop stopped.")

    async def check_ended_events(self):
        """
        Scans database for tracked events where end_iso has passed and status is 'active'.
        Sends interactive check-in message to Telegram.
        """
        if not self.bot:
            return

        now_local = datetime.now(Config.USER_TIMEZONE)
        current_iso = now_local.isoformat()

        try:
            due_events = await db_instance.get_events_due_for_review(current_iso)
            for event in due_events:
                event_id = event["event_id"]
                chat_id = event["chat_id"]
                title = event["title"]

                logger.info(f"Event ended: '{title}' (ID: {event_id}). Prompting user...")

                keyboard = [
                    [
                        InlineKeyboardButton("✅ Done", callback_data=f"status_done:{event_id}"),
                        InlineKeyboardButton("⏳ Working on it", callback_data=f"status_extend:{event_id}")
                    ],
                    [
                        InlineKeyboardButton("🔄 Reschedule", callback_data=f"status_resched:{event_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                clean_title = title.replace("⏳", "").strip()
                text = (
                    f"🔔 *Your block just ended!*\n\n"
                    f"📌 *Session:* `{clean_title}`\n"
                    f"📁 *Calendar:* `{event['calendar_name']}`\n\n"
                    f"What's the status?"
                )

                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    # Mark as pending_review so we don't spam the user
                    await db_instance.update_event_status(event_id, "pending_review")
                except Exception as send_err:
                    logger.error(f"Failed to send block completion notification to chat {chat_id}: {send_err}")

        except Exception as e:
            logger.error(f"Error in accountability check job: {e}", exc_info=True)

scheduler_instance = AccountabilityScheduler()
