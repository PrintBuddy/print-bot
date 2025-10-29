from typing import Optional, Callable
import signal
import asyncio
import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from .config import get_config
from .logger import get_logger
from .services import create_services
from .bot_handlers import BotHandlers


class BotApp:
    """Encapsulates the lifecycle of the Telegram bot application.

    Responsibilities:
    - build Application
    - wire services and handlers
    - run and stop the app
    """

    def __init__(self, token: Optional[str] = None, *, logger_name: str = "bot", post_init: Optional[Callable] = None):
        cfg = get_config()
        self.token = token or cfg.TELEGRAM_TOKEN
        self.logger = get_logger(logger_name)

        logging.getLogger("httpx").setLevel(logging.WARNING)

        self._app: Optional[Application] = None
        self.services = create_services()
        self.post_init = post_init
        # seconds to wait for pending tasks during shutdown
        self.shutdown_timeout = getattr(cfg, "SHUTDOWN_TIMEOUT", 10)

    async def _setup_commands(self, app: Application):
        # default command registration
        commands = [
            BotCommand("start", "Start the bot and check your access"),
            BotCommand("myid", "Show your Telegram chat ID"),
            BotCommand("generate", "Generate a voucher (admins only)"),
            BotCommand("users", "List all users (admins only)"),
            BotCommand("user", "Get user info by username"),
            BotCommand("recharge", "Recharge a user's balance"),
            BotCommand("adjust", "Adjust a user's balance"),
        ]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:
            self.logger.exception("Failed to set bot commands")

    def build(self) -> Application:
        if self._app is not None:
            return self._app

        self.logger.info("Building Telegram Application")

        builder = Application.builder().token(self.token)
        # if a post_init was provided, use it; otherwise use the internal commands setup
        if self.post_init is not None:
            builder = builder.post_init(self.post_init)
        else:
            builder = builder.post_init(self._setup_commands)

        self._app = builder.build()

        # register handlers (BotHandlers expects services dict)
        handlers = BotHandlers(services=self.services, logger=self.logger)

        self._app.add_handler(CommandHandler("start", handlers.start))
        self._app.add_handler(CommandHandler("myid", handlers.myid))
        self._app.add_handler(CommandHandler("generate", handlers.generate))
        self._app.add_handler(CommandHandler("users", handlers.list_users))
        self._app.add_handler(CommandHandler("user", handlers.get_user_info))
        self._app.add_handler(CommandHandler("recharge", handlers.recharge))
        self._app.add_handler(CommandHandler("adjust", handlers.adjust))
        self._app.add_handler(CallbackQueryHandler(handlers.button_callback))

        # Global error handler: handle timeouts specially and log unexpected errors
        async def _global_error_handler(update, context):
            err = context.error
            try:
                if isinstance(err, asyncio.TimeoutError):
                    self.logger.warning("Timeout while handling update %s: %s", update, err)
                    try:
                        if update and getattr(update, "effective_message", None):
                            await update.effective_message.reply_text("\u26a0\ufe0f Request timed out, please try again.")
                    except Exception:
                        self.logger.exception("Failed to notify user about timeout")
                    return

                # generic handler
                self.logger.exception("Unhandled exception in update handler: %s", err)
                try:
                    if update and getattr(update, "effective_message", None):
                        await update.effective_message.reply_text("\u26a0\ufe0f An internal error occurred. The team has been notified.")
                except Exception:
                    self.logger.exception("Failed to notify user about internal error")
            except Exception:
                self.logger.exception("Error in global error handler")

        self._app.add_error_handler(_global_error_handler)

        return self._app

    def run(self):
        app = self.build()
        self.logger.info("Starting bot application")
        app.run_polling()

    def run_forever(self):
        """Run the bot using long-polling.

        Using `Application.run_polling()` is simpler and more robust in
        environments where managing the event loop and signal handlers
        manually can cause the bot to be unresponsive. It also keeps
        behavior identical to `run()` but blocks the current thread until
        shutdown (handling SIGINT/SIGTERM internally).
        """

        app = self.build()
        self.logger.info("Starting bot application (polling)")
        try:
            app.run_polling()
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt received; exiting")

    async def stop(self):
        if self._app is not None:
            self.logger.info("Stopping bot application")
            try:
                await self._app.stop()
            except Exception:
                self.logger.exception("Error while stopping application")

            # wait for other pending tasks (e.g. background network calls) with timeout
            try:
                current = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks() if t is not current]
                if pending:
                    self.logger.info("Waiting for %d pending tasks (timeout=%s)s", len(pending), self.shutdown_timeout)
                    done, pending = await asyncio.wait(pending, timeout=self.shutdown_timeout)
                    if pending:
                        self.logger.warning("%d tasks did not finish before timeout", len(pending))
                        for t in pending:
                            t.cancel()
            except Exception:
                self.logger.exception("Error while waiting for pending tasks during shutdown")
