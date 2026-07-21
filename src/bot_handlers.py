from html import escape

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

from .services import create_services, validate_expense_input, EXPENSE_CATEGORIES
from .logger import LOGGER_MANAGER
from .config import get_config
from .utilities import safe_handler


# Conversation states — two independent ConversationHandlers (wired in
# bot_app.py), so overlapping int values across them are harmless.
STOCK_CHOOSE_ITEM, STOCK_ADJUST_DELTA = range(2)
EXPENSE_CHOOSE_CATEGORY, EXPENSE_AWAIT_AMOUNT, EXPENSE_AWAIT_DESCRIPTION, EXPENSE_CONFIRM = range(4)
RECHARGE_SEARCH, RECHARGE_AWAIT_AMOUNT = range(2)
ADJUST_SEARCH, ADJUST_AWAIT_AMOUNT = range(2)

EXPENSE_CATEGORY_LABELS = {
    "toner": "🖨️ Toner",
    "paper": "📄 Paper",
    "maintenance": "🔧 Maintenance",
    "other": "📦 Other",
}


class BotHandlers:
    def __init__(self, services=None, logger=None):
        self.services = services or create_services()
        self.user_service = self.services["user"]
        self.logger = logger or LOGGER_MANAGER.get_logger(self.__class__.__name__)
        self.cfg = get_config()
        self.recharge_request_notifications = {}

    def _admin_commands(self) -> list[BotCommand]:
        return [
            BotCommand("start", "Start the bot and check your access"),
            BotCommand("myid", "Show your Telegram chat ID"),
            BotCommand("users", "List all users"),
            BotCommand("user", "Get user info by username"),
            BotCommand("recharge", "Search a user, then recharge their balance"),
            BotCommand("adjust", "Search a user, then set their balance"),
            BotCommand("request_recharge", "Request a balance recharge"),
            BotCommand("stock", "View or adjust inventory stock"),
            BotCommand("expense", "Log an expense (guided, asks to confirm)"),
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
        request = payload.get("request", {})
        request_id = request.get("id")
        if not request_id:
            return

        text = self._build_resolution_text(payload)
        notifications = self.recharge_request_notifications.pop(request_id, [])

        # Requests created from the web app were never announced by this
        # bot process (the backend messaged the target admin directly), so
        # they're never in the in-memory map above — but the backend
        # persists the one message it sent on the request row itself,
        # which survives a bot restart unlike the in-memory map.
        if not notifications and request.get("notified_chat_id") and request.get("notified_message_id"):
            notifications = [{
                "chat_id": int(request["notified_chat_id"]),
                "message_id": request["notified_message_id"],
            }]

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

    def _build_purchase_resolution_text(self, payload: dict) -> str:
        purchase = payload.get("purchase", {})
        status = purchase.get("status")
        resolved_by = purchase.get("resolved_by_username") or "unknown admin"
        if status == "fulfilled":
            header = "✅ <b>Purchase Given</b>"
        else:
            header = "❌ <b>Purchase Rejected & Refunded</b>"

        lines = [
            header,
            "",
            f"👤 <b>User:</b> {self._escape_html(purchase.get('username'))}",
            f"📦 <b>Item:</b> {purchase.get('quantity')}x {self._escape_html(purchase.get('product_name'))}",
            f"💶 <b>Total:</b> {float(purchase.get('total_amount', 0)):.2f}€",
            f"🛠️ <b>Handled by:</b> {self._escape_html(resolved_by)}",
        ]
        if purchase.get("admin_message"):
            lines.append(f"📝 <b>Note:</b> {self._escape_html(purchase['admin_message'])}")
        return "\n".join(lines)

    async def _edit_purchase_notifications(self, context: ContextTypes.DEFAULT_TYPE, payload: dict):
        """Every admin was notified individually about this purchase (it's
        broadcast to all of them, unlike a recharge request's single
        target admin), so resolving it has to edit every one of those
        messages, not just one."""
        text = self._build_purchase_resolution_text(payload)
        for notification in payload.get("notifications", []):
            try:
                await context.bot.edit_message_text(
                    chat_id=int(notification["chat_id"]),
                    message_id=notification["message_id"],
                    text=text,
                    parse_mode="HTML",
                )
            except Exception:
                self.logger.exception(
                    "Failed to update admin notification for purchase %s in chat_id=%s",
                    payload.get("purchase", {}).get("id"),
                    notification.get("chat_id"),
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
        status_code, user_res = await self.user_service.get_me(chat_id)

        keyboard = None
        msg = None
        msg_html = None

        if status_code == 200:
            await self._set_chat_commands(context, chat_id, is_admin=True)
            name = user_res.get("name") or "Admin"
            # HTML-formatted top part (no angle-bracket placeholders here)
            msg_html = (
                f"🖨️👋 <b>Welcome back, {name}!</b>\n\n"
                "This is your PrintBuddy admin assistant for managing user balances directly from Telegram.\n\n"
                "✨ <b>Available commands</b>\n\n"
                "🆔 <b>General</b>\n"
                "/myid – Show your Telegram chat ID\n\n"

                "👥 <b>Users</b>\n"
                "/users – List all users\n"
                "/user &lt;username&gt; – Show user info\n\n"

                "💳 <b>Balance</b>\n"
                "/recharge – Search for a user, then enter an amount to add\n"
                "/adjust – Search for a user, then set their balance directly\n\n"

                "📩 <b>Recharge Requests</b>\n"
                "Users can send recharge requests here, and you can approve or reject them directly from the bot.\n\n"

                "📦 <b>Inventory</b>\n"
                "/stock – Pick an item, then use the +/- buttons and Confirm\n\n"

                "🧾 <b>Expenses</b>\n"
                "/expense – Pick a category, enter the amount, then confirm before it's logged"
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
            status_code, res = await self.user_service.resolve_recharge_request(chat_id, request_id, action)

            if status_code == 200:
                await self._mark_request_messages_resolved(context, res)
                await self._notify_requester_of_resolution(context, res)
                try:
                    await query.answer(f"Request {action}d", show_alert=False)
                except Exception:
                    self.logger.exception("Failed to answer callback query for request %s", request_id)
            elif status_code == 403:
                try:
                    await query.answer("You are not authorized to do that.", show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for request %s", request_id)
            elif status_code == 404:
                try:
                    await query.answer("Recharge request not found.", show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for request %s", request_id)
            elif status_code == 409:
                detail = res.get("detail", "Request already resolved.")
                resolved_by = res.get("resolved_by_username")
                if resolved_by:
                    detail = f"{detail} By {resolved_by}."
                try:
                    await query.answer(detail, show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for request %s", request_id)
            else:
                try:
                    await query.answer(res.get("detail", "Unknown error"), show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for request %s", request_id)
            return

        if data and data.startswith("pp:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                if msg is not None:
                    await msg.reply_text("❌ Invalid purchase action.")
                return

            action = parts[1]
            purchase_id = parts[2]
            status_code, res = await self.user_service.resolve_product_purchase(chat_id, purchase_id, action)

            if status_code == 200:
                await self._edit_purchase_notifications(context, res)
                try:
                    await query.answer(f"Purchase {action}ed" if action == "reject" else "Marked as given", show_alert=False)
                except Exception:
                    self.logger.exception("Failed to answer callback query for purchase %s", purchase_id)
            elif status_code == 403:
                try:
                    await query.answer("You are not authorized to do that.", show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for purchase %s", purchase_id)
            elif status_code == 404:
                try:
                    await query.answer("Purchase not found.", show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for purchase %s", purchase_id)
            elif status_code == 409:
                detail = res.get("detail", "Purchase already resolved.")
                resolved_by = res.get("resolved_by_username")
                if resolved_by:
                    detail = f"{detail} By {resolved_by}."
                try:
                    await query.answer(detail, show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for purchase %s", purchase_id)
            else:
                try:
                    await query.answer(res.get("detail", "Unknown error"), show_alert=True)
                except Exception:
                    self.logger.exception("Failed to answer callback query for purchase %s", purchase_id)
            return

        if msg is not None:
            await msg.reply_text("❌ Unknown button action.")

    @safe_handler
    async def list_users(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        status_code, res = await self.user_service.list_users(chat_id)

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
        status_code, res = await self.user_service.request_recharge(
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
        status_code, res = await self.user_service.get_user(chat_id, username)

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

    # ---- /recharge and /adjust: guided user search + picker, one-shot args as fallback ----

    # Buttons only render when a search narrows the result to this many or
    # fewer — with 60+ users, showing every one of them as a button would
    # be unusable, so a big/unfiltered list gets a text prompt instead.
    USER_PICKER_THRESHOLD = 10
    MIN_USER_SEARCH_CHARS = 2

    def _format_user_label(self, user: dict) -> str:
        name = f"{user.get('name', '')} {user.get('surname', '')}".strip()
        username = user.get("username")
        return f"{name} (@{username})" if name else f"@{username}"

    def _filter_users(self, users: list[dict], query: str) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return users
        return [
            u for u in users
            if q in f"{u.get('username', '')} {u.get('name', '')} {u.get('surname', '')}".lower()
        ]

    def _build_user_picker_buttons(self, users: list[dict], prefix: str) -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(self._format_user_label(u), callback_data=f"{prefix}:user:{u.get('username')}")]
            for u in users
        ]
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=f"{prefix}:cancel")])
        return InlineKeyboardMarkup(buttons)

    def _amount_prompt_text(self, prefix: str, user: dict) -> str:
        if prefix == "recharge":
            return f"Selected @{user.get('username')}. How much to recharge? (e.g. 10)"
        return (
            f"Selected @{user.get('username')} (current balance {float(user.get('balance', 0)):.2f}€). "
            "Set balance to how much? (e.g. 0)"
        )

    def _format_recharge_result(self, username: str, amount, status_code: int, res: dict) -> str:
        if status_code == 200:
            return f"💰 Successfully recharged {username} with {float(amount):.2f}€"
        elif status_code == 403:
            return "❌ You are not authorized to recharge users."
        elif status_code == 404:
            return "⚠️ User not found."
        elif status_code == 400:
            return f"❌ {res.get('detail', 'Invalid amount')}"
        else:
            return f"⚠️ Error: {res.get('detail', 'Unknown error')}"

    def _format_adjust_result(self, username: str, status_code: int, res: dict) -> str:
        if status_code == 200:
            name = res.get("name") or username
            balance = res.get("balance")
            return f"🧾 Adjusted {name}'s balance to {balance:.2f}€"
        elif status_code == 403:
            return "❌ You are not authorized to adjust balances."
        elif status_code == 404:
            return "⚠️ User not found."
        elif status_code == 400:
            return f"❌ {res.get('detail', 'Invalid amount')}"
        else:
            return f"⚠️ Error: {res.get('detail', 'Unknown error')}"

    async def _start_user_search(self, update, context: ContextTypes.DEFAULT_TYPE, prefix: str):
        """Shared by /recharge and /adjust's no-args entry: fetch the user
        list once and cache it in chat_data. Only renders it as tappable
        buttons if it's small enough to browse — otherwise, it just asks
        for a search term up front (see _handle_user_search_text)."""
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        message = getattr(update, "message", None)

        status_code, res = await self.user_service.list_users(chat_id)
        if status_code == 403:
            action = "recharge" if prefix == "recharge" else "adjust balances for"
            if message is not None:
                await message.reply_text(f"❌ You are not authorized to {action} users.")
            return ConversationHandler.END
        if status_code != 200:
            if message is not None:
                await message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")
            return ConversationHandler.END
        if not res:
            if message is not None:
                await message.reply_text("No users found.")
            return ConversationHandler.END

        context.chat_data[f"{prefix}_all_users"] = res
        next_state = RECHARGE_SEARCH if prefix == "recharge" else ADJUST_SEARCH

        if message is not None:
            if len(res) <= self.USER_PICKER_THRESHOLD:
                await message.reply_text(
                    "👥 Tap a user, or type a name to search.",
                    reply_markup=self._build_user_picker_buttons(res, prefix),
                )
            else:
                await message.reply_text(
                    f"👥 {len(res)} users total. Type at least {self.MIN_USER_SEARCH_CHARS} "
                    "characters of a name or username to search."
                )
        return next_state

    async def _prompt_for_amount(
        self, update, context: ContextTypes.DEFAULT_TYPE, user: dict, prefix: str,
        prompt_text: str, next_state, *, edit: bool,
    ):
        context.chat_data[f"{prefix}_target"] = user
        if edit:
            query = getattr(update, "callback_query", None)
            msg = getattr(query, "message", None) if query is not None else None
            if msg is not None:
                await msg.edit_text(prompt_text)
        else:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(prompt_text)
        return next_state

    async def _handle_user_search_text(self, update, context: ContextTypes.DEFAULT_TYPE, prefix: str, search_state, amount_state):
        """Shared free-text step for both /recharge and /adjust: filters
        the cached user list by whatever was typed, then either auto-picks
        a single match, shows a small picker, or asks to narrow further."""
        message = getattr(update, "message", None)
        text = getattr(message, "text", "") if message is not None else ""
        query = text.strip()
        all_users = context.chat_data.get(f"{prefix}_all_users") or []

        if len(query) < self.MIN_USER_SEARCH_CHARS:
            if message is not None:
                await message.reply_text(f"Type at least {self.MIN_USER_SEARCH_CHARS} characters to search.")
            return search_state

        matches = self._filter_users(all_users, query)

        if not matches:
            if message is not None:
                await message.reply_text("No matching user. Try another name, or /cancel.")
            return search_state

        if len(matches) == 1:
            user = matches[0]
            prompt = self._amount_prompt_text(prefix, user)
            return await self._prompt_for_amount(update, context, user, prefix, prompt, amount_state, edit=False)

        if len(matches) > self.USER_PICKER_THRESHOLD:
            if message is not None:
                await message.reply_text(f"👥 {len(matches)} users match — type more of the name to narrow it down.")
            return search_state

        if message is not None:
            await message.reply_text(
                f"👥 {len(matches)} matches — tap one, or refine your search.",
                reply_markup=self._build_user_picker_buttons(matches, prefix),
            )
        return search_state

    async def _handle_user_choice_callback(self, update, context: ContextTypes.DEFAULT_TYPE, prefix: str, amount_state):
        """Shared callback for both /recharge's and /adjust's user-picker buttons."""
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END
        data = getattr(query, "data", None) or ""
        username = data.split(":", 2)[-1]
        all_users = context.chat_data.get(f"{prefix}_all_users") or []
        user = next((u for u in all_users if u.get("username") == username), None)

        await query.answer()
        if user is None:
            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.edit_text(f"⚠️ That user is no longer available. Run /{prefix} again.")
            return ConversationHandler.END

        prompt = self._amount_prompt_text(prefix, user)
        return await self._prompt_for_amount(update, context, user, prefix, prompt, amount_state, edit=True)

    async def _cancel_user_flow(self, update, context: ContextTypes.DEFAULT_TYPE, prefix: str):
        query = getattr(update, "callback_query", None)
        context.chat_data.pop(f"{prefix}_target", None)
        context.chat_data.pop(f"{prefix}_all_users", None)
        if query is not None:
            await query.answer("Cancelled")
            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.edit_text("❌ Cancelled.")
        return ConversationHandler.END

    @safe_handler
    async def recharge_entry(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        message = getattr(update, "message", None)

        if len(args) == 2:
            # Fallback one-shot form: /recharge <username> <amount>
            username = args[0]
            status_code, res = await self.user_service.recharge(chat_id, username, args[1])
            if message is not None:
                await message.reply_text(self._format_recharge_result(username, args[1], status_code, res))
            return ConversationHandler.END

        return await self._start_user_search(update, context, "recharge")

    @safe_handler
    async def recharge_search(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._handle_user_search_text(update, context, "recharge", RECHARGE_SEARCH, RECHARGE_AWAIT_AMOUNT)

    @safe_handler
    async def recharge_choose_user(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._handle_user_choice_callback(update, context, "recharge", RECHARGE_AWAIT_AMOUNT)

    @safe_handler
    async def recharge_receive_amount(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        message = getattr(update, "message", None)
        text = getattr(message, "text", "") if message is not None else ""

        user = context.chat_data.get("recharge_target")
        if user is None:
            if message is not None:
                await message.reply_text("⚠️ Session expired. Run /recharge again.")
            return ConversationHandler.END

        username = user.get("username")
        status_code, res = await self.user_service.recharge(chat_id, username, text.strip())

        if status_code == 400:
            if message is not None:
                await message.reply_text(f"❌ {res.get('detail', 'Invalid amount')} Try again.")
            return RECHARGE_AWAIT_AMOUNT

        if message is not None:
            await message.reply_text(self._format_recharge_result(username, text.strip(), status_code, res))

        context.chat_data.pop("recharge_target", None)
        context.chat_data.pop("recharge_all_users", None)
        return ConversationHandler.END

    @safe_handler
    async def recharge_cancel(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._cancel_user_flow(update, context, "recharge")

    @safe_handler
    async def adjust_entry(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        message = getattr(update, "message", None)

        if len(args) == 2:
            # Fallback one-shot form: /adjust <username> <new_balance>
            username = args[0]
            status_code, res = await self.user_service.adjust(chat_id, username, args[1])
            if message is not None:
                await message.reply_text(self._format_adjust_result(username, status_code, res))
            return ConversationHandler.END

        return await self._start_user_search(update, context, "adjust")

    @safe_handler
    async def adjust_search(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._handle_user_search_text(update, context, "adjust", ADJUST_SEARCH, ADJUST_AWAIT_AMOUNT)

    @safe_handler
    async def adjust_choose_user(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._handle_user_choice_callback(update, context, "adjust", ADJUST_AWAIT_AMOUNT)

    @safe_handler
    async def adjust_receive_amount(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        message = getattr(update, "message", None)
        text = getattr(message, "text", "") if message is not None else ""

        user = context.chat_data.get("adjust_target")
        if user is None:
            if message is not None:
                await message.reply_text("⚠️ Session expired. Run /adjust again.")
            return ConversationHandler.END

        username = user.get("username")
        status_code, res = await self.user_service.adjust(chat_id, username, text.strip())

        if status_code == 400:
            if message is not None:
                await message.reply_text(f"❌ {res.get('detail', 'Invalid amount')} Try again.")
            return ADJUST_AWAIT_AMOUNT

        if message is not None:
            await message.reply_text(self._format_adjust_result(username, status_code, res))

        context.chat_data.pop("adjust_target", None)
        context.chat_data.pop("adjust_all_users", None)
        return ConversationHandler.END

    @safe_handler
    async def adjust_cancel(self, update, context: ContextTypes.DEFAULT_TYPE):
        return await self._cancel_user_flow(update, context, "adjust")

    @safe_handler
    async def cancel_command(self, update, context: ContextTypes.DEFAULT_TYPE):
        """Shared /cancel fallback for the /stock, /expense, /recharge, and
        /adjust guided flows."""
        for key in (
            "stock_target", "stock_items", "stock_delta", "pending_expense",
            "recharge_target", "recharge_all_users", "adjust_target", "adjust_all_users",
        ):
            context.chat_data.pop(key, None)
        message = getattr(update, "message", None)
        if message is not None:
            await message.reply_text("❌ Cancelled.")
        return ConversationHandler.END

    # ---- /stock: guided item picker + button stepper, one-shot args as fallback ----

    STOCK_STEPS = (-50, -10, -1, 1, 10, 50)

    def _build_stock_item_buttons(self, items: list[dict]) -> InlineKeyboardMarkup:
        buttons = []
        for item in items:
            warn = "⚠️ " if item.get("is_low_stock") else ""
            label = f"{warn}{item.get('name')} ({item.get('current_stock'):g} {item.get('unit')})"
            buttons.append([InlineKeyboardButton(label, callback_data=f"stock:item:{item.get('id')}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="stock:cancel")])
        return InlineKeyboardMarkup(buttons)

    def _format_delta(self, delta: float) -> str:
        return f"+{delta:g}" if delta > 0 else f"{delta:g}"

    def _format_stock_stepper_text(self, item: dict, delta: float) -> str:
        current = item.get("current_stock", 0)
        unit = item.get("unit", "")
        new_stock = current + delta
        change = self._format_delta(delta) if delta else "0"
        return (
            f"📦 <b>{self._escape_html(item.get('name'))}</b>\n"
            f"Current stock: {current:g} {unit}\n"
            f"Change: {change}\n"
            f"New stock: {new_stock:g} {unit}\n\n"
            "Use the buttons below, then Confirm."
        )

    def _build_stock_stepper_buttons(self, delta: float) -> InlineKeyboardMarkup:
        step_row = [
            InlineKeyboardButton(self._format_delta(step), callback_data=f"stock:step:{step}")
            for step in self.STOCK_STEPS
        ]
        return InlineKeyboardMarkup([
            step_row,
            [
                InlineKeyboardButton("✅ Confirm", callback_data="stock:confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="stock:cancel"),
            ],
        ])

    def _format_stock_result_text(self, item_name: str, delta, status_code: int, res: dict) -> str:
        if status_code == 200:
            name = res.get("name") or item_name
            unit = res.get("unit") or ""
            new_stock = res.get("current_stock")
            try:
                old_stock = new_stock - float(delta)
                return f"📦 {name}: {old_stock:g} → {new_stock:g} {unit}"
            except Exception:
                return f"📦 {name}: now {new_stock} {unit}"
        elif status_code == 403:
            return "❌ You are not authorized to manage inventory."
        elif status_code == 404:
            return f"⚠️ Item not found: {item_name}. Run /stock to see the list."
        elif status_code == 400:
            return f"❌ {res.get('detail', 'Invalid amount')}"
        else:
            return f"⚠️ Error: {res.get('detail', 'Unknown error')}"

    async def _reply_stock_result(self, message, item_name: str, delta, status_code: int, res: dict):
        if message is not None:
            await message.reply_text(self._format_stock_result_text(item_name, delta, status_code, res))

    async def _edit_stock_result(self, message, item_name: str, delta, status_code: int, res: dict):
        if message is not None:
            await message.edit_text(self._format_stock_result_text(item_name, delta, status_code, res))

    @safe_handler
    async def stock_entry(self, update, context: ContextTypes.DEFAULT_TYPE):
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        args = getattr(context, "args", None) or []
        message = getattr(update, "message", None)

        if len(args) >= 2:
            # Fallback one-shot form: /stock <delta> <item name...>
            delta_raw = args[0]
            item_name = " ".join(args[1:])
            status_code, res = await self.user_service.adjust_stock(chat_id, item_name, delta_raw)
            await self._reply_stock_result(message, item_name, delta_raw, status_code, res)
            return ConversationHandler.END

        status_code, res = await self.user_service.list_inventory(chat_id)
        if status_code == 403:
            if message is not None:
                await message.reply_text("❌ You are not authorized to manage inventory.")
            return ConversationHandler.END
        if status_code != 200:
            if message is not None:
                await message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")
            return ConversationHandler.END
        if not res:
            if message is not None:
                await message.reply_text("No inventory items found.")
            return ConversationHandler.END

        context.chat_data["stock_items"] = {str(item["id"]): item for item in res}
        if message is not None:
            await message.reply_text("📦 Select an item to adjust:", reply_markup=self._build_stock_item_buttons(res))
        return STOCK_CHOOSE_ITEM

    @safe_handler
    async def stock_choose_item(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        data = getattr(query, "data", None) or ""
        item_id = data.split(":", 2)[-1]
        items = context.chat_data.get("stock_items") or {}
        item = items.get(item_id)

        await query.answer()
        msg = getattr(query, "message", None)
        if item is None:
            if msg is not None:
                await msg.edit_text("⚠️ That item is no longer available. Run /stock again.")
            return ConversationHandler.END

        context.chat_data["stock_target"] = item
        context.chat_data["stock_delta"] = 0.0
        if msg is not None:
            await msg.edit_text(
                self._format_stock_stepper_text(item, 0.0),
                reply_markup=self._build_stock_stepper_buttons(0.0),
                parse_mode="HTML",
            )
        return STOCK_ADJUST_DELTA

    @safe_handler
    async def stock_step(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        item = context.chat_data.get("stock_target")
        if item is None:
            await query.answer("Session expired. Run /stock again.", show_alert=True)
            return ConversationHandler.END

        data = getattr(query, "data", None) or ""
        try:
            step = float(data.split(":", 2)[-1])
        except Exception:
            step = 0.0

        delta = context.chat_data.get("stock_delta", 0.0) + step
        context.chat_data["stock_delta"] = delta

        await query.answer()
        msg = getattr(query, "message", None)
        if msg is not None:
            await msg.edit_text(
                self._format_stock_stepper_text(item, delta),
                reply_markup=self._build_stock_stepper_buttons(delta),
                parse_mode="HTML",
            )
        return STOCK_ADJUST_DELTA

    @safe_handler
    async def stock_confirm(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        item = context.chat_data.get("stock_target")
        delta = context.chat_data.get("stock_delta", 0.0)
        if item is None:
            await query.answer("Session expired. Run /stock again.", show_alert=True)
            return ConversationHandler.END

        if delta == 0:
            await query.answer("Change the amount before confirming.", show_alert=True)
            return STOCK_ADJUST_DELTA

        msg = getattr(query, "message", None)
        chat = getattr(msg, "chat", None)
        chat_id = getattr(chat, "id", None)
        status_code, res = await self.user_service.adjust_stock(chat_id, item.get("name"), delta)

        if status_code == 200:
            await query.answer("Stock updated")
        elif status_code == 403:
            await query.answer("You are not authorized to manage inventory.", show_alert=True)
        else:
            await query.answer(res.get("detail", "Unknown error"), show_alert=True)

        await self._edit_stock_result(msg, item.get("name"), delta, status_code, res)

        context.chat_data.pop("stock_target", None)
        context.chat_data.pop("stock_items", None)
        context.chat_data.pop("stock_delta", None)
        return ConversationHandler.END

    @safe_handler
    async def stock_cancel(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        context.chat_data.pop("stock_target", None)
        context.chat_data.pop("stock_items", None)
        context.chat_data.pop("stock_delta", None)
        if query is not None:
            await query.answer("Cancelled")
            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.edit_text("❌ Cancelled.")
        return ConversationHandler.END

    # ---- /expense: guided category/amount/description + confirm, one-shot args as fallback ----

    def _build_expense_category_buttons(self) -> InlineKeyboardMarkup:
        row = [
            InlineKeyboardButton(EXPENSE_CATEGORY_LABELS[c], callback_data=f"expense:cat:{c}")
            for c in EXPENSE_CATEGORIES
        ]
        return InlineKeyboardMarkup([row, [InlineKeyboardButton("❌ Cancel", callback_data="expense:cancel")]])

    def _build_expense_confirm_buttons(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="expense:confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="expense:cancel"),
        ]])

    def _format_expense_summary(self, category, amount, description, header="📝 <b>Confirm expense</b>") -> str:
        lines = [
            header,
            "",
            f"Category: {self._escape_html(category)}",
            f"Amount: {float(amount):.2f}€",
        ]
        if description:
            lines.append(f"Description: {self._escape_html(description)}")
        return "\n".join(lines)

    async def _show_expense_confirmation(self, update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool):
        pending = context.chat_data.get("pending_expense") or {}
        text = self._format_expense_summary(pending.get("category"), pending.get("amount"), pending.get("description"))
        buttons = self._build_expense_confirm_buttons()

        if edit:
            query = getattr(update, "callback_query", None)
            msg = getattr(query, "message", None) if query is not None else None
            if msg is not None:
                await msg.edit_text(text, reply_markup=buttons, parse_mode="HTML")
        else:
            message = getattr(update, "message", None)
            if message is not None:
                await message.reply_text(text, reply_markup=buttons, parse_mode="HTML")
        return EXPENSE_CONFIRM

    async def _start_expense_confirmation(self, update, context: ContextTypes.DEFAULT_TYPE, category, amount, description):
        ok, normalized_category, a, error = validate_expense_input(category, amount)
        message = getattr(update, "message", None)
        if not ok:
            if message is not None:
                await message.reply_text(f"❌ {error}")
            return ConversationHandler.END

        context.chat_data["pending_expense"] = {
            "category": normalized_category,
            "amount": a,
            "description": description,
        }
        return await self._show_expense_confirmation(update, context, edit=False)

    @safe_handler
    async def expense_entry(self, update, context: ContextTypes.DEFAULT_TYPE):
        args = getattr(context, "args", None) or []
        message = getattr(update, "message", None)

        if len(args) >= 2:
            # Fallback one-shot form: /expense <category> <amount> [description...]
            category = args[0]
            amount = args[1]
            description = " ".join(args[2:]).strip() or None
            return await self._start_expense_confirmation(update, context, category, amount, description)

        if message is not None:
            await message.reply_text("🧾 Select an expense category:", reply_markup=self._build_expense_category_buttons())
        return EXPENSE_CHOOSE_CATEGORY

    @safe_handler
    async def expense_choose_category(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        data = getattr(query, "data", None) or ""
        category = data.split(":", 2)[-1]
        await query.answer()

        context.chat_data["pending_expense"] = {"category": category}
        msg = getattr(query, "message", None)
        if msg is not None:
            await msg.edit_text(f"Category: {EXPENSE_CATEGORY_LABELS.get(category, category)}\n\nHow much did it cost? (e.g. 15.50)")
        return EXPENSE_AWAIT_AMOUNT

    @safe_handler
    async def expense_receive_amount(self, update, context: ContextTypes.DEFAULT_TYPE):
        message = getattr(update, "message", None)
        text = getattr(message, "text", "") if message is not None else ""
        pending = context.chat_data.get("pending_expense")

        if pending is None:
            if message is not None:
                await message.reply_text("⚠️ Session expired. Run /expense again.")
            return ConversationHandler.END

        try:
            amount = float(text.strip())
        except Exception:
            if message is not None:
                await message.reply_text("❌ That's not a valid number. How much did it cost? (e.g. 15.50)")
            return EXPENSE_AWAIT_AMOUNT

        if amount <= 0:
            if message is not None:
                await message.reply_text("❌ Amount must be positive. How much did it cost?")
            return EXPENSE_AWAIT_AMOUNT

        pending["amount"] = amount
        if message is not None:
            await message.reply_text(
                "Add a description, or tap Skip.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Skip", callback_data="expense:skip_desc")]]),
            )
        return EXPENSE_AWAIT_DESCRIPTION

    @safe_handler
    async def expense_receive_description(self, update, context: ContextTypes.DEFAULT_TYPE):
        message = getattr(update, "message", None)
        text = getattr(message, "text", "") if message is not None else ""
        pending = context.chat_data.get("pending_expense")

        if pending is None:
            if message is not None:
                await message.reply_text("⚠️ Session expired. Run /expense again.")
            return ConversationHandler.END

        pending["description"] = text.strip() or None
        return await self._show_expense_confirmation(update, context, edit=False)

    @safe_handler
    async def expense_skip_description(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        await query.answer()
        pending = context.chat_data.get("pending_expense")
        if pending is None:
            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.edit_text("⚠️ Session expired. Run /expense again.")
            return ConversationHandler.END

        pending["description"] = None
        return await self._show_expense_confirmation(update, context, edit=True)

    @safe_handler
    async def expense_confirm(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        if query is None:
            return ConversationHandler.END

        msg = getattr(query, "message", None)
        chat = getattr(msg, "chat", None)
        chat_id = getattr(chat, "id", None)
        pending = context.chat_data.pop("pending_expense", None)

        if pending is None:
            await query.answer("Nothing to confirm — run /expense again.", show_alert=True)
            return ConversationHandler.END

        status_code, res = await self.user_service.create_expense(
            chat_id, pending.get("category"), pending.get("amount"), pending.get("description"),
        )

        if status_code in (200, 201):
            await query.answer("Expense logged")
            if msg is not None:
                await msg.edit_text(
                    self._format_expense_summary(
                        pending.get("category"), pending.get("amount"), pending.get("description"),
                        header="✅ <b>Expense logged</b>",
                    ),
                    parse_mode="HTML",
                )
        elif status_code == 403:
            await query.answer("You are not authorized to log expenses.", show_alert=True)
        else:
            await query.answer(res.get("detail", "Unknown error"), show_alert=True)
        return ConversationHandler.END

    @safe_handler
    async def expense_cancel(self, update, context: ContextTypes.DEFAULT_TYPE):
        query = getattr(update, "callback_query", None)
        context.chat_data.pop("pending_expense", None)
        if query is not None:
            await query.answer("Cancelled")
            msg = getattr(query, "message", None)
            if msg is not None:
                await msg.edit_text("❌ Expense cancelled.")
        return ConversationHandler.END
