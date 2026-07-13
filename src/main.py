from .logger import setup_logging, get_logger
from .config import get_config
from .bot_app import BotApp
import sys


logger = get_logger(__name__)


def main():
    # Setup logging
    setup_logging()

    try:
        get_config().validate()
    except ValueError as e:
        logger.error(f"Invalid configuration: {e}. Exiting.")
        sys.exit(1)

    # Build and run using BotApp
    app = BotApp()
    logger.info("Starting bot (run_forever)...")
    app.run_forever()


if __name__ == "__main__":
    main()