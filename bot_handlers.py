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

# =====================================================================
# HELPERS
# =====================================================================

def is_authorized(user_id: int) -> bool:
    """Checks if user is in TELEGRAM_ALLOWED_USER_IDS."""
    if not Config.TELEGRAM_ALLOWED_USER_IDS:
        return True
    return user_id in Config.TELEGRAM_ALLOWED_USER_IDS

def format_human_datetime(iso_str: str) -> str:
    """Formats ISO datetime into readable string like 'Tue, Oct 21 at 10:00'."""
    try:
        dt = datetime.fromisoformat(iso_str).astimezone(Config.USER_TIMEZONE)
        return dt.strftime("%a, %b %d at %H:%M")
    except Exception:
        return iso_str

def format_duration(start_iso: str, end_iso: str) -> str:
    """Returns human-readable duration like '1h 30m'."""
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        mins = int((end - start).total_seconds() / 60)
        if mins >= 60:
            h, m = divmod(mins, 60)
            return f"{h}h{f' {m}m' if m else ''}"
        return f"{mins}m"
    except Exception:
        return ""

def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Returns the persistent main-menu keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Schedule", callback_data="menu:schedule"),
            InlineKeyboardButton("📋 Upcoming", callback_data="menu:events"),
        ],
        [
            InlineKeyboardButton("🕰️ Past / Review", callback_data="menu:past"),
            InlineKeyboardButton("🗑️ Delete Event", callback_data="menu:delete"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="menu:help"),
        ],
    ])

def event_action_keyboard(event_id: str) -> InlineKeyboardMarkup:
    """Per-event action buttons shown after scheduling and on status detail view."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Mark Done", callback_data=f"ev_done:{event_id}"),
            InlineKeyboardButton("⏳ Extend", callback_data=f"ev_extend:{event_id}"),
        ],
        [
            InlineKeyboardButton("🔄 Reschedule", callback_data=f"ev_resched_menu:{event_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"ev_del_ask:{event_id}"),
        ],
    ])

async def complete_tracked_event(event_id: str) -> dict | None:
    """
    Marks an event as completed:
    - Moves event to 'Completed Tasks' calendar in Google Calendar
    - Patches summary with ✅
    - Marks linked Google Task as completed
    - Updates SQLite status to 'completed'
    """
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        return None

    completed_cal_id = Config.get_calendar_id("Completed Tasks")
    if not completed_cal_id:
        logger.warning("'Completed Tasks' calendar not configured.")
        return None

    orig_cal_id = event.get("original_calendar_id") or event["calendar_id"]
    orig_cal_name = event.get("original_calendar_name") or event["calendar_name"]

    try:
        if event["calendar_id"] != completed_cal_id:
            google_service_instance.move_calendar_event(
                source_cal_id=event["calendar_id"],
                dest_cal_id=completed_cal_id,
                event_id=event_id
            )

        old_title = event["title"]
        new_title = old_title.replace("⏳", "✅").strip()
        if not new_title.startswith("✅"):
            new_title = f"✅ {new_title}"

        google_service_instance.patch_calendar_event_title(
            calendar_id=completed_cal_id,
            event_id=event_id,
            new_title=new_title
        )

        task_id = event.get("task_id")
        if task_id:
            google_service_instance.complete_google_task(task_id)

        await db_instance.update_event_completed(
            event_id=event_id,
            completed_cal_id=completed_cal_id,
            new_title=new_title,
            orig_cal_id=orig_cal_id,
            orig_cal_name=orig_cal_name
        )

        event["title"] = new_title
        event["status"] = "completed"
        return event

    except Exception as e:
        logger.error(f"Error completing event {event_id}: {e}", exc_info=True)
        return None

async def reopen_tracked_event(event_id: str) -> dict | None:
    """
    Reopens a completed event:
    - Moves event from 'Completed Tasks' back to its original category calendar
    - Patches summary replacing ✅ with ⏳
    - Marks linked Google Task as needsAction
    - Updates SQLite status back to 'active'
    """
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        return None

    orig_cal_id = event.get("original_calendar_id")
    orig_cal_name = event.get("original_calendar_name") or "Personal"
    if not orig_cal_id:
        orig_cal_id = Config.get_calendar_id(orig_cal_name)

    completed_cal_id = Config.get_calendar_id("Completed Tasks")

    try:
        target_cal_id = orig_cal_id or event["calendar_id"]
        if target_cal_id and event["calendar_id"] == completed_cal_id:
            google_service_instance.move_calendar_event(
                source_cal_id=completed_cal_id,
                dest_cal_id=target_cal_id,
                event_id=event_id
            )

        old_title = event["title"]
        new_title = old_title.replace("✅", "⏳").strip()
        if not new_title.startswith("⏳"):
            new_title = f"⏳ {new_title}"

        google_service_instance.patch_calendar_event_title(
            calendar_id=target_cal_id,
            event_id=event_id,
            new_title=new_title
        )

        task_id = event.get("task_id")
        if task_id:
            google_service_instance.reopen_google_task(task_id)

        await db_instance.update_event_reopened(
            event_id=event_id,
            orig_cal_id=target_cal_id,
            orig_cal_name=orig_cal_name,
            new_title=new_title
        )

        event["title"] = new_title
        event["status"] = "active"
        return event

    except Exception as e:
        logger.error(f"Error reopening event {event_id}: {e}", exc_info=True)
        return None

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
        f"I am *Kairos*, your AI Executive Auto-Scheduler.\n\n"
        f"Just *talk to me naturally* — I understand things like:\n"
        f'_"Study algorithms tomorrow at 2 PM for 2 hours"_\n'
        f'_"Delete my morning run"_\n'
        f'_"Reschedule my grading session to Friday at 3 PM"_\n'
        f'_"Every weekday gym at 7 AM"_\n\n'
        f"⚙️ `{tz_name}` · `{configured_cals}` calendars\n\n"
        f"Or use the quick menu below:"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    help_text = (
        "⏳ *Kairos — Quick Reference:*\n\n"
        "🗣️ *Just talk naturally:*\n"
        '• _"Study linear algebra for 2h tomorrow at 10 AM"_\n'
        '• _"Delete my database session"_\n'
        '• _"Reschedule grading to Friday 3 PM"_\n'
        '• _"Schedule a daily gym at 7 AM for 45 mins"_\n\n'
        "📋 *Commands:*\n"
        "  /status — view & manage upcoming sessions\n"
        "  /past or /review — view ended sessions & review completion\n"
        "  /delete — pick an event to delete\n"
        "  /cancel — cancel current action\n"
        "  /help — this message\n\n"
        "⏰ *Accountability & 2-Way Sync:*\n"
        "• When a block ends, Kairos pings you for completion.\n"
        "• If you mark a task done in Google Calendar / Tasks app, Kairos auto-syncs it to Completed Tasks!\n"
        "• Reopen or review any past session anytime with /past.\n\n"
        "🔁 *Recurrence:* daily · weekly · monthly · weekdays · weekends · mon–sun"
    )
    await update.message.reply_text(
        help_text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard()
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    upcoming = await db_instance.get_upcoming_events(chat_id, limit=8)

    # Live service check
    if not google_service_instance.calendar_service:
        google_service_instance._init_services()
    cal_ok = "✅" if google_service_instance.calendar_service else "❌"
    tasks_ok = "✅" if google_service_instance.tasks_service else "⚠️"

    diag = (
        f"⚙️ Calendar {cal_ok} · Tasks {tasks_ok} · "
        f"`{Config.USER_TIMEZONE_STR}`\n\n"
    )

    if not upcoming:
        await update.message.reply_text(
            f"{diag}📭 *No upcoming sessions scheduled.*\n\n"
            "_Use the menu below or just tell me what to schedule._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard()
        )
        return

    # Build numbered list text
    lines = [f"{diag}📅 *Upcoming Sessions ({len(upcoming)}):*\n"]
    keyboard = []

    for i, ev in enumerate(upcoming, 1):
        title_clean = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
        start_fmt = format_human_datetime(ev["start_iso"])
        end_time = format_human_datetime(ev["end_iso"]).split(" at ")[-1]
        dur = format_duration(ev["start_iso"], ev["end_iso"])
        cal = ev["calendar_name"]

        lines.append(
            f"*{i}.* ⏳ {title_clean}\n"
            f"   🕒 {start_fmt} – {end_time}  _{dur}_ · 📁 _{cal}_"
        )

        # Per-event action row (index-labeled so user knows which event)
        short = title_clean[:16]
        keyboard.append([
            InlineKeyboardButton(f"{i}:✅", callback_data=f"ev_done:{ev['event_id']}"),
            InlineKeyboardButton(f"{i}:⏳+", callback_data=f"ev_extend:{ev['event_id']}"),
            InlineKeyboardButton(f"{i}:🔄", callback_data=f"ev_resched_menu:{ev['event_id']}"),
            InlineKeyboardButton(f"{i}:🗑️", callback_data=f"ev_del_ask:{ev['event_id']}"),
        ])

    lines.append("\n_Buttons: ✅ Done · ⏳ Extend · 🔄 Reschedule · 🗑️ Delete_")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        return

    pending = await db_instance.get_pending_dialog(user.id)
    if pending:
        await db_instance.clear_pending_dialog(user.id)
        await update.message.reply_text(
            "✅ Action cancelled.",
            reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text("No active action to cancel.", reply_markup=main_menu_keyboard())


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists upcoming tracked events with a Delete button next to each one."""
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    upcoming = await db_instance.get_upcoming_events(chat_id, limit=10)

    if not upcoming:
        await update.message.reply_text(
            "📭 No active scheduled events found.",
            reply_markup=main_menu_keyboard()
        )
        return

    lines = ["🗑️ *Select an event to delete:*\n"]
    keyboard = []
    for i, ev in enumerate(upcoming, 1):
        start_fmt = format_human_datetime(ev["start_iso"])
        short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
        lines.append(f"*{i}.* {short_title} — {start_fmt}")
        keyboard.append([
            InlineKeyboardButton(
                f"🗑️ {i}. {short_title[:28]}",
                callback_data=f"ev_del_ask:{ev['event_id']}"
            )
        ])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")])

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def past_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Displays past / ended sessions (especially ones missed for review)
    with interactive buttons to mark as Done, Reopen (Not Done), Reschedule, or Delete.
    """
    user = update.effective_user
    if not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    past_events = await db_instance.get_past_events(chat_id, limit=8)

    if not past_events:
        await update.message.reply_text(
            "📭 *No past sessions found in database.*",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    lines = ["🕰️ *Past Sessions & Completion Review:*\n"]
    keyboard = []

    for i, ev in enumerate(past_events, 1):
        status = ev["status"]
        title_clean = ev["title"].replace("⏳ ", "").replace("⏳", "").replace("✅ ", "").replace("✅", "").strip()
        start_fmt = format_human_datetime(ev["start_iso"])
        end_time = format_human_datetime(ev["end_iso"]).split(" at ")[-1]
        dur = format_duration(ev["start_iso"], ev["end_iso"])

        if status == "completed":
            badge = "✅ Completed"
            lines.append(f"*{i}.* ✅ `{title_clean}`\n   🕒 {start_fmt} – {end_time}  _{dur}_ · {badge}")
            keyboard.append([
                InlineKeyboardButton(f"{i}: ↩️ Not Done", callback_data=f"ev_undone:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}: 🗑️ Delete", callback_data=f"ev_del_ask:{ev['event_id']}")
            ])
        else:
            badge = "⏳ Needs Review" if status == "pending_review" else "⏳ Ended"
            lines.append(f"*{i}.* ⏳ *{title_clean}*\n   🕒 {start_fmt} – {end_time}  _{dur}_ · _{badge}_")
            keyboard.append([
                InlineKeyboardButton(f"{i}: ✅ Done", callback_data=f"ev_done:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}: ⏳+", callback_data=f"ev_extend:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}: 🔄", callback_data=f"ev_resched_menu:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}: 🗑️", callback_data=f"ev_del_ask:{ev['event_id']}")
            ])

    lines.append("\n_Buttons: ✅ Done · ↩️ Not Done · ⏳+ Extend · 🔄 Reschedule_")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
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
    recurrence: str | None = None,
    reminder_minutes: int | None = 10
):
    """
    Performs availability checking, inserts the Google Calendar event + Task,
    updates SQLite, and sends a rich success message with action buttons.
    """
    target_cal_id = Config.get_calendar_id(target_calendar_name)
    if not target_cal_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"❌ Calendar '{target_calendar_name}' is not configured."
        )
        return

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

    # Handle conflict — propose next free slot with buttons (and trim option if overlapping)
    if not is_free:
        dt_start = datetime.fromisoformat(start_iso)
        dt_end = datetime.fromisoformat(end_iso)
        duration_mins = max(15, int((dt_end - dt_start).total_seconds() / 60))

        next_start_iso, next_end_iso = google_service_instance.find_next_free_slot(
            duration_minutes=duration_mins,
            calendar_ids=active_cal_ids,
            start_search_dt=dt_start
        )

        # Detect overlapping event to offer trimming / override
        overlapping = await db_instance.get_overlapping_events(chat_id, start_iso, end_iso)
        if not overlapping:
            overlapping = google_service_instance.get_events_in_range(active_cal_ids, start_iso, end_iso)

        trim_info = None
        if overlapping:
            conf_ev = overlapping[0]
            clean_conf_title = conf_ev.get("title", "Existing Event").replace("⏳ ", "").replace("⏳", "").replace("✅", "").strip()
            try:
                conf_start_dt = datetime.fromisoformat(conf_ev["start_iso"])
                conf_end_dt = datetime.fromisoformat(conf_ev["end_iso"])

                if conf_start_dt < dt_start:
                    trimmed_start_iso = conf_ev["start_iso"]
                    trimmed_end_iso = start_iso
                    cut_mins = max(1, int((conf_end_dt - dt_start).total_seconds() / 60))
                    new_desc = f"end at {format_human_datetime(start_iso).split(' at ')[-1]}"
                else:
                    trimmed_start_iso = end_iso
                    trimmed_end_iso = conf_ev["end_iso"] if conf_end_dt > dt_end else (dt_end + timedelta(minutes=30)).isoformat()
                    cut_mins = max(1, int((min(conf_end_dt, dt_end) - conf_start_dt).total_seconds() / 60))
                    new_desc = f"start at {format_human_datetime(end_iso).split(' at ')[-1]}"

                trim_info = {
                    "conflicting_event": conf_ev,
                    "clean_title": clean_conf_title,
                    "trimmed_start_iso": trimmed_start_iso,
                    "trimmed_end_iso": trimmed_end_iso,
                    "cut_mins": cut_mins,
                    "new_boundary_desc": new_desc,
                    "new_event": {
                        "title": title,
                        "start_iso": start_iso,
                        "end_iso": end_iso,
                        "target_calendar_name": target_calendar_name,
                        "recurrence": recurrence,
                        "reminder_minutes": reminder_minutes
                    }
                }
            except Exception as ex:
                logger.warning(f"Could not calculate trim_info: {ex}")

        proposal_payload = {
            "title": title,
            "start_iso": next_start_iso,
            "end_iso": next_end_iso,
            "target_calendar_name": target_calendar_name,
            "recurrence": recurrence,
            "reminder_minutes": reminder_minutes,
            "trim_info": trim_info
        }
        await db_instance.set_pending_dialog(user_id, "propose_slot", proposal_payload)

        start_fmt = format_human_datetime(start_iso)
        end_fmt = format_human_datetime(end_iso).split(" at ")[-1]
        next_start_fmt = format_human_datetime(next_start_iso)
        next_end_fmt = format_human_datetime(next_end_iso).split(" at ")[-1]

        keyboard = [
            [InlineKeyboardButton("📅 Book This Slot", callback_data="accept_proposal")],
        ]
        if trim_info:
            short_conf = trim_info["clean_title"][:18]
            keyboard.append([
                InlineKeyboardButton(f"✂️ Trim '{short_conf}' & Book", callback_data="ask_trim_override")
            ])
        keyboard.append([InlineKeyboardButton("❌ Dismiss", callback_data="cancel_proposal")])

        conflict_msg = (
            f"⚠️ *Conflict Detected!*\n\n"
            f"Busy: `{start_fmt} – {end_fmt}`"
        )
        if trim_info:
            conflict_msg += f"\nOverlap: *{trim_info['clean_title']}*"
        conflict_msg += (
            f"\n\n💡 *Next Available:*\n"
            f"🕒 *{next_start_fmt} – {next_end_fmt}*\n\n"
            f"Schedule `{title}` here instead, or trim the existing session?"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=conflict_msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # No conflict: create event + task
    try:
        clean_title = title.replace("⏳", "").replace("✅", "").strip()
        cal_event = google_service_instance.create_calendar_event(
            calendar_id=target_cal_id,
            title=clean_title,
            start_iso=start_iso,
            end_iso=end_iso,
            metadata={"calendar_name": target_calendar_name, "chat_id": str(chat_id)},
            recurrence=recurrence,
            reminder_minutes=reminder_minutes
        )
        event_id = cal_event.get("id")

        dt_end = datetime.fromisoformat(end_iso)
        task_due = dt_end + timedelta(minutes=5)
        task_id = google_service_instance.create_google_task(
            title=clean_title,
            due_iso=task_due.isoformat(),
            notes=f"Linked calendar block in '{target_calendar_name}'"
        )

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
        dur = format_duration(start_iso, end_iso)
        task_status = "✅ Created" if task_id else "⚠️ Skipped"
        recurrence_label = f"\n🔁 *Repeats:* `{recurrence}`" if recurrence else ""
        reminder_label = f"\n🔔 *Reminder:* `{reminder_minutes}m before` (Calendar & Telegram)" if reminder_minutes else ""

        success_text = (
            f"✨ *Scheduled!*\n\n"
            f"📌 `{cal_event.get('summary')}`\n"
            f"📁 {target_calendar_name}\n"
            f"🕒 {start_fmt} – {end_fmt}  _{dur}_"
            f"{recurrence_label}"
            f"{reminder_label}\n"
            f"📋 Task: {task_status}\n\n"
            f"🔔 _I'll ping you when this block ends._"
        )

        # Post-schedule action buttons
        post_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Mark Done", callback_data=f"ev_done:{event_id}"),
                InlineKeyboardButton("⏳ Extend", callback_data=f"ev_extend:{event_id}"),
            ],
            [
                InlineKeyboardButton("🔄 Reschedule", callback_data=f"ev_resched_menu:{event_id}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"ev_del_ask:{event_id}"),
            ],
        ])

        await context.bot.send_message(
            chat_id=chat_id,
            text=success_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_keyboard
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

    # Active pending dialog check
    pending = await db_instance.get_pending_dialog(user.id)
    if pending:
        action = pending.get("action")
        payload = pending.get("payload", {})

        if action == "await_custom_extension":
            clean_digits = "".join([c for c in user_text if c.isdigit()])
            if clean_digits:
                mins = int(clean_digits)
                event_id = payload.get("event_id")
                await db_instance.clear_pending_dialog(user.id)
                await execute_extension(chat_id, event_id, mins, context)
                return
            else:
                await update.message.reply_text(
                    "⚠️ Reply with a number of minutes (e.g. `30`) or /cancel.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")
                    ]])
                )
                return

        if action == "await_reschedule_time":
            # User typed a new time for an existing event
            event_id = payload.get("event_id")
            await db_instance.clear_pending_dialog(user.id)
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            # Feed back to LLM to parse the new time in context of the event
            llm_result = await llm_service_instance.parse_user_request(
                f"Reschedule to: {user_text}"
            )
            args = llm_result.get("arguments", {}) or {}
            if llm_result.get("tool_name") in ("reschedule_session", "schedule_session"):
                new_start = args.get("new_start_iso") or args.get("start_iso")
                new_end = args.get("new_end_iso") or args.get("end_iso")
                if new_start and new_end:
                    await execute_reschedule_to(chat_id, event_id, new_start, new_end, context)
                    return
            await update.message.reply_text(
                "🤔 Couldn't parse that time. Try: _\"tomorrow at 3 PM\"_ or _\"Friday at 10 AM\"_.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    llm_result = await llm_service_instance.parse_user_request(user_text)

    tool_name = llm_result.get("tool_name") if llm_result.get("is_tool_call") else None
    args = llm_result.get("arguments", {}) or {}

    # ── schedule_session ──────────────────────────────────────────────
    if tool_name == "schedule_session":
        title = args.get("title", "Study Session")
        start_iso = args.get("start_iso")
        end_iso = args.get("end_iso")
        target_cal = args.get("target_calendar_name")
        recurrence = args.get("recurrence")
        raw_rem = args.get("reminder_minutes")
        try:
            reminder_minutes = int(raw_rem) if raw_rem is not None else 10
        except Exception:
            reminder_minutes = 10

        if not start_iso or not end_iso:
            # Time unclear — show quick time picker
            await _send_time_picker(update, user, title, recurrence)
            return

        if target_cal == "uncertain" or not Config.get_calendar_id(target_cal):
            pending_payload = {
                "title": title,
                "start_iso": start_iso,
                "end_iso": end_iso,
                "recurrence": recurrence,
                "reminder_minutes": reminder_minutes
            }
            await db_instance.set_pending_dialog(user.id, "select_calendar", pending_payload)
            await _send_calendar_picker(update, title, start_iso, end_iso, recurrence)
            return

        await schedule_and_reply(chat_id, user.id, title, start_iso, end_iso, target_cal, context,
                                 recurrence=recurrence, reminder_minutes=reminder_minutes)

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
                f"❌ No event found matching *\"{args.get('search_title')}\"*.\n"
                "Use /delete to see all scheduled events.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 Show All Events", callback_data="menu:delete")
                ]])
            )
            return

        ev = matches[0]
        short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
        start_fmt = format_human_datetime(ev["start_iso"])
        await update.message.reply_text(
            f"⚠️ *Confirm Deletion*\n\n"
            f"📌 *{short_title}*\n🕒 {start_fmt}\n\n"
            f"This permanently removes it from Google Calendar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"delete_confirm:{ev['event_id']}"),
                InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )

    # ── reschedule_session ────────────────────────────────────────────
    elif tool_name == "reschedule_session":
        search_title = args.get("search_title", "").strip().lower()
        new_start_iso = args.get("new_start_iso")
        new_end_iso = args.get("new_end_iso")

        if not new_start_iso or not new_end_iso:
            await update.message.reply_text(
                "🤔 I couldn't determine the new time. Please specify it, e.g. _\"tomorrow at 3 PM\"_.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        upcoming = await db_instance.get_upcoming_events(chat_id, limit=20)
        matches = [
            ev for ev in upcoming
            if search_title in ev["title"].lower()
            or search_title in ev["title"].replace("⏳ ", "").lower()
        ]

        if not matches:
            search_query = args.get("search_title", "")
            await update.message.reply_text(
                f"❌ No event found matching *\"{search_query}\"*.\n"
                "Use /status to view your upcoming events.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📋 My Events", callback_data="menu:events")
                ]])
            )
            return

        ev = matches[0]
        await update.message.reply_text("🔄 Rescheduling...")
        await execute_reschedule_to(chat_id, ev["event_id"], new_start_iso, new_end_iso, context)

    # ── conversational fallback ───────────────────────────────────────
    else:
        reply = llm_result.get("reply_text") or "How can I help? Use the menu below."
        await update.message.reply_text(
            reply,
            reply_markup=main_menu_keyboard()
        )


# =====================================================================
# TIME + CALENDAR PICKERS
# =====================================================================

async def _send_time_picker(update: Update, user, title: str, recurrence: str | None):
    """Shows a quick time selection grid when time is not specified."""
    tz = Config.USER_TIMEZONE
    now = datetime.now(tz)

    # Build suggested time slots
    slots = []
    for label, delta_h, hour in [
        ("Today 9 AM", 0, 9),
        ("Today 12 PM", 0, 12),
        ("Today 3 PM", 0, 15),
        ("Today 6 PM", 0, 18),
        ("Today 8 PM", 0, 20),
        ("Tomorrow 9 AM", 1, 9),
        ("Tomorrow 12 PM", 1, 12),
        ("Tomorrow 3 PM", 1, 15),
    ]:
        slot_dt = (now + timedelta(days=delta_h)).replace(hour=hour, minute=0, second=0, microsecond=0)
        if slot_dt > now + timedelta(minutes=15):
            slots.append((label, slot_dt.isoformat()))

    slots = slots[:6]  # Max 6 suggestions

    # Save pending
    await db_instance.set_pending_dialog(user.id, "select_calendar", {
        "title": title, "recurrence": recurrence,
        "awaiting_time": True
    })

    keyboard = []
    row = []
    for label, iso in slots:
        row.append(InlineKeyboardButton(label, callback_data=f"quicktime:{iso}:{title[:20]}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_proposal")])

    await update.message.reply_text(
        f"🕒 *When should I schedule:*\n`{title}`?\n\nPick a slot or just type the time:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )


async def _send_calendar_picker(update, title: str, start_iso: str, end_iso: str, recurrence: str | None):
    """Shows calendar selection buttons when category is uncertain."""
    keyboard = []
    row = []
    for cal_name in ACTIVE_CALENDARS:
        row.append(InlineKeyboardButton(cal_name, callback_data=f"pick_cal:{cal_name}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    start_fmt = format_human_datetime(start_iso)
    end_fmt = format_human_datetime(end_iso).split(" at ")[-1]
    recurrence_hint = f"\n🔁 `{recurrence}`" if recurrence else ""

    msg = (
        f"📁 *Which calendar for:*\n`{title}`?\n\n"
        f"🕒 {start_fmt} – {end_fmt}{recurrence_hint}\n\n"
        f"Pick below:"
    )
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


# =====================================================================
# EXTENSION & RESCHEDULE EXECUTORS
# =====================================================================

async def execute_extension(chat_id: int, event_id: str, minutes: int, context: ContextTypes.DEFAULT_TYPE):
    """Extends calendar event and linked task by X minutes."""
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in database.")
        return

    old_end = datetime.fromisoformat(event["end_iso"])
    new_end = old_end + timedelta(minutes=minutes)
    new_end_iso = new_end.isoformat()

    try:
        google_service_instance.patch_calendar_event_times(
            calendar_id=event["calendar_id"],
            event_id=event_id,
            start_iso=event["start_iso"],
            end_iso=new_end_iso
        )

        task_id = event.get("task_id")
        if task_id:
            new_task_due = (new_end + timedelta(minutes=5)).isoformat()
            google_service_instance.update_google_task_due(task_id, new_task_due)

        await db_instance.update_event_times(event_id, event["start_iso"], new_end_iso, status="active")

        new_end_fmt = format_human_datetime(new_end_iso)
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏳ *Extended by {minutes} minutes!*\n\n"
                f"📌 `{event['title']}`\n"
                f"🕒 New end: {new_end_fmt}\n\n"
                f"_I'll check in again when the extension concludes!_"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=event_action_keyboard(event_id)
        )
    except Exception as e:
        logger.error(f"Failed to extend event: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to extend: {e}")


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
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in database.")
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
        dur = format_duration(new_start_iso, new_end_iso)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"🔄 *Rescheduled!*\n\n"
                f"📌 `{event['title']}`\n"
                f"🕒 {new_start_fmt} – {new_end_fmt}  _{dur}_\n"
                f"📋 Task due updated."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=event_action_keyboard(event_id)
        )
    except Exception as e:
        logger.error(f"Failed to reschedule: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to reschedule: {e}")


async def execute_reschedule(chat_id: int, event_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Finds next available gap and reschedules event and task."""
    event = await db_instance.get_tracked_event(event_id)
    if not event:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Event not found in database.")
        return

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
        await execute_reschedule_to(chat_id, event_id, new_start_iso, new_end_iso, context)

    except Exception as e:
        logger.error(f"Failed to reschedule: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to find free slot: {e}")


# =====================================================================
# CALLBACK QUERY HANDLER
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

    # ── Main Menu shortcuts ───────────────────────────────────────────
    if data == "menu:schedule":
        await query.edit_message_text(
            "💬 *Just tell me what to schedule!*\n\n"
            "Examples:\n"
            '• _"Study algorithms tomorrow at 2 PM for 2 hours"_\n'
            '• _"Daily gym at 7 AM for 45 minutes"_\n'
            '• _"Grading session Thursday 3–5 PM"_',
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "menu:events":
        upcoming = await db_instance.get_upcoming_events(chat_id, limit=8)
        if not upcoming:
            await query.edit_message_text(
                "📭 No upcoming sessions scheduled.",
                reply_markup=main_menu_keyboard()
            )
            return
        lines = ["📅 *Upcoming Sessions:*\n"]
        keyboard = []
        for i, ev in enumerate(upcoming, 1):
            title_clean = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
            start_fmt = format_human_datetime(ev["start_iso"])
            end_time = format_human_datetime(ev["end_iso"]).split(" at ")[-1]
            dur = format_duration(ev["start_iso"], ev["end_iso"])
            lines.append(f"*{i}.* ⏳ {title_clean}\n   🕒 {start_fmt} – {end_time}  _{dur}_")
            keyboard.append([
                InlineKeyboardButton(f"{i}:✅", callback_data=f"ev_done:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}:⏳+", callback_data=f"ev_extend:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}:🔄", callback_data=f"ev_resched_menu:{ev['event_id']}"),
                InlineKeyboardButton(f"{i}:🗑️", callback_data=f"ev_del_ask:{ev['event_id']}"),
            ])
        lines.append("\n_✅ Done · ⏳ Extend · 🔄 Reschedule · 🗑️ Delete_")
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "menu:past":
        past_events = await db_instance.get_past_events(chat_id, limit=8)
        if not past_events:
            await query.edit_message_text(
                "📭 *No past sessions found in database.*",
                reply_markup=main_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        lines = ["🕰️ *Past Sessions & Completion Review:*\n"]
        keyboard = []

        for i, ev in enumerate(past_events, 1):
            status = ev["status"]
            title_clean = ev["title"].replace("⏳ ", "").replace("⏳", "").replace("✅ ", "").replace("✅", "").strip()
            start_fmt = format_human_datetime(ev["start_iso"])
            end_time = format_human_datetime(ev["end_iso"]).split(" at ")[-1]
            dur = format_duration(ev["start_iso"], ev["end_iso"])

            if status == "completed":
                badge = "✅ Completed"
                lines.append(f"*{i}.* ✅ `{title_clean}`\n   🕒 {start_fmt} – {end_time}  _{dur}_ · {badge}")
                keyboard.append([
                    InlineKeyboardButton(f"{i}: ↩️ Not Done", callback_data=f"ev_undone:{ev['event_id']}"),
                    InlineKeyboardButton(f"{i}: 🗑️ Delete", callback_data=f"ev_del_ask:{ev['event_id']}")
                ])
            else:
                badge = "⏳ Needs Review" if status == "pending_review" else "⏳ Ended"
                lines.append(f"*{i}.* ⏳ *{title_clean}*\n   🕒 {start_fmt} – {end_time}  _{dur}_ · _{badge}_")
                keyboard.append([
                    InlineKeyboardButton(f"{i}: ✅ Done", callback_data=f"ev_done:{ev['event_id']}"),
                    InlineKeyboardButton(f"{i}: ⏳+", callback_data=f"ev_extend:{ev['event_id']}"),
                    InlineKeyboardButton(f"{i}: 🔄", callback_data=f"ev_resched_menu:{ev['event_id']}"),
                    InlineKeyboardButton(f"{i}: 🗑️", callback_data=f"ev_del_ask:{ev['event_id']}")
                ])

        lines.append("\n_Buttons: ✅ Done · ↩️ Not Done · ⏳+ Extend · 🔄 Reschedule_")
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "menu:delete":
        upcoming = await db_instance.get_upcoming_events(chat_id, limit=10)
        if not upcoming:
            await query.edit_message_text("📭 No active events to delete.", reply_markup=main_menu_keyboard())
            return
        lines = ["🗑️ *Select an event to delete:*\n"]
        keyboard = []
        for i, ev in enumerate(upcoming, 1):
            short_title = ev["title"].replace("⏳ ", "").replace("⏳", "").strip()
            start_fmt = format_human_datetime(ev["start_iso"])
            lines.append(f"*{i}.* {short_title} — {start_fmt}")
            keyboard.append([InlineKeyboardButton(
                f"🗑️ {i}. {short_title[:28]}",
                callback_data=f"ev_del_ask:{ev['event_id']}"
            )])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")])
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "menu:help":
        await query.edit_message_text(
            "⏳ *Kairos — Quick Reference:*\n\n"
            "🗣️ *Just talk naturally:*\n"
            '• _"Study linear algebra for 2h tomorrow at 10 AM"_\n'
            '• _"Delete my database session"_\n'
            '• _"Reschedule grading to Friday 3 PM"_\n'
            '• _"Schedule a daily gym at 7 AM for 45 mins"_\n\n'
            "📋 Commands: /status · /delete · /cancel · /help\n\n"
            "🔁 Recurrence: daily · weekly · weekdays · weekends · mon–sun",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard()
        )
        return

    if data == "cancel_action":
        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text("✅ Action cancelled.", reply_markup=main_menu_keyboard())
        return

    # ── Quick time slot selection (from time picker) ──────────────────
    if data.startswith("quicktime:"):
        # format: quicktime:{iso}:{title}
        parts = data.split(":", 2)
        if len(parts) < 3:
            await query.edit_message_text("⚠️ Invalid quick-time data.")
            return
        _, iso_start, short_title = parts
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending:
            await query.edit_message_text("⚠️ Session expired. Please try again.")
            return
        payload = pending.get("payload", {})
        title = payload.get("title", short_title)
        recurrence = payload.get("recurrence")
        # Default 1-hour duration
        dt_start = datetime.fromisoformat(iso_start)
        dt_end = dt_start + timedelta(hours=1)
        # Show calendar picker next
        pending_payload = {
            "title": title,
            "start_iso": iso_start,
            "end_iso": dt_end.isoformat(),
            "recurrence": recurrence
        }
        await db_instance.set_pending_dialog(user.id, "select_calendar", pending_payload)
        start_fmt = format_human_datetime(iso_start)

        keyboard = []
        row = []
        for cal_name in ACTIVE_CALENDARS:
            row.append(InlineKeyboardButton(cal_name, callback_data=f"pick_cal:{cal_name}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        await query.edit_message_text(
            f"🕒 *{start_fmt}* (1 hour)\n📌 `{title}`\n\n📁 *Which calendar?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Calendar picker callback ──────────────────────────────────────
    if data.startswith("pick_cal:"):
        selected_cal = data.split("pick_cal:")[-1]
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "select_calendar":
            await query.edit_message_text("⚠️ Selection expired. Please try again.")
            return

        payload = pending.get("payload", {})
        title = payload.get("title")
        start_iso = payload.get("start_iso")
        end_iso = payload.get("end_iso")
        recurrence = payload.get("recurrence")

        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text(
            f"📁 *{selected_cal}* selected. Checking availability…",
            parse_mode=ParseMode.MARKDOWN
        )
        await schedule_and_reply(chat_id, user.id, title, start_iso, end_iso, selected_cal, context,
                                 recurrence=recurrence,
                                 reminder_minutes=payload.get("reminder_minutes", 10))
        return

    # ── Accept conflict proposal ──────────────────────────────────────
    if data == "accept_proposal":
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "propose_slot":
            await query.edit_message_text("⚠️ Proposed slot expired.")
            return
        payload = pending.get("payload", {})
        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text("📅 Booking proposed slot…")
        await schedule_and_reply(
            chat_id=chat_id, user_id=user.id,
            title=payload["title"],
            start_iso=payload["start_iso"],
            end_iso=payload["end_iso"],
            target_calendar_name=payload["target_calendar_name"],
            context=context,
            recurrence=payload.get("recurrence"),
            reminder_minutes=payload.get("reminder_minutes", 10)
        )
        return

    if data == "cancel_proposal":
        await db_instance.clear_pending_dialog(user.id)
        await query.edit_message_text("❌ Scheduling cancelled.", reply_markup=main_menu_keyboard())
        return

    # ── Trim & Override Prompt ────────────────────────────────────────
    if data == "ask_trim_override":
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "propose_slot":
            await query.edit_message_text("⚠️ Request expired. Please try scheduling again.")
            return

        payload = pending.get("payload", {})
        trim_info = payload.get("trim_info")
        if not trim_info:
            await query.edit_message_text("⚠️ No conflicting event details found.")
            return

        new_ev = trim_info["new_event"]
        clean_conf = trim_info["clean_title"]
        cut_mins = trim_info["cut_mins"]
        new_desc = trim_info["new_boundary_desc"]

        start_fmt = format_human_datetime(new_ev["start_iso"])
        end_fmt = format_human_datetime(new_ev["end_iso"]).split(" at ")[-1]

        confirm_msg = (
            f"⚠️ *Confirm Trim & Schedule:*\n\n"
            f"📌 *Book:* `{new_ev['title']}`\n"
            f"🕒 *Time:* {start_fmt} – {end_fmt}\n\n"
            f"✂️ *{clean_conf}* will be *cut back by {cut_mins} min* and will *{new_desc}*.\n\n"
            f"Do you want to proceed?"
        )
        keyboard = [
            [InlineKeyboardButton("✂️ Yes, Cut & Book", callback_data="confirm_trim_override")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_proposal")]
        ]
        await query.edit_message_text(
            confirm_msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Confirm Trim & Override ───────────────────────────────────────
    if data == "confirm_trim_override":
        pending = await db_instance.get_pending_dialog(user.id)
        if not pending or pending.get("action") != "propose_slot":
            await query.edit_message_text("⚠️ Request expired.")
            return

        payload = pending.get("payload", {})
        trim_info = payload.get("trim_info")
        if not trim_info:
            await query.edit_message_text("⚠️ No trim information found.")
            return

        await db_instance.clear_pending_dialog(user.id)

        conf_ev = trim_info["conflicting_event"]
        conf_event_id = conf_ev.get("event_id")
        conf_cal_id = conf_ev.get("calendar_id") or Config.get_calendar_id(conf_ev.get("calendar_name", "uncertain"))
        new_ev = trim_info["new_event"]
        clean_conf = trim_info["clean_title"]
        cut_mins = trim_info["cut_mins"]
        new_desc = trim_info["new_boundary_desc"]

        # Step 1: Trim conflicting event
        try:
            if conf_cal_id and conf_event_id:
                google_service_instance.patch_calendar_event_times(
                    calendar_id=conf_cal_id,
                    event_id=conf_event_id,
                    start_iso=trim_info["trimmed_start_iso"],
                    end_iso=trim_info["trimmed_end_iso"]
                )
                if conf_ev.get("task_id"):
                    task_due = (datetime.fromisoformat(trim_info["trimmed_end_iso"]) + timedelta(minutes=5)).isoformat()
                    google_service_instance.update_google_task_due(conf_ev["task_id"], task_due)

                await db_instance.update_event_times(
                    event_id=conf_event_id,
                    start_iso=trim_info["trimmed_start_iso"],
                    end_iso=trim_info["trimmed_end_iso"],
                    status="active"
                )
        except Exception as e:
            logger.error(f"Failed to trim event {conf_event_id}: {e}", exc_info=True)

        # Step 2: Book new event
        try:
            new_cal_id = Config.get_calendar_id(new_ev["target_calendar_name"])
            clean_new_title = new_ev["title"].replace("⏳", "").replace("✅", "").strip()

            cal_event = google_service_instance.create_calendar_event(
                calendar_id=new_cal_id,
                title=clean_new_title,
                start_iso=new_ev["start_iso"],
                end_iso=new_ev["end_iso"],
                metadata={"calendar_name": new_ev["target_calendar_name"], "chat_id": str(chat_id)},
                recurrence=new_ev.get("recurrence"),
                reminder_minutes=new_ev.get("reminder_minutes", 10)
            )
            new_event_id = cal_event.get("id")

            dt_end = datetime.fromisoformat(new_ev["end_iso"])
            task_due = dt_end + timedelta(minutes=5)
            task_id = google_service_instance.create_google_task(
                title=clean_new_title,
                due_iso=task_due.isoformat(),
                notes=f"Linked calendar block in '{new_ev['target_calendar_name']}'"
            )

            await db_instance.add_tracked_event(
                event_id=new_event_id,
                calendar_id=new_cal_id,
                calendar_name=new_ev["target_calendar_name"],
                task_id=task_id,
                title=cal_event.get("summary"),
                start_iso=new_ev["start_iso"],
                end_iso=new_ev["end_iso"],
                chat_id=chat_id,
                status="active"
            )

            new_start_fmt = format_human_datetime(new_ev["start_iso"])
            new_end_fmt = format_human_datetime(new_ev["end_iso"]).split(" at ")[-1]

            success_msg = (
                f"✨ *{cal_event.get('summary')}* has been scheduled!\n"
                f"🕒 {new_start_fmt} – {new_end_fmt}\n\n"
                f"✂️ *{clean_conf}* was cut back by *{cut_mins} min* (now will {new_desc}).\n\n"
                f"Would you like to reschedule *{clean_conf}*?"
            )

            followup_keyboard = [
                [
                    InlineKeyboardButton("🔄 Reschedule Entire Session", callback_data=f"trim_resched_all:{conf_event_id}"),
                ],
                [
                    InlineKeyboardButton(f"⏱️ Reschedule Remaining ({cut_mins}m)", callback_data=f"trim_resched_rem:{conf_event_id}:{cut_mins}"),
                ],
                [
                    InlineKeyboardButton("✅ Keep As Is", callback_data="trim_keep_as_is")
                ]
            ]

            await query.edit_message_text(
                success_msg,
                reply_markup=InlineKeyboardMarkup(followup_keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        except Exception as e:
            logger.error(f"Failed to book event in trim_override: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Failed to book event: {e}")
        return

    # ── Reschedule Remaining Cut Minutes ──────────────────────────────
    if data.startswith("trim_resched_rem:"):
        parts = data.split(":")
        conf_event_id = parts[1]
        cut_mins = int(parts[2])

        event = await db_instance.get_tracked_event(conf_event_id)
        raw_title = event["title"] if event else "Session Remainder"
        clean_title = raw_title.replace("⏳ ", "").replace("⏳", "").replace("✅", "").strip()
        rem_title = f"{clean_title} (Part 2)"
        target_cal_name = event["calendar_name"] if event else "Self Studying"
        target_cal_id = Config.get_calendar_id(target_cal_name)

        active_cal_ids = [
            cid for name, cid in Config.GOOGLE_CALENDAR_IDS.items()
            if name != "Completed Tasks" and cid
        ]

        try:
            next_start_iso, next_end_iso = google_service_instance.find_next_free_slot(
                duration_minutes=cut_mins,
                calendar_ids=active_cal_ids
            )

            cal_event = google_service_instance.create_calendar_event(
                calendar_id=target_cal_id,
                title=rem_title,
                start_iso=next_start_iso,
                end_iso=next_end_iso,
                metadata={"calendar_name": target_cal_name, "chat_id": str(chat_id)}
            )
            new_event_id = cal_event.get("id")

            dt_end = datetime.fromisoformat(next_end_iso)
            task_due = dt_end + timedelta(minutes=5)
            task_id = google_service_instance.create_google_task(
                title=rem_title,
                due_iso=task_due.isoformat(),
                notes=f"Remainder of '{clean_title}'"
            )

            await db_instance.add_tracked_event(
                event_id=new_event_id,
                calendar_id=target_cal_id,
                calendar_name=target_cal_name,
                task_id=task_id,
                title=cal_event.get("summary"),
                start_iso=next_start_iso,
                end_iso=next_end_iso,
                chat_id=chat_id,
                status="active"
            )

            rem_start_fmt = format_human_datetime(next_start_iso)
            rem_end_fmt = format_human_datetime(next_end_iso).split(" at ")[-1]

            await query.edit_message_text(
                f"⏱️ *Remaining Session Booked!*\n\n"
                f"📌 `{cal_event.get('summary')}`\n"
                f"📁 {target_cal_name}\n"
                f"🕒 {rem_start_fmt} – {rem_end_fmt} ({cut_mins}m)\n"
                f"📋 Linked Google Task created.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to book remaining session: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Failed to book remaining session: {e}")
        return

    # ── Keep Trimmed As Is ────────────────────────────────────────────
    if data == "trim_keep_as_is":
        await query.edit_message_text(
            "✅ *All set!* The session remains trimmed and your new schedule is locked in.",
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ── Per-event: Mark Done ──────────────────────────────────────────
    if data.startswith("ev_done:") or data.startswith("status_done:"):
        event_id = data.split(":", 1)[-1]
        res = await complete_tracked_event(event_id)
        if res:
            new_title = res["title"]
            await query.edit_message_text(
                f"🎉 *Well done!*\n\n"
                f"📌 `{new_title}`\n"
                f"📁 Moved to Completed Tasks\n"
                f"📋 Google Task ✅",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard()
            )
        else:
            await query.edit_message_text("⚠️ Could not mark event as completed. Please check configuration.")
        return

    # ── Per-event: Mark Not Done / Reopen ─────────────────────────────
    if data.startswith("ev_undone:"):
        event_id = data.split(":", 1)[-1]
        res = await reopen_tracked_event(event_id)
        if res:
            new_title = res["title"]
            cal_name = res.get("original_calendar_name") or "Personal"
            await query.edit_message_text(
                f"↩️ *Session Reopened!*\n\n"
                f"📌 `{new_title}`\n"
                f"📁 Moved back to `{cal_name}`\n"
                f"📋 Google Task reopened (needs action).",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard()
            )
        else:
            await query.edit_message_text("⚠️ Could not reopen event.")
        return

    # ── Per-event: Extend ─────────────────────────────────────────────
    if data.startswith("ev_extend:") or data.startswith("status_extend:"):
        event_id = data.split(":", 1)[-1]
        keyboard = [
            [
                InlineKeyboardButton("+15 min", callback_data=f"ext_quick:{event_id}:15"),
                InlineKeyboardButton("+30 min", callback_data=f"ext_quick:{event_id}:30"),
                InlineKeyboardButton("+45 min", callback_data=f"ext_quick:{event_id}:45"),
            ],
            [
                InlineKeyboardButton("+60 min", callback_data=f"ext_quick:{event_id}:60"),
                InlineKeyboardButton("+90 min", callback_data=f"ext_quick:{event_id}:90"),
                InlineKeyboardButton("⌨️ Custom", callback_data=f"ext_custom:{event_id}"),
            ]
        ]
        await query.edit_message_text(
            "⏳ *Extend block by how many minutes?*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Quick Extension callback
    if data.startswith("ext_quick:"):
        parts = data.split(":")
        event_id, mins_str = parts[1], parts[2]
        mins = int(mins_str)
        await query.edit_message_text(f"Extending by {mins} minutes…")
        await execute_extension(chat_id, event_id, mins, context)
        return

    # Custom Extension callback
    if data.startswith("ext_custom:"):
        event_id = data.split("ext_custom:")[-1]
        await db_instance.set_pending_dialog(user.id, "await_custom_extension", {"event_id": event_id})
        await query.edit_message_text(
            "⌨️ *Custom Extension:*\n\nReply with the number of minutes (e.g. `20` or `45`).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")
            ]])
        )
        return

    # ── Per-event: Reschedule menu ────────────────────────────────────
    if data.startswith("ev_resched_menu:") or data.startswith("status_resched:") or data.startswith("trim_resched_all:"):
        event_id = data.split(":", 1)[-1]
        event = await db_instance.get_tracked_event(event_id)
        if not event:
            await query.edit_message_text("⚠️ Event not found.")
            return

        title_clean = event["title"].replace("⏳ ", "").replace("⏳", "").strip()

        # Compute quick relative options
        tz = Config.USER_TIMEZONE
        now = datetime.now(tz)
        quick_slots = []
        for label, delta_h, hour in [
            ("Today 3 PM", 0, 15),
            ("Today 6 PM", 0, 18),
            ("Today 8 PM", 0, 20),
            ("Tomorrow 9 AM", 1, 9),
            ("Tomorrow 2 PM", 1, 14),
            ("Tomorrow 6 PM", 1, 18),
        ]:
            slot_dt = (now + timedelta(days=delta_h)).replace(hour=hour, minute=0, second=0, microsecond=0)
            if slot_dt > now + timedelta(minutes=15):
                quick_slots.append((label, slot_dt.isoformat()))

        # Duration of original event
        orig_dur = max(30, int(
            (datetime.fromisoformat(event["end_iso"]) - datetime.fromisoformat(event["start_iso"])).total_seconds() / 60
        ))

        keyboard = []
        row = []
        for label, start_iso in quick_slots[:6]:
            end_iso = (datetime.fromisoformat(start_iso) + timedelta(minutes=orig_dur)).isoformat()
            row.append(InlineKeyboardButton(
                label,
                callback_data=f"resched_slot:{event_id}:{start_iso}:{end_iso}"
            ))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([
            InlineKeyboardButton("⌨️ Type a time", callback_data=f"resched_custom:{event_id}"),
            InlineKeyboardButton("🔄 Next Free Slot", callback_data=f"resched_auto:{event_id}"),
        ])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")])

        await query.edit_message_text(
            f"🔄 *Reschedule:* `{title_clean}`\n\nPick a new time:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Quick reschedule slot selected from menu
    if data.startswith("resched_slot:"):
        # format: resched_slot:{event_id}:{start_iso}:{end_iso}
        parts = data.split(":", 3)
        if len(parts) < 4:
            await query.edit_message_text("⚠️ Invalid reschedule data.")
            return
        _, event_id, new_start_iso, new_end_iso = parts
        await query.edit_message_text("🔄 Rescheduling…")
        await execute_reschedule_to(chat_id, event_id, new_start_iso, new_end_iso, context)
        return

    # Auto-reschedule: find next free slot
    if data.startswith("resched_auto:"):
        event_id = data.split("resched_auto:")[-1]
        await query.edit_message_text("🔍 Finding next available slot…")
        await execute_reschedule(chat_id, event_id, context)
        return

    # Custom reschedule: ask user to type new time
    if data.startswith("resched_custom:"):
        event_id = data.split("resched_custom:")[-1]
        await db_instance.set_pending_dialog(user.id, "await_reschedule_time", {"event_id": event_id})
        await query.edit_message_text(
            "⌨️ *Type the new date and time:*\n\n"
            "Examples:\n• _tomorrow at 3 PM_\n• _Friday at 10 AM_\n• _next Monday 2 PM_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_action")
            ]])
        )
        return

    # ── Per-event: Delete (confirm prompt) ───────────────────────────
    if data.startswith("ev_del_ask:") or data.startswith("delete_ask:"):
        event_id = data.split(":", 1)[-1]
        event = await db_instance.get_tracked_event(event_id)
        if not event:
            await query.edit_message_text("⚠️ Event not found.")
            return
        short_title = event["title"].replace("⏳ ", "").replace("⏳", "").strip()
        start_fmt = format_human_datetime(event["start_iso"])
        await query.edit_message_text(
            f"⚠️ *Confirm Deletion*\n\n"
            f"📌 *{short_title}*\n🕒 {start_fmt}\n\n"
            f"This permanently removes it from Google Calendar.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🗑️ Yes, Delete", callback_data=f"delete_confirm:{event_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="delete_cancel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Confirmed delete
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
            task_id = event.get("task_id")
            if task_id:
                google_service_instance.delete_google_task(task_id)
            await db_instance.update_event_status(event_id, "deleted")
            await query.edit_message_text(
                f"🗑️ *Deleted!*\n\n`{short_title}` removed from Google Calendar.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_menu_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to delete event {event_id}: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Failed to delete: {e}")
        return

    if data == "delete_cancel":
        await query.edit_message_text("✅ Deletion cancelled.", reply_markup=main_menu_keyboard())
        return

    # ── Accountability loop (scheduler.py sends these) ────────────────
    # status_done / status_extend / status_resched are now aliased above via
    # the ev_done / ev_extend / ev_resched_menu handlers — no duplicate code needed.


def register_handlers(application):
    """Registers command, message, and callback handlers to the Telegram application."""
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("past", past_command))
    application.add_handler(CommandHandler("review", past_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delete", delete_command))

    application.add_handler(CallbackQueryHandler(handle_callback_query))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
