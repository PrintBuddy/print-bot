from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes
import functools

from .services import create_services
from .logger import LOGGER_MANAGER
from .config import get_config
from .utilities import safe_handler


class BotHandlers:
    def __init__(self, services=None, logger=None):
        self.services = services or create_services()
        self.voucher_service = self.services["voucher"]
        self.user_service = self.services["user"]
        self.logger = logger or LOGGER_MANAGER.get_logger(self.__class__.__name__)
        self.cfg = get_config()

    @safe_handler
    async def start(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        status_code, user_res = self.user_service.get_me(chat_id)

        buttons = [
            [InlineKeyboardButton("Generate 1€ voucher", callback_data="gen_1")],
            [InlineKeyboardButton("Generate 2€ voucher", callback_data="gen_2")],
            [InlineKeyboardButton("Generate 5€ voucher", callback_data="gen_5")],
            [InlineKeyboardButton("Generate 10€ voucher", callback_data="gen_10")]
        ]
        keyboard = InlineKeyboardMarkup(buttons)

        msg = None
        msg_html = None
        usage_text = None

        if status_code == 200:
            name = user_res.get("name") or "Admin"
            # HTML-formatted top part (no angle-bracket placeholders here)
            msg_html = (
                f"✅ <b>Welcome {name}!</b>\n\n"
                "Here are your available commands:\n\n"
                "ℹ️ <b>General:</b>\n"
                "/myid – Show your Telegram chat ID\n\n"

                "📘 <b>User Commands:</b>\n"
                "/users – List all users\n"
                "/user &lt;username&gt; – Show user info\n\n"
                
                "💰 <b>Voucher & Balance:</b>\n"
                "/generate &lt;amount&gt; – Generate a voucher\n"
                "/recharge &lt;username&gt; &lt;amount&gt; – Add credit to a user\n"
                "/adjust &lt;username&gt; &lt;new_balance&gt; – Set user balance manually\n"
            )

        elif status_code == 403:
            msg = (
                "❌ You are not an authorized admin.\n\n"
                "You can still use the following command:\n"
                "/myid – Show your Telegram chat ID"
            )
            keyboard = None
        else:
            msg = (
                f"⚠️ Error: {user_res.get('detail', 'Unknown error')}\n\n"
                "You can still use /myid to get your chat ID."
            )
            keyboard = None

        message = getattr(update, "message", None)
        if message is not None:
            if status_code == 200:
                # send formatted header with keyboard
                await message.reply_text(msg_html, reply_markup=keyboard, parse_mode="HTML")
            else:
                # other branches have plain text messages; no HTML parsing needed
                await message.reply_text(msg, reply_markup=keyboard)

    @safe_handler
    async def generate(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        if len(args) != 1:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("Usage: /generate <amount>")
            return

        status_code, res = self.voucher_service.generate(chat_id, args[0])

        if status_code == 200:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(f"✅ Voucher generated: {res.get('code')}")
        elif status_code == 400:
            await update.message.reply_text("❌ Amount must be a positive number")
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to generate vouchers")
        else:
            await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def myid(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        message = getattr(update, "message", None)
        if message is not None:
            await message.reply_text(f"Your Telegram chat ID is: {chat_id}")

    @safe_handler
    async def button_callback(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return
        try:
            await query.answer()
        except Exception:
            # ignore answer failures
            pass

        msg = getattr(query, "message", None)
        chat_id = getattr(getattr(msg, "chat", None), "id", None)

        data = getattr(query, "data", None)
        try:
            amount = float(data.split("_")[1]) if data else None
        except Exception:
            if msg is not None:
                await msg.reply_text("❌ Invalid button data")
            return

        status_code, res = self.voucher_service.generate(chat_id, amount)
        if status_code == 200:
            if msg is not None:
                await msg.reply_text(f"✅ Voucher generated: {res.get('code')}")
        elif status_code == 403:
            if msg is not None:
                await msg.reply_text("❌ You are not authorized to generate vouchers")
        else:
            if msg is not None:
                await msg.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def list_users(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        status_code, res = self.user_service.list_users(chat_id)

        if status_code == 200:
            if not res:
                message = getattr(update, "message", None)
                if message is not None:
                    await message.reply_text("No users found.")
                return

            msg_lines = ["📋 Users list:"]
            for u in res:
                msg_lines.append(f"{u.get('name')} {u.get('surname')} ({u.get('username')})")
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("\n".join(msg_lines))
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to view users.")
        else:
            await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def get_user_info(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        if len(args) != 1:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("Usage: /user <username>")
            return

        username = args[0]
        status_code, res = self.user_service.get_user(chat_id, username)

        if status_code == 200:
            msg = (
                f"👤 User Info\n"
                f"Name: {res.get('name')} {res.get('surname')}\n"
                f"Username: {res.get('username')}\n"
                f"Balance: {res.get('balance'):.2f}€"
            )
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(msg)
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to view users.")
        elif status_code == 404:
            await update.message.reply_text("⚠️ User not found.")
        else:
            await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def recharge(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        if len(args) != 2:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("Usage: /recharge <username> <amount>")
            return

        username = args[0]
        status_code, res = self.user_service.recharge(chat_id, username, args[1])

        if status_code == 200:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(f"💰 Successfully recharged {username} with {float(args[1]):.2f}€")
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to recharge users.")
        elif status_code == 404:
            await update.message.reply_text("⚠️ User not found.")
        else:
            await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def adjust(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        if len(args) != 2:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("Usage: /adjust <username> <new_balance>")
            return

        username = args[0]
        status_code, res = self.user_service.adjust(chat_id, username, args[1])

        if status_code == 200:
            name = res.get("name") or username
            balance = res.get("balance")
            await update.message.reply_text(f"🧾 Adjusted {name}'s balance to {balance:.2f}€")
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to adjust balances.")
        elif status_code == 404:
            await update.message.reply_text("⚠️ User not found.")
        else:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")
