from .logger import setup_logging, get_logger
from .config import TELEGRAM_TOKEN
from .bot_app import BotApp
import sys


logger = get_logger(__name__)


def main():
    # Setup logging
    setup_logging()

    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not configured. Exiting.")
        sys.exit(1)

    # Build and run using BotApp
    app = BotApp()
    logger.info("Starting bot (run_forever)...")
    app.run_forever()


if __name__ == "__main__":
    main()