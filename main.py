import asyncio
import logging
import sys
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

async def post_init(application: Application):
    """Initializes async components after the bot application is built."""
    logger.info("Initializing database...")
    await db_instance.init_db()

    logger.info("Starting accountability scheduler...")
    scheduler_instance.set_bot(application.bot)
    scheduler_instance.start()

    bot_info = await application.bot.get_me()
    logger.info(f"Bot authorized as @{bot_info.username} (ID: {bot_info.id})")

async def post_shutdown(application: Application):
    """Graceful cleanup on shutdown."""
    logger.info("Shutting down accountability scheduler...")
    scheduler_instance.stop()
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
