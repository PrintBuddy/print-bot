from typing import Optional, Callable
import asyncio
import logging

from telegram import BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .config import get_config
from .logger import get_logger
from .services import create_services
from .bot_handlers import (
    BotHandlers,
    STOCK_CHOOSE_ITEM,
    STOCK_ADJUST_DELTA,
    EXPENSE_CHOOSE_CATEGORY,
    EXPENSE_AWAIT_AMOUNT,
    EXPENSE_AWAIT_DESCRIPTION,
    EXPENSE_CONFIRM,
    RECHARGE_SEARCH,
    RECHARGE_AWAIT_AMOUNT,
    ADJUST_SEARCH,
    ADJUST_AWAIT_AMOUNT,
)


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
        # Keep the global menu minimal; chat-specific menus are set after /start.
        commands = [
            BotCommand("start", "Start the bot and check your access"),
            BotCommand("myid", "Show your Telegram chat ID"),
            BotCommand("request_recharge", "Request a balance recharge"),
        ]
        try:
            await app.bot.set_my_commands(commands)
        except Exception:
            self.logger.exception("Failed to set bot commands")

    def build(self) -> Application:
        if self._app is not None:
            return self._app

        self.logger.info("Building Telegram Application")

        builder = Application.builder().token(self.token).concurrent_updates(True)
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
        self._app.add_handler(CommandHandler("users", handlers.list_users))
        self._app.add_handler(CommandHandler("user", handlers.get_user_info))
        self._app.add_handler(CommandHandler("request_recharge", handlers.request_recharge))

        # Guided, button-driven flows — one-shot command args still work as
        # a fallback (see each entry point), but the default path is
        # buttons + prompts rather than memorized command syntax.
        stock_conv = ConversationHandler(
            entry_points=[CommandHandler("stock", handlers.stock_entry)],
            states={
                STOCK_CHOOSE_ITEM: [
                    CallbackQueryHandler(handlers.stock_choose_item, pattern="^stock:item:"),
                    CallbackQueryHandler(handlers.stock_cancel, pattern="^stock:cancel$"),
                ],
                STOCK_ADJUST_DELTA: [
                    CallbackQueryHandler(handlers.stock_step, pattern="^stock:step:"),
                    CallbackQueryHandler(handlers.stock_confirm, pattern="^stock:confirm$"),
                    CallbackQueryHandler(handlers.stock_cancel, pattern="^stock:cancel$"),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(handlers.stock_cancel, pattern="^stock:cancel$"),
                CommandHandler("cancel", handlers.cancel_command),
            ],
            conversation_timeout=300,
        )
        expense_conv = ConversationHandler(
            entry_points=[CommandHandler("expense", handlers.expense_entry)],
            states={
                EXPENSE_CHOOSE_CATEGORY: [
                    CallbackQueryHandler(handlers.expense_choose_category, pattern="^expense:cat:"),
                    CallbackQueryHandler(handlers.expense_cancel, pattern="^expense:cancel$"),
                ],
                EXPENSE_AWAIT_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.expense_receive_amount),
                ],
                EXPENSE_AWAIT_DESCRIPTION: [
                    CallbackQueryHandler(handlers.expense_skip_description, pattern="^expense:skip_desc$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.expense_receive_description),
                ],
                EXPENSE_CONFIRM: [
                    CallbackQueryHandler(handlers.expense_confirm, pattern="^expense:confirm$"),
                    CallbackQueryHandler(handlers.expense_cancel, pattern="^expense:cancel$"),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(handlers.expense_cancel, pattern="^expense:cancel$"),
                CommandHandler("cancel", handlers.cancel_command),
            ],
            conversation_timeout=300,
        )
        recharge_conv = ConversationHandler(
            entry_points=[CommandHandler("recharge", handlers.recharge_entry)],
            states={
                RECHARGE_SEARCH: [
                    CallbackQueryHandler(handlers.recharge_choose_user, pattern="^recharge:user:"),
                    CallbackQueryHandler(handlers.recharge_cancel, pattern="^recharge:cancel$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.recharge_search),
                ],
                RECHARGE_AWAIT_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.recharge_receive_amount),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(handlers.recharge_cancel, pattern="^recharge:cancel$"),
                CommandHandler("cancel", handlers.cancel_command),
            ],
            conversation_timeout=300,
        )
        adjust_conv = ConversationHandler(
            entry_points=[CommandHandler("adjust", handlers.adjust_entry)],
            states={
                ADJUST_SEARCH: [
                    CallbackQueryHandler(handlers.adjust_choose_user, pattern="^adjust:user:"),
                    CallbackQueryHandler(handlers.adjust_cancel, pattern="^adjust:cancel$"),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.adjust_search),
                ],
                ADJUST_AWAIT_AMOUNT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.adjust_receive_amount),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(handlers.adjust_cancel, pattern="^adjust:cancel$"),
                CommandHandler("cancel", handlers.cancel_command),
            ],
            conversation_timeout=300,
        )

        self._app.add_handler(stock_conv)
        self._app.add_handler(expense_conv)
        self._app.add_handler(recharge_conv)
        self._app.add_handler(adjust_conv)

        # Restricted to the prefixes it actually handles now that stock:/
        # expense: callbacks are routed by the ConversationHandlers above.
        self._app.add_handler(CallbackQueryHandler(handlers.button_callback, pattern="^(rr|pp):"))

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
