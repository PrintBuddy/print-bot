from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from .api import get_me, generate_voucher, get_users, get_user, recharge_user, adjust_balance


# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    status_code, user_res = get_me(chat_id)

    # Botones rápidos
    buttons = [
        [InlineKeyboardButton("Generate 1€ voucher", callback_data="gen_1")],
        [InlineKeyboardButton("Generate 2€ voucher", callback_data="gen_2")],
        [InlineKeyboardButton("Generate 5€ voucher", callback_data="gen_5")],
        [InlineKeyboardButton("Generate 10€ voucher", callback_data="gen_10")]
    ]
    keyboard = InlineKeyboardMarkup(buttons)

    if status_code == 200:
        name = user_res.get("name") or "Admin"
        msg = (
            f"✅ <b>Welcome {name}!</b>\n\n"
            "Here are your available commands:\n\n"
            "📘 <b>User Commands:</b>\n"
            "/myid – Show your Telegram chat ID\n\n"
            "💰 <b>Voucher & Balance:</b>\n"
            "/generate &lt;amount&gt; – Generate a voucher\n"
            "/users – List all users\n"
            "/user &lt;username&gt; – Show user info\n"
            "/recharge &lt;username&gt; &lt;amount&gt; – Add credit to a user\n"
            "/adjust &lt;username&gt; &lt;new_balance&gt; – Set user balance manually\n"
        )
    elif status_code == 403:
        msg = (
            "❌ You are not authorized to manage vouchers.\n\n"
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

    await update.message.reply_text(msg, reply_markup=keyboard, parse_mode="HTML")



async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /generate <amount>")
        return

    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Amount must be a number")
        return

    status_code, res = generate_voucher(chat_id, amount)

    if status_code == 200:
        await update.message.reply_text(f"✅ Voucher generated: {res.get('code')}")
    elif status_code == 403:
        await update.message.reply_text("❌ You are not authorized to generate vouchers")
    else:
        await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Your Telegram chat ID is: {chat_id}")


# --- Callback for inline buttons ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # acknowledge the callback
    chat_id = query.message.chat.id

    # Parse the amount from the button
    try:
        amount = float(query.data.split("_")[1])
    except Exception:
        await query.message.reply_text("❌ Invalid button data")
        return

    status_code, res = generate_voucher(chat_id, amount)
    if status_code == 200:
        await query.message.reply_text(f"✅ Voucher generated: {res.get('code')}")
    elif status_code == 403:
        await query.message.reply_text("❌ You are not authorized to generate vouchers")
    else:
        await query.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    status_code, res = get_users(chat_id)

    if status_code == 200:
        if not res:
            await update.message.reply_text("No users found.")
            return

        msg_lines = ["📋 Users list:"]
        for u in res:
            # Ajusta según los campos de UserAdminRead
            msg_lines.append(f"{u.get('name')} {u.get('surname')} ({u.get('username')})")
        await update.message.reply_text("\n".join(msg_lines))
    elif status_code == 403:
        await update.message.reply_text("❌ You are not authorized to view users.")
    else:
        await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")



async def get_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /user <username>")
        return

    username = context.args[0]
    status_code, res = get_user(chat_id, username)

    if status_code == 200:

        msg = (
            f"👤 User Info\n"
            f"Name: {res.get('name')} {res.get('surname')}\n"
            f"Username: {res.get('username')}\n"
            f"Balance: {res.get('balance'):.2f}€"
        )
        await update.message.reply_text(msg)
    elif status_code == 403:
        await update.message.reply_text("❌ You are not authorized to view users.")
    elif status_code == 404:
        await update.message.reply_text("⚠️ User not found.")
    else:
        await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")


async def recharge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /recharge <username> <amount>")
        return

    username = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Amount must be a number")
        return

    status_code, res = recharge_user(chat_id, username, amount)

    if status_code == 200:
        await update.message.reply_text(f"💰 Successfully recharged {username} with {amount:.2f}€")
    elif status_code == 403:
        await update.message.reply_text("❌ You are not authorized to recharge users.")
    elif status_code == 404:
        await update.message.reply_text("⚠️ User not found.")
    else:
        await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")


async def adjust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /adjust <username> <new_balance>")
        return

    username = context.args[0]
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ Balance must be a number")
        return

    status_code, res = adjust_balance(chat_id, username, amount)

    if status_code == 200:
        name = res.get("name") or username
        balance = res.get("balance")
        await update.message.reply_text(f"🧾 Adjusted {name}'s balance to {balance:.2f}€")
    elif status_code == 403:
        await update.message.reply_text("❌ You are not authorized to adjust balances.")
    elif status_code == 404:
        await update.message.reply_text("⚠️ User not found.")
    else:
        await update.message.reply_text(f"⚠️ Error: {res.get('detail', 'Unknown error')}")