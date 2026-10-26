import logging
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import Config
from database import db_instance
from google_service import google_service_instance
from llm_service import llm_service_instance

logger = logging.getLogger(__name__)

ACTIVE_CALENDARS = [
    "Personal",
    "Assistant work",
    "Quizzez / FInals",
    "Self Studying",
    "Uni Classes",
    "University Studying",
]

def is_authorized(user_id: int) -> bool:
    """Checks if user is in TELEGRAM_ALLOWED_USER_IDS."""
    if not Config.TELEGRAM_ALLOWED_USER_IDS:
        return True  # If not configured, allow (warned in validate)
    return user_id in Config.TELEGRAM_ALLOWED_USER_IDS

def format_human_datetime(iso_str: str) -> str:
    """Formats ISO datetime into readable string like 'Tue, Oct 21, 10:00'."""
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(Config.USER_TIMEZONE)
        return dt.strftime("%a, %b %d at %H:%M")
    except Exception:
        return iso_str

# =====================================================================
# COMMAND HANDLERS
# =====================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Unauthorized access. Your Telegram ID is not authorized.")
        return

    tz_name = Config.USER_TIMEZONE_STR
    configured_cals = len(Config.GOOGLE_CALENDAR_IDS)

    welcome_text = (
        f"👋 *Hello {user.first_name}!*\n\n"
        f"I am *Kairos*, your AI Executive Auto-Scheduler for Google Calendar & Tasks.\n\n"
        f"⚡ *Features:*\n"
        f"• Natural language scheduling powered by LLM tool calling.\n"
        f"• Automated routing into your designated calendars.\n"
        f"• Conflict detection across all your calendars simultaneously.\n"
        f"• Companion Google Tasks due 5 mins post-event.\n"
        f"• Automated accountability checks when your block finishes!\n\n"
        f"⚙️ *Status:*\n"
        f"• Timezone: `{tz_name}`\n"
        f"• Calendars configured: `{configured_cals}`\n\n"
        f"💬 *Try saying:*\n"
        f"_\"Schedule 2 hours of university studying for algorithms tomorrow at 2 PM\"_\n"
        f"_\"Plan a 45 min workout this evening at 6\"_\n\n"
        f"Use /status to see upcoming blocks or /help for more."
    )
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    help_text = (
        "⏳ *Kairos — AI Auto-Scheduler Help:*\n\n"
        "1. *Scheduling:* Just type what you want to do naturally:\n"
        "   • _\"Study linear algebra for 2 hours tomorrow at 10 AM\"_\n"
        "   • _\"Assistant grading meeting on Thursday from 3pm to 4pm\"_\n"
        "   • _\"Finals prep: database systems for 90 mins tonight at 8\"_\n\n"
        "2. *Accountability Loop:*\n"
        "   When your scheduled session finishes, Kairos will ping you with 3 options:\n"
        "   • *Done*: Moves the event to `Completed Tasks`, changes ⏳ to ✅, and checks off the Google Task.\n"
        "   • *Working on it*: Extends the calendar block and shifts the task due time.\n"
        "   • *Reschedule*: Finds the next open slot on your schedule.\n\n"
        "3. *Delete Events:*\n"
        "   Say _\"delete my algorithms study session\"_ or use /delete to pick from a list.\n\n"
        "4. *Recurring Events:*\n"
        "   Say _\"schedule daily morning run at 7 AM for 30 minutes\"_ or\n"
        "   _\"every weekday 1 hour of self study at 9 PM\"_.\n\n"
        "5. *Reschedule:*\n"
        "   Say _\"reschedule my algorithms session to tomorrow at 3 PM\"_\n"
        "   (bot moves the existing event — no duplicate created).\n\n"
        "6. *Commands:*\n"
        "   /status - View upcoming tracked sessions\n"
        "   /delete - Delete a scheduled event (with confirmation)\n"
        "   /cancel - Cancel any active pending action\n"
        "   /help - Show this message"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    upcoming = await db_instance.get_upcoming_events(chat_id, limit=6)

    # Check live service state
    if not google_service_instance.calendar_service:
        google_service_instance._init_services()

    cal_ok = "✅ Connected" if google_service_instance.calendar_service else "❌ Not Initialized (Missing GOOGLE_SERVICE_ACCOUNT_JSON)"
    tasks_ok = "✅ Connected" if google_service_instance.tasks_service else "⚠️ Not Configured"

    diag = (
        "⚙️ *Kairos System Diagnostics:*\n"
        f"• *Google Calendar:* {cal_ok}\n"
        f"• *Google Tasks:* {tasks_ok}\n"
        f"• *Timezone:* `{Config.USER_TIMEZONE_STR}`\n"
        f"• *Configured Calendars:* `{len(Config.GOOGLE_CALENDAR_IDS)}`\n\n"
    )

    if not upcoming:
        await update.message.reply_text(
            f"{diag}📅 *Upcoming Blocks:* None currently scheduled.\n\n"
            "Try: _\"Schedule 1 hour of self studying tomorrow at 10 AM\"_",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    lines = [f"{diag}📅 *Your Upcoming Scheduled Blocks:*\n"]
    for ev in upcoming:
        start_fmt = format_human_datetime(ev["start_iso"])
        end_fmt = format_human_datetime(ev["end_iso"]).split(" at ")[-1]
        lines.append(
            f"• *{ev['title']}*\n"
            f"  📁 `{ev['calendar_name']}` | 🕒 {start_fmt} – {end_fmt}"
        )

    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    pending = await db_instance.get_pending_dialog(user.id)
    if pending:
        await db_instance.clear_pending_dialog(user.id)
        await update.message.reply_text("✅ Current pending action cancelled.")
    else:
        await update.message.reply_text("No active action to cancel.")


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists upcoming tracked events with a Delete button next to each one."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    upcoming = await db_instance.get_upcoming_events(chat_id, limit=10)

    if not upcoming:
        await update.message.reply_text(
            "📅 No active scheduled events found to delete."
        )
        return

    lines = ["🗑️ *Select an event to delete:*\n"]
    keyboard = []
    for ev in upcoming:
        start_fmt = format_human_datetime(ev["start_iso"])
        short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
        lines.append(f"• `{short_title}` — {start_fmt}")
        keyboard.append([
            InlineKeyboardButton(
                f"🗑️ {short_title[:30]}",
                callback_data=f"delete_ask:{ev['event_id']}"
            )
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# =====================================================================
# CORE SCHEDULING HELPER
# =====================================================================

async def schedule_and_reply(
    chat_id: int,
    user_id: int,
    title: str,
    start_iso: str,
    end_iso: str,
    target_calendar_name: str,
    context: ContextTypes.DEFAULT_TYPE,
    recurrence: str | None = None
):
    """
    Performs availability checking via freebusy across all active calendars,
    inserts the Google Calendar event with ⏳, inserts the companion Google Task,
    and updates SQLite state.
    """
    target_cal_id = Config.get_calendar_id(target_calendar_name)
    if not target_cal_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Error: Calendar '{target_calendar_name}' is not configured in GOOGLE_CALENDAR_IDS."
        )
        return

    # Collect all active calendar IDs for conflict check
    active_cal_ids = [
        cid for name, cid in Config.GOOGLE_CALENDAR_IDS.items()
        if name != "Completed Tasks" and cid
    ]

    # Availability check
    try:
        is_free, conflicts = google_service_instance.check_availability(start_iso, end_iso, active_cal_ids)
    except Exception as e:
        logger.error(f"Failed to check availability: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ Failed to connect to Google Calendar: {e}"
        )
        return

    # Handle conflict
    if not is_free:
        # Calculate duration in minutes
        dt_start = datetime.fromisoformat(start_iso)
        dt_end = datetime.fromisoformat(end_iso)
        duration_mins = max(15, int((dt_end - dt_start).total_seconds() / 60))

        # Find next free slot
        next_start_iso, next_end_iso = google_service_instance.find_next_free_slot(
            duration_minutes=duration_mins,
            calendar_ids=active_cal_ids,
            start_search_dt=dt_start
        )

        # Save proposal in pending dialogs
        proposal_payload = {
            "title": title,
            "start_iso": next_start_iso,
            "end_iso": next_end_iso,
            "target_calendar_name": target_calendar_name,
            "recurrence": recurrence
        }
        await db_instance.set_pending_dialog(user_id, "propose_slot", proposal_payload)

        start_fmt = format_human_datetime(start_iso)
        end_fmt = format_human_datetime(end_iso).split(" at ")[-1]
        next_start_fmt = format_human_datetime(next_start_iso)
        next_end_fmt = format_human_datetime(next_end_iso).split(" at ")[-1]

        keyboard = [
            [InlineKeyboardButton("📅 Book Proposed Slot", callback_data="accept_proposal")],
            [InlineKeyboardButton("❌ Dismiss", callback_data="cancel_proposal")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        conflict_msg = (
            f"⚠️ *Scheduling Conflict Detected!*\n\n"
            f"You are already busy during `{start_fmt} – {end_fmt}`.\n\n"
            f"💡 *Earliest Available Slot:*\n"
            f"🕒 *{next_start_fmt} – {next_end_fmt}*\n\n"
            f"Would you like to schedule `{title}` in this open slot instead?"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=conflict_msg,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # No conflict: Insert Event & Task
    try:
        # Action A: Insert Calendar Event
        clean_title = title.replace("⏳", "").replace("✅", "").strip()
        cal_event = google_service_instance.create_calendar_event(
            calendar_id=target_cal_id,
            title=clean_title,
            start_iso=start_iso,
            end_iso=end_iso,
            metadata={"calendar_name": target_calendar_name, "chat_id": str(chat_id)},
            recurrence=recurrence
        )
        event_id = cal_event.get("id")

        # Action B: Insert Linked Google Task (Due = end_iso + 5 minutes)
        dt_end = datetime.fromisoformat(end_iso)
        task_due = dt_end + timedelta(minutes=5)
        task_notes = f"Linked to Calendar block: {cal_event.get('summary')} in '{target_calendar_name}'"
        task_id = google_service_instance.create_google_task(
            title=clean_title,
            due_iso=task_due.isoformat(),
            notes=task_notes
        )

        # Action C: Track in SQLite DB
        await db_instance.add_tracked_event(
            event_id=event_id,
            calendar_id=target_cal_id,
            calendar_name=target_calendar_name,
            task_id=task_id,
            title=cal_event.get("summary"),
            start_iso=start_iso,
            end_iso=end_iso,
            chat_id=chat_id,
            status="active"
        )

        start_fmt = format_human_datetime(start_iso)
        end_fmt = format_human_datetime(end_iso).split(" at ")[-1]
        task_status_text = "✅ Created (Due 5m post-session)" if task_id else "⚠️ Skipped (Check Tasks credentials)"

        recurrence_label = f"\n🔁 *Repeats:* `{recurrence}`" if recurrence else ""
        success_text = (
            f"✨ *Session Successfully Scheduled!*\n\n"
            f"📌 *Event:* `{cal_event.get('summary')}`\n"
            f"📁 *Calendar:* `{target_calendar_name}`\n"
            f"🕒 *Time:* {start_fmt} – {end_fmt}"
            f"{recurrence_label}\n"
            f"📋 *Google Task:* {task_status_text}\n\n"
            f"🔔 _I will automatically ping you when this block ends!_"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=success_text,
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Failed to create event/task: {e}", exc_info=True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Failed to schedule session: {e}"
        )

# =====================================================================
# NATURAL LANGUAGE MESSAGE HANDLER
# =====================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Unauthorized access.")
        return

    # Check if user has an active pending dialog (e.g. custom extension minutes)
    pending = await db_instance.get_pending_dialog(user.id)
    if pending:
        action = pending.get("action")
        payload = pending.get("payload", {})

        if action == "await_custom_extension":
            # Expecting a number of minutes
            clean_digits = "".join([c for c in user_text if c.isdigit()])
            if clean_digits:
                mins = int(clean_digits)
                event_id = payload.get("event_id")
                await db_instance.clear_pending_dialog(user.id)
                await execute_extension(chat_id, event_id, mins, context)
                return
            else:
                await update.message.reply_text(
                    "⚠️ Please reply with a valid number of minutes (e.g. `15`, `30`, `45`) or /cancel."
                )
                return

    # Send typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Call LLM via OpenRouter
    llm_result = await llm_service_instance.parse_user_request(user_text)

    tool_name = llm_result.get("tool_name") if llm_result.get("is_tool_call") else None
    args = llm_result.get("arguments", {}) or {}

    # ── schedule_session ──────────────────────────────────────────────
    if tool_name == "schedule_session":
        title = args.get("title", "Study Session")
        start_iso = args.get("start_iso")
        end_iso = args.get("end_iso")
        target_cal = args.get("target_calendar_name")
        recurrence = args.get("recurrence")  # may be None for one-off

        if not start_iso or not end_iso:
            await update.message.reply_text(
                "🤔 I couldn't determine the exact start and end times. Could you specify the time or duration?"
            )
            return

        # If LLM is uncertain or calendar not found, ask user via inline keyboard
        if target_cal == "uncertain" or not Config.get_calendar_id(target_cal):
            pending_payload = {
                "title": title,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "recurrence": recurrence
            }
            await db_instance.set_pending_dialog(user.id, "select_calendar", pending_payload)

            keyboard = []
            row = []
            for cal_name in ACTIVE_CALENDARS:
                row.append(InlineKeyboardButton(cal_name, callback_data=f"pick_cal:{cal_name}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

            reply_markup = InlineKeyboardMarkup(keyboard)
            start_fmt = format_human_datetime(start_iso)
            end_fmt = format_human_datetime(end_iso).split(" at ")[-1]
            recurrence_hint = f"\n🔁 Recurring: `{recurrence}`" if recurrence else ""

            msg = (
                f"🤔 *Which calendar should this be scheduled in?*\n\n"
                f"📌 *Activity:* `{title}`\n"
                f"🕒 *Time:* {start_fmt} – {end_fmt}"
                f"{recurrence_hint}\n\n"
                f"Please choose below:"
            )
            await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
            return

        # Category is known: execute schedule
        await schedule_and_reply(chat_id, user.id, title, start_iso, end_iso, target_cal, context,
                                 recurrence=recurrence)

    # ── delete_session ────────────────────────────────────────────────
    elif tool_name == "delete_session":
        search_title = args.get("search_title", "").strip().lower()
        upcoming = await db_instance.get_upcoming_events(chat_id, limit=20)
        matches = [
            ev for ev in upcoming
            if search_title in ev["title"].lower()
            or search_title in ev["title"].replace("⏳ ", "").lower()
        ]

        if not matches:
            await update.message.reply_text(
                "\u274c No active scheduled event found matching "
                f"*\"{args.get('search_title')}\"*.\n\n"
                "Use /delete to see all your upcoming events.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if len(matches) == 1:
            ev = matches[0]
            short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
            start_fmt = format_human_datetime(ev["start_iso"])
            keyboard = [[
                InlineKeyboardButton("🗑️ Yes, Delete It", callback_data=f"delete_confirm:{ev['event_id']}"),
                InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
            ]]
            await update.message.reply_text(
                f"⚠️ *Confirm Deletion*\n\n"
                f"📌 *{short_title}*\n"
                f"🕒 {start_fmt}\n\n"
                f"This will permanently remove the event from Google Calendar.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Multiple matches — let user pick
            keyboard = []
            for ev in matches[:5]:
                short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
                start_fmt = format_human_datetime(ev["start_iso"])
                keyboard.append([InlineKeyboardButton(
                    f"🗑️ {short_title[:25]} — {start_fmt}",
                    callback_data=f"delete_ask:{ev['event_id']}"
                )])
            keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")])
            await update.message.reply_text(
                f"🔍 Found {len(matches)} events matching *\"{args.get('search_title')}\"*. Which one?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    # ── reschedule_session ────────────────────────────────────────────
    elif tool_name == "reschedule_session":
        search_title = args.get("search_title", "").strip().lower()
        new_start_iso = args.get("new_start_iso")
        new_end_iso = args.get("new_end_iso")

        if not new_start_iso or not new_end_iso:
            await update.message.reply_text(
                "🤔 I couldn't determine the new time. Please specify the new date and time."
            )
            return

        upcoming = await db_instance.get_upcoming_events(chat_id, limit=20)
        matches = [
            ev for ev in upcoming
            if search_title in ev["title"].lower()
            or search_title in ev["title"].replace("⏳ ", "").lower()
        ]

        if not matches:
            search_query = args.get('search_title', '')
            await update.message.reply_text(
                f"❌ No active event found matching *\"{search_query}\"*.\n\n"
                "Use /status to view your upcoming events.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        ev = matches[0]  # Take closest match
        await update.message.reply_text("🔄 Rescheduling...")
        await execute_reschedule_to(chat_id, ev["event_id"], new_start_iso, new_end_iso, context)

    # ── conversational fallback ───────────────────────────────────────
    else:
        reply = llm_result.get("reply_text") or "I received your message."
        await update.message.reply_text(reply)

# =====================================================================
# EXTENSION & RESCHEDULE EXECUTORS
# =====================================================================

async def execute_extension(chat_id: int, event_id: str, minutes: int, context: ContextTypes.DEFAULT_TYPE):
    """Extends calendar event and linked task by X minutes."""
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in local database.")
        return

    old_end = datetime.fromisoformat(event["end_iso"])
    new_end = old_end + timedelta(minutes=minutes)
    new_end_iso = new_end.isoformat()

    try:
        # Patch Calendar
        google_service_instance.patch_calendar_event_times(
            calendar_id=event["calendar_id"],
            event_id=event_id,
            start_iso=event["start_iso"],
            end_iso=new_end_iso
        )

        # Shift Linked Task
        task_id = event.get("task_id")
        if task_id:
            new_task_due = (new_end + timedelta(minutes=5)).isoformat()
            google_service_instance.update_google_task_due(task_id, new_task_due)

        # Update DB and set status back to active
        await db_instance.update_event_times(event_id, event["start_iso"], new_end_iso, status="active")

        new_end_fmt = format_human_datetime(new_end_iso)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏳ *Extended by {minutes} minutes!*\n\n"
                f"📌 *Block:* `{event['title']}`\n"
                f"🕒 *New End Time:* {new_end_fmt}\n"
                f"📋 Linked Task due time shifted accordingly.\n\n"
                f"_I will check in again when the extension concludes!_"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to extend event: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to extend event: {e}")

async def execute_reschedule_to(
    chat_id: int,
    event_id: str,
    new_start_iso: str,
    new_end_iso: str,
    context: ContextTypes.DEFAULT_TYPE
):
    """Reschedules an existing event to an explicit new start/end time."""
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in local database.")
        return

    try:
        google_service_instance.reschedule_calendar_event(
            calendar_id=event["calendar_id"],
            event_id=event_id,
            new_start_iso=new_start_iso,
            new_end_iso=new_end_iso
        )

        task_id = event.get("task_id")
        if task_id:
            new_task_due = (datetime.fromisoformat(new_end_iso) + timedelta(minutes=5)).isoformat()
            google_service_instance.update_google_task_due(task_id, new_task_due)

        await db_instance.update_event_times(event_id, new_start_iso, new_end_iso, status="active")

        new_start_fmt = format_human_datetime(new_start_iso)
        new_end_fmt = format_human_datetime(new_end_iso).split(" at ")[-1]

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔄 *Rescheduled!*\n\n"
                f"📌 *Block:* `{event['title']}`\n"
                f"🕒 *New Time:* {new_start_fmt} – {new_end_fmt}\n"
                f"📋 Google Task due date updated."
            ),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to reschedule event to specific time: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to reschedule: {e}")


async def execute_reschedule(chat_id: int, event_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Finds next available gap and reschedules event and task."""
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in local database.")
        return

    # Calculate duration
    start_dt = datetime.fromisoformat(event["start_iso"])
    end_dt = datetime.fromisoformat(event["end_iso"])
    duration_mins = max(15, int((end_dt - start_dt).total_seconds() / 60))

    active_cal_ids = [
        cid for name, cid in Config.GOOGLE_CALENDAR_IDS.items()
        if name != "Completed Tasks" and cid
    ]

    try:
        new_start_iso, new_end_iso = google_service_instance.find_next_free_slot(
            duration_minutes=duration_mins,
            calendar_ids=active_cal_ids
        )

        # Patch Calendar
        google_service_instance.patch_calendar_event_times(
            calendar_id=event["calendar_id"],
            event_id=event_id,
            start_iso=new_start_iso,
            end_iso=new_end_iso
        )

        # Patch Task
        task_id = event.get("task_id")
        if task_id:
            new_task_due = (datetime.fromisoformat(new_end_iso) + timedelta(minutes=5)).isoformat()
            google_service_instance.update_google_task_due(task_id, new_task_due)

        # Update DB
        await db_instance.update_event_times(event_id, new_start_iso, new_end_iso, status="active")

        new_start_fmt = format_human_datetime(new_start_iso)
        new_end_fmt = format_human_datetime(new_end_iso).split(" at ")[-1]

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔄 *Session Rescheduled!*\n\n"
                f"📌 *Block:* `{event['title']}`\n"
                f"🕒 *New Timeslot:* {new_start_fmt} – {new_end_fmt}\n"
                f"📋 Google Task due date updated.\n\n"
                f"_Accountability monitor will ping you during the new timeslot._"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Failed to reschedule: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to reschedule event: {e}")

# =====================================================================
# CALLBACK QUERY HANDLERS
# =====================================================================

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = update.effective_user
    chat_id = update.effective_chat.id

    if not is_authorized(user.id):
        await query.edit_message_text("⛔ Unauthorized.")
        return

    # Calendar Picker callback
    if data.startswith("pick_cal:"):
        selected_cal = data.split("pick_cal:")[-1]
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "select_calendar":
            await query.edit_message_text("⚠️ Selection expired. Please request scheduling again.")
            return

        payload = pending.get("payload", {})
        title = payload.get("title")
        start_iso = payload.get("start_iso")
        end_iso = payload.get("end_iso")

        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text(f"Selected calendar: *{selected_cal}*. Checking availability...", parse_mode=ParseMode.MARKDOWN)

        await schedule_and_reply(chat_id, user.id, title, start_iso, end_iso, selected_cal, context,
                               recurrence=payload.get("recurrence"))
        return

    # Accept Proposed Slot callback
    if data == "accept_proposal":
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "propose_slot":
            await query.edit_message_text("⚠️ Proposed slot expired.")
            return

        payload = pending.get("payload", {})
        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text("Booking proposed slot...")

        await schedule_and_reply(
            chat_id=chat_id,
            user_id=user.id,
            title=payload["title"],
            start_iso=payload["start_iso"],
            end_iso=payload["end_iso"],
            target_calendar_name=payload["target_calendar_name"],
            context=context,
            recurrence=payload.get("recurrence")
        )
        return

    if data == "cancel_proposal":
        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text("❌ Scheduling request cancelled.")
        return

    # Delete: first confirmation prompt (from /delete list)
    if data.startswith("delete_ask:"):
        event_id = data.split("delete_ask:")[-1]
        event = await db_instance.get_tracked_event(event_id)
        if not event:
            await query.edit_message_text("⚠️ Event not found.")
            return
        short_title = event["title"].replace("⏳ ", "").replace("⏳", "").strip()
        start_fmt = format_human_datetime(event["start_iso"])
        keyboard = [[
            InlineKeyboardButton("🗑️ Yes, Delete It", callback_data=f"delete_confirm:{event_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
        ]]
        await query.edit_message_text(
            f"⚠️ *Confirm Deletion*\n\n"
            f"📌 *{short_title}*\n"
            f"🕒 {start_fmt}\n\n"
            f"This will permanently remove the event from Google Calendar.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Delete: confirmed — actually delete
    if data.startswith("delete_confirm:"):
        event_id = data.split("delete_confirm:")[-1]
        event = await db_instance.get_tracked_event(event_id)
        if not event:
            await query.edit_message_text("⚠️ Event not found.")
            return
        short_title = event["title"].replace("⏳ ", "").replace("⏳", "").strip()
        try:
            google_service_instance.delete_calendar_event(
                calendar_id=event["calendar_id"],
                event_id=event_id
            )
            # Delete linked task if present
            task_id = event.get("task_id")
            if task_id:
                google_service_instance.delete_google_task(task_id)
            # Mark deleted in DB
            await db_instance.update_event_status(event_id, "deleted")
            await query.edit_message_text(
                f"🗑️ *Deleted!*\n\n"
                f"📌 `{short_title}` has been permanently removed from Google Calendar.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Failed to delete event: {e}")
        return

    # Delete: cancelled
    if data == "delete_cancel":
        await query.edit_message_text("✅ Deletion cancelled.")
        return

    # Block Status: Done
    if data.startswith("status_done:"):
        event_id = data.split("status_done:")[-1]
        event = await db_instance.get_tracked_event(event_id)
        if not event:
            await query.edit_message_text("⚠️ Event record not found.")
            return

        completed_cal_id = Config.get_calendar_id("Completed Tasks")
        if not completed_cal_id:
            await query.edit_message_text("⚠️ 'Completed Tasks' calendar is not configured.")
            return

        try:
            # Move event to Completed Tasks calendar
            google_service_instance.move_calendar_event(
                source_cal_id=event["calendar_id"],
                dest_cal_id=completed_cal_id,
                event_id=event_id
            )

            # Patch summary: replace ⏳ with ✅
            old_title = event["title"]
            new_title = old_title.replace("⏳", "✅").strip()
            if not new_title.startswith("✅"):
                new_title = f"✅ {new_title}"

            google_service_instance.patch_calendar_event_title(
                calendar_id=completed_cal_id,
                event_id=event_id,
                new_title=new_title
            )

            # Complete Linked Task
            task_id = event.get("task_id")
            if task_id:
                google_service_instance.complete_google_task(task_id)

            # Update DB
            await db_instance.update_event_calendar(
                event_id=event_id,
                new_calendar_id=completed_cal_id,
                new_calendar_name="Completed Tasks",
                new_title=new_title,
                status="completed"
            )

            await query.edit_message_text(
                f"🎉 *Fantastic work!*\n\n"
                f"📌 *Block:* `{new_title}`\n"
                f"📁 *Moved to:* `Completed Tasks`\n"
                f"📋 *Google Task:* Marked as Completed ✅",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Error marking event done: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Error updating completed block: {e}")
        return

    # Block Status: Working on it (Extension prompt)
    if data.startswith("status_extend:"):
        event_id = data.split("status_extend:")[-1]
        keyboard = [
            [
                InlineKeyboardButton("+15 min", callback_data=f"ext_quick:{event_id}:15"),
                InlineKeyboardButton("+30 min", callback_data=f"ext_quick:{event_id}:30")
            ],
            [
                InlineKeyboardButton("+45 min", callback_data=f"ext_quick:{event_id}:45"),
                InlineKeyboardButton("+60 min", callback_data=f"ext_quick:{event_id}:60")
            ],
            [
                InlineKeyboardButton("⌨️ Custom Minutes", callback_data=f"ext_custom:{event_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "⏳ *Still working on it!*\n\nHow many minutes would you like to extend this block by?",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Quick Extension Callback
    if data.startswith("ext_quick:"):
        _, event_id, mins_str = data.split(":")
        mins = int(mins_str)
        await query.edit_message_text(f"Extending block by {mins} minutes...")
        await execute_extension(chat_id, event_id, mins, context)
        return

    # Custom Extension Callback
    if data.startswith("ext_custom:"):
        event_id = data.split("ext_custom:")[-1]
        await db_instance.set_pending_dialog(user.id, "await_custom_extension", {"event_id": event_id})
        await query.edit_message_text(
            "⌨️ *Custom Extension:*\n\nPlease reply with the number of minutes (e.g. `20` or `45`).\nType /cancel to abort.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Block Status: Reschedule
    if data.startswith("status_resched:"):
        event_id = data.split("status_resched:")[-1]
        await query.edit_message_text("🔍 Searching for next available slot...")
        await execute_reschedule(chat_id, event_id, context)
        return

def register_handlers(application):
    """Registers command, message, and callback handlers to the Telegram application."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delete", delete_command))

    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
