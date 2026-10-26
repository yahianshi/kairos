import os
import asyncio
import logging
import sys
from aiohttp import web
from telegram import Update
from telegram.error import Conflict, NetworkError
from telegram.ext import ApplicationBuilder, Application, ContextTypes

from config import Config
from database import db_instance
from scheduler import scheduler_instance
from bot_handlers import register_handlers

# Configure structured logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Kairos")

async def health_check(request):
    """Health check endpoint for Render/Railway free-tier monitoring."""
    return web.Response(
        text="⏳ Kairos — AI Executive Auto-Scheduler is online and running 24/7!\nStatus: Healthy ✅",
        content_type="text/plain"
    )

async def start_health_server():
    """Starts an HTTP server to satisfy Render/Railway port binding checks."""
    port_str = os.getenv("PORT", "8080")
    try:
        port = int(port_str)
    except ValueError:
        port = 8080

    web_app = web.Application()
    web_app.router.add_get("/", health_check)
    web_app.router.add_get("/health", health_check)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check HTTP server listening on port {port}")
    return runner

async def keep_alive_ping_loop():
    """
    Periodic self-ping loop to prevent Render Free tier from spinning down.
    Render automatically populates RENDER_EXTERNAL_URL (e.g. https://kairos.onrender.com).
    """
    external_url = os.getenv("RENDER_EXTERNAL_URL")
    if not external_url:
        logger.info("RENDER_EXTERNAL_URL not set; skipping automated self-ping loop.")
        return

    health_url = f"{external_url.rstrip('/')}/health"
    logger.info(f"Keep-alive self-ping loop active for {health_url}")

    # Wait 3 minutes after launch before starting recurring pings
    await asyncio.sleep(180)

    import aiohttp
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(health_url, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                    logger.info(f"Keep-alive ping to {health_url} status: {resp.status}")
        except Exception as e:
            logger.warning(f"Keep-alive self-ping error: {e}")
        # Ping every 8 minutes (Render sleeps after 15 minutes of inactivity)
        await asyncio.sleep(480)

async def post_init(application: Application):
    """Initializes async components after the bot application is built."""
    logger.info("Initializing database...")
    await db_instance.init_db()

    logger.info("Starting accountability scheduler...")
    scheduler_instance.set_bot(application.bot)
    scheduler_instance.start()

    logger.info("Starting health check HTTP server...")
    web_runner = await start_health_server()
    application.bot_data["web_runner"] = web_runner

    # Start automated self-ping keep-alive
    ping_task = asyncio.create_task(keep_alive_ping_loop())
    application.bot_data["ping_task"] = ping_task

    bot_info = await application.bot.get_me()
    logger.info(f"Bot authorized as @{bot_info.username} (ID: {bot_info.id})")

async def post_shutdown(application: Application):
    """Graceful cleanup on shutdown."""
    logger.info("Shutting down accountability scheduler...")
    scheduler_instance.stop()

    ping_task = application.bot_data.get("ping_task")
    if ping_task:
        ping_task.cancel()

    web_runner = application.bot_data.get("web_runner")
    if web_runner:
        logger.info("Stopping health check HTTP server...")
        await web_runner.cleanup()

    logger.info("Application shutdown complete.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — suppresses 409 Conflict during rolling deploys."""
    err = context.error
    if isinstance(err, Conflict):
        logger.warning(
            "409 Conflict: another bot instance is polling. "
            "This is normal during Render rolling deploys and will resolve in ~30s."
        )
        return
    if isinstance(err, NetworkError):
        logger.warning(f"Network error (will auto-retry): {err}")
        return
    logger.error(f"Unhandled exception: {err}", exc_info=err)


def main():
    logger.info("Starting Kairos — AI Auto-Scheduler Telegram Bot...")

    # Validate configuration
    config_warnings = Config.validate()
    if config_warnings:
        logger.warning("Configuration alerts:")
        for w in config_warnings:
            logger.warning(f"  • {w}")

    if not Config.TELEGRAM_BOT_TOKEN:
        logger.critical("TELEGRAM_BOT_TOKEN is missing! Please configure .env file.")
        sys.exit(1)

    # Build Application
    app = (
        ApplicationBuilder()
        .token(Config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register all handlers
    register_handlers(app)
    app.add_error_handler(error_handler)

    logger.info("Bot is starting polling. Ready to receive commands...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
