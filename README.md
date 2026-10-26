# ⏳ Kairos — AI Executive Auto-Scheduler

**Kairos** (*καιρός* — the ancient Greek concept of the opportune, perfect moment) is a stateful, asynchronous Python Telegram bot that functions as an intelligent executive assistant for **Google Calendar** and **Google Tasks**.

Powered by OpenRouter LLM tool calling, multi-calendar conflict detection via `freebusy.query`, button-first interactive workflows, and automated **Accountability & Pre-Event Reminders** driven by APScheduler.

---

## 🌟 Key Features

### 🗣️ Natural Language & Smart Tool Calling
- **Conversational Scheduling**: Natural language time-blocking (e.g. *"Study algorithms for 2 hours tomorrow at 10 AM"* or *"Daily gym session at 7 AM for 45 mins"*).
- **True In-Place Rescheduling**: Moving an event patches the existing Google Calendar event and companion task in-place — never creating duplicates.
- **Natural & Button-Driven Deletion**: Say *"delete my algorithms session"* or use `/delete` for a numbered interactive list with two-step confirmation.
- **Dynamic Temporal Context**: Injects current timestamp, weekday, and local timezone offset into every request for accurate relative date calculation.
- **OpenRouter Model Fallback Array**: Supports prioritized fallback model chains (e.g. `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-31b-it:free`, `openrouter/free`).

### 📁 Intelligent Calendar Routing
Classifies your sessions into your designated Google Calendars:
- `Personal` — errands, workouts, medical, leisure
- `Assistant work` — grading, office hours, TA duties
- `Quizzez / FInals` — exams, quizzes, review sessions
- `Self Studying` — independent projects, tech reading
- `Uni Classes` — lectures, scheduled university labs
- `University Studying` — homework, problem sets, assignments
- *Interactive Fallback*: If intent is ambiguous, Kairos displays 1-tap inline buttons to choose the target calendar.

### 🔁 Recurring Events Support
- Schedule recurring blocks naturally:
  - *"Schedule a daily morning run at 7 AM for 30 minutes"*
  - *"Every weekday 1 hour of self study at 9 PM"*
  - *"Weekly team meeting on Thursdays at 3 PM"*
- Automatically converted to standard Google Calendar **RRULE** definitions (`FREQ=DAILY`, `BYDAY=MO,TU,WE,TH,FR`, etc.).

### ✂️ Smart Conflict Resolution & Trim / Override
- **Simultaneous Conflict Prevention (`freebusy.query`)**: Scans across **all active calendars simultaneously** before proposing or booking.
- **Earliest Opening Suggestion**: Automatically calculates the next available gap during active waking hours (08:00 – 23:00).
- **Trim & Override Option**: When an event collides with an existing scheduled session, Kairos detects the overlap and offers a 3rd option:
  - `[📅 Book This Slot]` *(moves the new event to the earliest free opening)*
  - `[✂️ Trim '<Existing Event>' & Book]` *(cuts back the overlapping block to fit the new session)*
  - `[❌ Dismiss]`
- **Confirmation & Post-Trim Recovery**:
  - Displays exact impact: e.g. *“Computer Architecture will be cut back by 18 min and will end at 18:00.”*
  - After booking, offers 1-tap recovery buttons:
    - `[🔄 Reschedule Entire Session]`
    - `[⏱️ Reschedule Remaining (18m)]` *(automatically schedules Part 2 in the next free gap)*
    - `[✅ Keep As Is]`

### 🔔 Dual Reminder System (Calendar Popups + Telegram Alerts)
- **Google Calendar Event Popups**: Custom reminder overrides (`method: popup`, default 10m or user-specified e.g. *"remind me 15 mins before"*) are set directly on the calendar event. Triggers native push notifications on Android, iOS, Chrome, and desktop calendar apps.
- **Telegram Pre-Event Alerts**: Background loop checks for sessions starting in ~10 minutes and sends an interactive Telegram alert with `[🔄 Reschedule]` and `[🗑️ Delete]` buttons.

### 🎛️ Button-First Telegram UI
- **Action Buttons on Every Reply**: Never leaves you guessing what to type next.
- **Interactive `/status` Dashboard**: Displays upcoming blocks with per-event action rows:
  - `[✅ Done]` `[⏳+ Extend]` `[🔄 Reschedule]` `[🗑️ Delete]`
- **Interactive Time Grids**: One-tap reschedule options (`Today 3 PM`, `Tomorrow 9 AM`, `Next Free Slot`, or custom time input).
- **Time Picker**: Suggested time grid when scheduling an activity without specifying a time.

### ⏳ The Accountability Loop (APScheduler)
- Background loop inspects blocks the moment they conclude.
- Pings you on Telegram with three actionable choices:
  - **`[✅ Done]`**: Moves event via `events.move` to `"Completed Tasks"`, changes badge from `⏳` to `✅`, and marks the companion Google Task as completed.
  - **`[⏳ Working on it]`**: Prompts for extension minutes (`+15m`, `+30m`, `+45m`, `+60m`, `+90m`, or custom numerical reply), extends the calendar block, and shifts the task due time.
  - **`[🔄 Reschedule]`**: Finds the next open gap and reschedules both event and task.

### 📋 Companion Google Tasks Integration
- For personal `@gmail.com` accounts, links Google Tasks using OAuth2 refresh tokens.
- Creates companion task due **5 minutes post-session** and automatically marks it completed or updates its due time when events are extended or rescheduled.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    User([Telegram User]) <--> Bot[Kairos Bot / python-telegram-bot]
    Bot <--> LLM[OpenRouter API<br/>Tool Calling + Fallback Chain]
    Bot <--> DB[(SQLite Database<br/>State, Dialogs & Events)]
    Bot <--> GC[Google Calendar API<br/>freebusy / events insert, patch, move, delete]
    Bot <--> GT[Google Tasks API<br/>tasks insert, patch, delete]
    
    subgraph Background Accountability & Reminder Loops
        APS[APScheduler<br/>AsyncIOScheduler (60s tick)] -->|Scan finished blocks| CheckOut[Accountability Check-In]
        APS -->|Scan blocks starting in ~10m| PreAlert[Pre-Event Reminder Alert]
        CheckOut --> Bot
        PreAlert --> Bot
    end
```

---

## 📁 Project Structure

```text
kairos/
├── bot_handlers.py          # Telegram commands, NLP routing, button callbacks & trim flows
├── config.py                # Environment configuration, base64 key parsing & calendar mapping
├── database.py              # Asynchronous SQLite storage (aiosqlite) with automated migrations
├── google_service.py        # Calendar & Tasks API client (freebusy, insert, move, patch, delete)
├── llm_service.py           # OpenRouter LLM client with multi-tool calling & dynamic prompt
├── main.py                  # Application entrypoint, aiohttp health server & conflict handler
├── scheduler.py             # APScheduler background accountability & reminder jobs
├── requirements.txt         # Production dependencies
├── Dockerfile               # Container build for 24/7 cloud hosts
├── Procfile                 # Process declaration for Render / Heroku
├── render.yaml              # Render blueprint specification (Web Service with /health)
├── railway.toml             # Railway deployment configuration
├── .gitignore               # Strict gitignore ensuring zero secret leaks
├── .env.example             # Clean environment variables template
└── scripts/
    └── get_tasks_token.py   # OAuth2 helper script for Google Tasks authorization
```

---

## 🔒 Security & Git Hygiene

This repository is pre-configured with a strict `.gitignore` that prevents sensitive data from ever reaching GitHub:
- `.env` and `.env.*` are ignored.
- The entire `secrets/` directory (containing JSON service account keys, tokens, and credentials) is ignored.
- SQLite databases (`*.db`, `*.sqlite`) are ignored.
- Virtual environments (`venv/`) and Python bytecode are ignored.

Only the generic configuration template [`.env.example`](file:///home/bl33d/Desktop/Calendar/.env.example) is committed.

---

## 🚀 Quickstart & Local Setup

### 1. Clone & Enter Directory
```bash
git clone <your-repo-url>
cd Calendar
```

### 2. Set Up Virtual Environment & Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Secrets
Create your `.env` file from the example template:
```bash
cp .env.example .env
```
Fill in your secrets:
```ini
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
TELEGRAM_ALLOWED_USER_IDS=your_numeric_telegram_user_id
OPENROUTER_API_KEY=your_openrouter_api_key
OPENROUTER_MODELS=nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free,openrouter/free
USER_TIMEZONE=Europe/Istanbul

# Path to service account JSON (for local run) or base64 JSON string in GOOGLE_SERVICE_ACCOUNT_JSON (for cloud)
GOOGLE_SERVICE_ACCOUNT_PATH=secrets/your_service_account.json

# OAuth2 credentials (generated via python scripts/get_tasks_token.py)
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REFRESH_TOKEN=your_refresh_token

# Google Calendar Routing Dictionary (JSON)
GOOGLE_CALENDAR_IDS={"Personal":"primary","Assistant work":"id@group.calendar.google.com",...}
```

### 4. Authorize Google Tasks (Personal Gmail Accounts)
Run the automated OAuth helper:
```bash
source venv/bin/activate
python scripts/get_tasks_token.py
```
This detects your OAuth client file, launches the Google authorization window, and writes `GOOGLE_REFRESH_TOKEN` directly into your `.env` file.

### 5. Launch Kairos
```bash
python main.py
```

---

## ☁️ 24/7 Cloud Deployment (Render)

Deploy as a **Web Service** on Render:

1. Push this repository to GitHub.
2. Go to the [Render Dashboard](https://dashboard.render.com/) > **New +** > **Web Service**.
3. Connect your repository.
4. Settings:
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
5. In **Environment Variables**, set:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_ALLOWED_USER_IDS`
   - `OPENROUTER_API_KEY`
   - `OPENROUTER_MODELS`
   - `USER_TIMEZONE`
   - `GOOGLE_CALENDAR_IDS`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: Base64-encoded or raw JSON string of your Google Service Account key.
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (for Google Tasks).
6. Kairos runs an internal aiohttp server on `$PORT` serving `/health` to keep Render happy.
7. *(Optional)* Set up an [UptimeRobot](https://uptimerobot.com) monitor to ping `https://<your-app>.onrender.com/health` every 10 minutes to prevent free-tier spin-down.

---

## 💬 Commands & Interactions

| Command / Trigger | Action |
|---|---|
| `/start` | Welcome card with persistent main-menu keyboard (`[📅 Schedule] [📋 My Events] [🗑️ Delete] [❓ Help]`) |
| `/status` | Live connection diagnostics + numbered session dashboard with per-event inline buttons (`✅ ⏳+ 🔄 🗑️`) |
| `/delete` | Lists active sessions with 1-tap delete buttons and confirmation safeguard |
| `/cancel` | Aborts any active pending dialog (custom extension, reschedule time, etc.) |
| `/help` | Detailed feature cheatsheet and syntax examples |

### Natural Language Examples:
- *"Study algorithms tomorrow at 2 PM for 2 hours"*
- *"Assistant math office hour on Thursday at 3 PM for 30 mins and remind me 10 mins before"*
- *"Schedule daily morning workout at 7 AM for 45 minutes"*
- *"Reschedule my algorithms session to Friday at 4 PM"*
- *"Delete my database study session"*
