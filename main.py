import os
import asyncio
import logging
import sys
from aiohttp import web
from telegram.ext import ApplicationBuilder, Application

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

    bot_info = await application.bot.get_me()
    logger.info(f"Bot authorized as @{bot_info.username} (ID: {bot_info.id})")

async def post_shutdown(application: Application):
    """Graceful cleanup on shutdown."""
    logger.info("Shutting down accountability scheduler...")
    scheduler_instance.stop()

    web_runner = application.bot_data.get("web_runner")
    if web_runner:
        logger.info("Stopping health check HTTP server...")
        await web_runner.cleanup()

    logger.info("Application shutdown complete.")

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

    logger.info("Bot is starting polling. Ready to receive commands...")
    app.run_polling()

if __name__ == "__main__":
    main()
