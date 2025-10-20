from telegram import BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler
from .config import TELEGRAM_TOKEN
from .handlers import (
    start, myid, generate, list_users, button_callback,
    get_user_info, recharge, adjust
)


async def setup_commands(app):
    commands = [
        BotCommand("start", "Start the bot and check your access"),
        BotCommand("myid", "Show your Telegram chat ID"),
        BotCommand("generate", "Generate a voucher (admins only)"),
        BotCommand("users", "List all users (admins only)"),
        BotCommand("user", "Get user info by username"),
        BotCommand("recharge", "Recharge a user's balance"),
        BotCommand("adjust", "Adjust a user's balance"),
    ]
    await app.bot.set_my_commands(commands)


def main():
    # Define Application with post_init
    app = ApplicationBuilder() \
        .token(TELEGRAM_TOKEN) \
        .post_init(setup_commands) \
        .build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("users", list_users))
    app.add_handler(CommandHandler("user", get_user_info))
    app.add_handler(CommandHandler("recharge", recharge))
    app.add_handler(CommandHandler("adjust", adjust))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()