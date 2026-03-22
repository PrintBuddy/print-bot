from html import escape

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import ContextTypes

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
        self.recharge_request_notifications = {}

    def _admin_commands(self) -> list[BotCommand]:
        return [
            BotCommand("start", "Start the bot and check your access"),
            BotCommand("myid", "Show your Telegram chat ID"),
            BotCommand("generate", "Generate a voucher"),
            BotCommand("users", "List all users"),
            BotCommand("user", "Get user info by username"),
            BotCommand("recharge", "Recharge a user's balance"),
            BotCommand("adjust", "Adjust a user's balance"),
            BotCommand("request_recharge", "Request a balance recharge"),
        ]

    def _user_commands(self) -> list[BotCommand]:
        return [
            BotCommand("start", "Start the bot and check your access"),
            BotCommand("myid", "Show your Telegram chat ID"),
            BotCommand("request_recharge", "Request a balance recharge"),
        ]

    async def _set_chat_commands(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int | None, is_admin: bool):
        if chat_id is None:
            return

        commands = self._admin_commands() if is_admin else self._user_commands()
        try:
            await context.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=chat_id))
        except Exception:
            self.logger.exception("Failed to set chat-specific commands for chat_id=%s", chat_id)

    def _format_telegram_identity(self, user) -> str:
        if user is None:
            return "Unknown Telegram user"

        parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
        name = " ".join(part for part in parts if part).strip()
        username = getattr(user, "username", None)
        if name and username:
            return f"{name} (@{username})"
        if username:
            return f"@{username}"
        if name:
            return name
        return f"chat_id={getattr(user, 'id', 'unknown')}"

    def _escape_html(self, value) -> str:
        return escape(str(value or ""))

    def _build_admin_request_text(self, payload: dict) -> str:
        request = payload.get("request", {})
        user_name = payload.get("user_name", "")
        user_surname = payload.get("user_surname", "")
        telegram_bits = []
        if request.get("requester_first_name") or request.get("requester_last_name"):
            telegram_bits.append(
                " ".join(
                    bit for bit in [request.get("requester_first_name"), request.get("requester_last_name")] if bit
                ).strip()
            )
        if request.get("requester_telegram_username"):
            telegram_bits.append(f"@{request['requester_telegram_username']}")
        telegram_identity = " ".join(bit for bit in telegram_bits if bit).strip() or request.get("requester_chat_id")
        message = request.get("message")

        lines = [
            "🔔 <b>New Recharge Request</b>",
            "",
            f"👤 <b>User:</b> {self._escape_html(user_name)} {self._escape_html(user_surname)} "
            f"(@{self._escape_html(request.get('username'))})",
            f"💶 <b>Amount:</b> {float(request.get('amount', 0)):.2f}€",
            f"💬 <b>Requested via Telegram by:</b> {self._escape_html(telegram_identity)}",
        ]
        if message:
            lines.append(f"📝 <b>Message:</b> {self._escape_html(message)}")

        return "\n".join(lines)

    def _build_request_buttons(self, request_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"rr:approve:{request_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"rr:reject:{request_id}"),
                ]
            ]
        )

    def _build_resolution_text(self, payload: dict) -> str:
        request = payload.get("request", {})
        status = request.get("status")
        resolved_by = request.get("resolved_by_username") or "unknown admin"
        if status == "approved":
            header = "✅ <b>Recharge Request Approved</b>"
            result = "Approved"
        else:
            header = "❌ <b>Recharge Request Rejected</b>"
            result = "Rejected"

        return (
            f"{header}\n\n"
            f"👤 <b>User:</b> {self._escape_html(payload.get('user_name'))} {self._escape_html(payload.get('user_surname'))} "
            f"(@{self._escape_html(request.get('username'))})\n"
            f"💶 <b>Amount:</b> {float(request.get('amount', 0)):.2f}€\n"
            f"📌 <b>Result:</b> {result}\n"
            f"🛠️ <b>Handled by:</b> {self._escape_html(resolved_by)}"
        )

    async def _notify_admins_of_request(self, context: ContextTypes.DEFAULT_TYPE, payload: dict):
        request = payload.get("request", {})
        request_id = request.get("id")
        text = self._build_admin_request_text(payload)
        buttons = self._build_request_buttons(request_id)
        notifications = []

        for admin_chat_id in payload.get("admin_chat_ids", []):
            try:
                sent = await context.bot.send_message(
                    chat_id=int(admin_chat_id),
                    text=text,
                    reply_markup=buttons,
                    parse_mode="HTML",
                )
                notifications.append({"chat_id": int(admin_chat_id), "message_id": sent.message_id})
            except Exception:
                self.logger.exception("Failed to notify admin chat_id=%s about recharge request %s", admin_chat_id, request_id)

        if request_id:
            self.recharge_request_notifications[request_id] = notifications

    async def _mark_request_messages_resolved(self, context: ContextTypes.DEFAULT_TYPE, payload: dict):
        request_id = payload.get("request", {}).get("id")
        if not request_id:
            return

        text = self._build_resolution_text(payload)
        notifications = self.recharge_request_notifications.pop(request_id, [])
        for notification in notifications:
            try:
                await context.bot.edit_message_text(
                    chat_id=notification["chat_id"],
                    message_id=notification["message_id"],
                    text=text,
                    parse_mode="HTML",
                )
            except Exception:
                self.logger.exception(
                    "Failed to update admin notification for recharge request %s in chat_id=%s",
                    request_id,
                    notification["chat_id"],
                )

    async def _notify_requester_of_resolution(self, context: ContextTypes.DEFAULT_TYPE, payload: dict):
        request = payload.get("request", {})
        status = request.get("status")
        if status == "approved":
            text = (
                f"✅ Your recharge request for {float(request.get('amount', 0)):.2f}€ "
                f"on user {request.get('username')} was approved."
            )
        else:
            text = (
                f"❌ Your recharge request for {float(request.get('amount', 0)):.2f}€ "
                f"on user {request.get('username')} was rejected."
            )

        try:
            await context.bot.send_message(chat_id=int(request.get("requester_chat_id")), text=text)
        except Exception:
            self.logger.exception("Failed to notify requester for recharge request %s", request.get("id"))

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
            await self._set_chat_commands(context, chat_id, is_admin=True)
            name = user_res.get("name") or "Admin"
            # HTML-formatted top part (no angle-bracket placeholders here)
            msg_html = (
                f"🖨️👋 <b>Welcome back, {name}!</b>\n\n"
                "This is your PrintBuddy admin assistant for managing user balances and vouchers directly from Telegram.\n\n"
                "✨ <b>Available commands</b>\n\n"
                "🆔 <b>General</b>\n"
                "/myid – Show your Telegram chat ID\n\n"

                "👥 <b>Users</b>\n"
                "/users – List all users\n"
                "/user &lt;username&gt; – Show user info\n\n"
                
                "💳 <b>Balance & Vouchers</b>\n"
                "/generate &lt;amount&gt; – Generate a voucher\n"
                "/recharge &lt;username&gt; &lt;amount&gt; – Add credit to a user\n"
                "/adjust &lt;username&gt; &lt;new_balance&gt; – Set a user balance manually\n\n"

                "📩 <b>Recharge Requests</b>\n"
                "Users can send recharge requests here, and you can approve or reject them directly from the bot."
            )

        elif status_code == 403:
            await self._set_chat_commands(context, chat_id, is_admin=False)
            msg = (
                "🖨️👋 Welcome to PrintBuddy!\n\n"
                "💳 This bot helps you request a balance recharge for your printing account.\n"
                "📩 Your request will be sent directly to the admins on Telegram for review.\n\n"
                "✨ Available commands:\n"
                "/myid - Show your Telegram chat ID 🆔\n"
                "/request_recharge <username> <amount> [message] - Ask the admins for a recharge 💶"
            )
            keyboard = None
        else:
            await self._set_chat_commands(context, chat_id, is_admin=False)
            msg = (
                f"⚠️ Error: {user_res.get('detail', 'Unknown error')}\n\n"
                "You can still use /myid and /request_recharge <username> <amount>."
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

        msg = getattr(query, "message", None)
        chat_id = getattr(getattr(msg, "chat", None), "id", None)

        data = getattr(query, "data", None)
        if data and data.startswith("rr:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                if msg is not None:
                    await msg.reply_text("❌ Invalid request action.")
                return

            action = parts[1]
            request_id = parts[2]
            status_code, res = self.user_service.resolve_recharge_request(chat_id, request_id, action)

            if status_code == 200:
                await self._mark_request_messages_resolved(context, res)
                await self._notify_requester_of_resolution(context, res)
                try:
                    await query.answer(f"Request {action}d", show_alert=False)
                except Exception:
                    pass
            elif status_code == 403:
                try:
                    await query.answer("You are not authorized to do that.", show_alert=True)
                except Exception:
                    pass
            elif status_code == 404:
                try:
                    await query.answer("Recharge request not found.", show_alert=True)
                except Exception:
                    pass
            elif status_code == 409:
                detail = res.get("detail", "Request already resolved.")
                resolved_by = res.get("resolved_by_username")
                if resolved_by:
                    detail = f"{detail} By {resolved_by}."
                try:
                    await query.answer(detail, show_alert=True)
                except Exception:
                    pass
            else:
                try:
                    await query.answer(res.get("detail", "Unknown error"), show_alert=True)
                except Exception:
                    pass
            return

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

            msg_lines = []
            for u in res:
                msg_lines.append(f"{u.get('name')} {u.get('surname')} ({u.get('username')})")
            message = getattr(update, "message", None)
            if message is not None:
                msg_lines.sort(key=lambda x: x.lower())
                msg_lines.insert(0, "👥 Users:\n")
                await message.reply_text("\n".join(msg_lines))
        elif status_code == 403:
            await update.message.reply_text("❌ You are not authorized to view users.")
        else:
            await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")

    @safe_handler
    async def request_recharge(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        if len(args) < 2:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text("Usage: /request_recharge <username> <amount> [message]")
            return

        username = args[0]
        request_message = " ".join(args[2:]).strip() or None
        telegram_user = getattr(update, "effective_user", None)
        status_code, res = self.user_service.request_recharge(
            chat_id,
            username,
            args[1],
            message=request_message,
            telegram_username=getattr(telegram_user, "username", None),
            telegram_first_name=getattr(telegram_user, "first_name", None),
            telegram_last_name=getattr(telegram_user, "last_name", None),
        )

        if status_code == 201:
            await self._notify_admins_of_request(context, res)
            requester_identity = self._format_telegram_identity(telegram_user)
            confirmation = (
                f"✅ Recharge request sent for {username} with {float(args[1]):.2f}€.\n"
                f"Admins have been notified.\n"
                f"Sent as: {requester_identity}"
            )
            if request_message:
                confirmation += f"\nMessage: {request_message}"
            await update.message.reply_text(
                confirmation
            )
        elif status_code == 400:
            await update.message.reply_text(f"❌ {res.get('detail', 'Invalid amount')}")
        elif status_code == 404:
            await update.message.reply_text("⚠️ User not found.")
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
