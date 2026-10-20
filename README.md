# ⏳ Kairos — AI Executive Auto-Scheduler

**Kairos** (*καιρός* — the ancient Greek concept of the opportune, perfect moment) is a stateful, asynchronous Python Telegram bot that functions as an intelligent executive assistant for **Google Calendar** and **Google Tasks**.

Powered by OpenRouter LLM tool calling, multi-calendar conflict detection via `freebusy.query`, and an automated **Accountability & Extension Loop** driven by APScheduler.

---

## 🌟 Key Features

- **Natural Language Scheduling**: Parses conversational time-blocking requests (e.g. *"Study algorithms for 2 hours tomorrow at 10 AM"* or *"Prep for Operating Systems exam tonight at 8 for 90 mins"*) using OpenRouter's free-tier LLMs (`google/gemini-2.0-flash-exp:free`).
- **Dynamic Temporal Context**: Dynamically injects the user's exact current date, time, weekday, and local timezone offset into every prompt.
- **Intelligent Calendar Routing**: Classifies your session into one of your 6 active Google Calendars:
  - `Personal`
  - `Assistant work`
  - `Quizzez / FInals`
  - `Self Studying`
  - `Uni Classes`
  - `University Studying`
  - *Ambiguity Fallback*: If the request is unclear, Kairos provides an interactive Telegram inline keyboard for 1-tap calendar selection.
- **Simultaneous Conflict Prevention (`freebusy.query`)**: Queries across **all active calendars simultaneously** before proposing or booking any session. If a clash exists, it automatically detects the earliest conflict-free opening and offers a 1-tap scheduling button.
- **Hybrid Event & Task Engine**:
  - **Calendar Event**: Inserts event with an hourglass badge (e.g., `⏳ Study: Operating Systems`) and records metadata in `extendedProperties`.
  - **Google Task**: Creates a companion task in your default Google Tasks list due **5 minutes post-event**.
- **The Accountability Loop (APScheduler)**:
  - Background loop inspects blocks the moment they conclude.
  - Pings you on Telegram with three actionable choices:
    - **`[✅ Done]`**: Moves event via `events.move` to `"Completed Tasks"`, changes badge from `⏳` to `✅`, and marks the companion Google Task as completed.
    - **`[⏳ Working on it]`**: Prompts for extension minutes (`+15m`, `+30m`, `+45m`, `+60m`, or custom numerical reply), extends the calendar block, and shifts the task due time.
    - **`[🔄 Reschedule]`**: Runs `freebusy.query` to find the next open gap and reschedules both the event and the task.
- **Stateful SQLite Persistence**: All tracked blocks, task links, and conversational states are stored in a lightweight SQLite database (`bot_scheduler.db`) with `aiosqlite`.

---

## 🏗️ Architecture

```mermaid
flowchart TD
    User([Telegram User]) <--> Bot[Kairos Bot / python-telegram-bot]
    Bot <--> LLM[OpenRouter API<br/>Gemini 2.0 Flash]
    Bot <--> DB[(SQLite Database<br/>State & Idempotency)]
    Bot <--> GC[Google Calendar API<br/>freebusy / events.insert / move / patch]
    Bot <--> GT[Google Tasks API<br/>tasks.insert / patch]
    
    subgraph Background Accountability Loop
        APS[APScheduler<br/>AsyncIOScheduler (60s tick)] -->|Scan finished blocks| DB
        APS -->|Dispatch interactive check-in| Bot
    end
```

---

## 📁 Project Structure

```text
kairos/
├── bot_handlers.py          # Telegram commands, natural language & callback handlers
├── config.py                # Environment configuration & calendar mapping
├── database.py              # Asynchronous SQLite storage (aiosqlite)
├── google_service.py        # Calendar & Tasks API client (freebusy, insert, move, patch)
├── llm_service.py           # OpenRouter LLM client with tool calling & dynamic prompt
├── main.py                  # Application entrypoint & worker lifecycle
├── scheduler.py             # APScheduler accountability background job
├── requirements.txt         # Production dependencies
├── Dockerfile               # Container build for 24/7 cloud hosts
├── Procfile                 # Worker process declaration
├── render.yaml              # Render blueprint deployment specification
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
- The entire `secrets/` directory (containing JSON keys, tokens, and credentials) is ignored.
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
OPENROUTER_MODEL=google/gemini-2.0-flash-exp:free
USER_TIMEZONE=Europe/Istanbul

# Path to service account JSON (or raw JSON in GOOGLE_SERVICE_ACCOUNT_JSON for cloud)
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
This automatically detects your OAuth client file, launches the Google authorization window, and writes `GOOGLE_REFRESH_TOKEN` directly into your `.env` file.

### 5. Launch Kairos
```bash
python main.py
```

---

## ☁️ 24/7 Cloud Deployment

### Deploy on Render (Free Background Worker)
1. Push this repository to GitHub (public or private).
2. Go to the [Render Dashboard](https://dashboard.render.com/) > **New +** > **Background Worker**.
3. Connect your GitHub repository.
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
5. In **Environment Variables**, set:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_ALLOWED_USER_IDS`
   - `OPENROUTER_API_KEY`
   - `USER_TIMEZONE`
   - `GOOGLE_CALENDAR_IDS`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: Paste the raw JSON string of your service account key.
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (for Google Tasks).
6. Click **Create Background Worker**. Render will build and run Kairos 24/7 with automatic restarts.

### Deploy on Railway
1. Go to [Railway.app](https://railway.app/) > **New Project** > **Deploy from GitHub repo**.
2. Railway will automatically detect the [`Dockerfile`](file:///home/bl33d/Desktop/Calendar/Dockerfile) and [`railway.toml`](file:///home/bl33d/Desktop/Calendar/railway.toml).
3. Set your environment variables in the Railway dashboard.

---

## 💬 Interacting with Kairos

Send messages to your bot on Telegram:
- *"Schedule 2 hours of self studying for React tomorrow at 2 PM"*
- *"I have an operating systems exam prep session for 90 minutes tonight at 8"*
- *"Block out 1 hour for grading assistant papers on Friday at 11 AM"*
- *"Workout session today at 6 PM for 45 mins"*
- `/status` to view upcoming scheduled sessions.
- When an event concludes, tap **`[✅ Done]`**, **`[⏳ Working on it]`**, or **`[🔄 Reschedule]`** directly on the interactive check-in message!
