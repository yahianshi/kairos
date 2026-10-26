import asyncio
import logging
from datetime import datetime, timedelta
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
        """Starts the periodic accountability loop, pre-event reminder checks, and Google Tasks sync."""
        self.scheduler.add_job(
            self.check_ended_events,
            "interval",
            seconds=60,
            id="accountability_check",
            replace_existing=True
        )
        self.scheduler.add_job(
            self.check_upcoming_reminders,
            "interval",
            seconds=60,
            id="upcoming_reminders_check",
            replace_existing=True
        )
        self.scheduler.add_job(
            self.sync_completed_tasks,
            "interval",
            seconds=60,
            id="sync_completed_tasks",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info("Accountability, Reminder & Tasks Sync loop (APScheduler) started, running every 60s.")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Accountability loop stopped.")

    async def check_upcoming_reminders(self):
        """
        Scans database for tracked events starting in ~10 minutes
        where reminder_sent is 0. Sends an interactive reminder on Telegram.
        """
        if not self.bot:
            return

        now_local = datetime.now(Config.USER_TIMEZONE)
        # Look for events starting between now and now + 12 minutes
        window_start = now_local.isoformat()
        window_end = (now_local + timedelta(minutes=12)).isoformat()

        try:
            upcoming = await db_instance.get_events_due_for_reminder(window_start, window_end)
            for event in upcoming:
                event_id = event["event_id"]
                chat_id = event["chat_id"]
                title = event["title"]
                clean_title = title.replace("⏳", "").replace("✅", "").strip()

                try:
                    event_start = datetime.fromisoformat(event["start_iso"]).astimezone(Config.USER_TIMEZONE)
                    mins_away = max(1, int((event_start - now_local).total_seconds() / 60))
                    start_fmt = event_start.strftime("%a, %b %d at %H:%M")
                except Exception:
                    mins_away = 10
                    start_fmt = event["start_iso"]

                text = (
                    f"⏰ *Upcoming Session Reminder!*\n\n"
                    f"📌 *{clean_title}*\n"
                    f"📁 `{event['calendar_name']}`\n"
                    f"🕒 Starts at *{start_fmt}* (in ~{mins_away} mins)\n\n"
                    f"_Need to change anything before it starts?_"
                )

                keyboard = [
                    [
                        InlineKeyboardButton("🔄 Reschedule", callback_data=f"ev_resched_menu:{event_id}"),
                        InlineKeyboardButton("🗑️ Delete", callback_data=f"ev_del_ask:{event_id}")
                    ]
                ]

                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode=ParseMode.MARKDOWN
                    )
                    await db_instance.mark_reminder_sent(event_id)
                    logger.info(f"Sent pre-event reminder for '{clean_title}' to chat {chat_id}")
                except Exception as send_err:
                    logger.error(f"Failed to send reminder for event {event_id}: {send_err}")

        except Exception as e:
            logger.error(f"Error in upcoming reminders check: {e}", exc_info=True)

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

    async def sync_completed_tasks(self):
        """
        Polls Google Tasks for tasks marked completed externally (e.g. checked off in Google Calendar
        or Google Tasks mobile/web app). For any matching active or pending_review tracked events,
        automatically moves them to 'Completed Tasks' calendar, replaces ⏳ with ✅, updates status
        to 'completed' in SQLite, and notifies the user on Telegram.
        """
        from google_service import google_service_instance
        try:
            uncompleted_events = await db_instance.get_uncompleted_events_with_tasks()
            if not uncompleted_events:
                return

            completed_task_ids = await asyncio.to_thread(google_service_instance.get_completed_tasks_set)
            if not completed_task_ids:
                return

            for event in uncompleted_events:
                task_id = event.get("task_id")
                if task_id and task_id in completed_task_ids:
                    event_id = event["event_id"]
                    chat_id = event["chat_id"]
                    logger.info(f"Detected Google Task completion for event {event_id} (task {task_id}). Auto-completing...")

                    from bot_handlers import complete_tracked_event
                    res = await complete_tracked_event(event_id)
                    if res and self.bot:
                        clean_title = res["title"].replace("✅ ", "").replace("✅", "").strip()
                        try:
                            await self.bot.send_message(
                                chat_id=chat_id,
                                text=(
                                    f"✅ *Task Synced from Google Tasks / Calendar!*\n\n"
                                    f"📌 *{clean_title}* was marked done in Google Tasks.\n"
                                    f"📁 Automatically moved to *Completed Tasks* calendar."
                                ),
                                parse_mode=ParseMode.MARKDOWN
                            )
                        except Exception as send_err:
                            logger.error(f"Failed to notify chat {chat_id} of synced task completion: {send_err}")
        except Exception as e:
            logger.error(f"Error in sync_completed_tasks job: {e}", exc_info=True)

scheduler_instance = AccountabilityScheduler()
