import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot_handlers import (
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
from src.services import UserService
from telegram.ext import ConversationHandler
from tests.conftest import FakeAPIClient


def make_query(data, answer_side_effect=None):
    answer = AsyncMock(side_effect=answer_side_effect)
    message = SimpleNamespace(chat=SimpleNamespace(id=123), reply_text=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(data=data, message=message, answer=answer)


def make_command_update(args=None, chat_id=123):
    message = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message)
    context = SimpleNamespace(args=args or [], chat_data={})
    return update, context, message


def make_text_update(text, chat_data=None, chat_id=123):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message)
    context = SimpleNamespace(args=[], chat_data=chat_data if chat_data is not None else {})
    return update, context, message


@pytest.mark.asyncio
async def test_button_callback_logs_when_answer_fails(caplog):
    fake_client = FakeAPIClient(response=(200, {}))
    handlers = BotHandlers(services={"user": UserService(fake_client)})

    query = make_query("rr:approve:req123", answer_side_effect=RuntimeError("telegram unavailable"))
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=AsyncMock())

    with caplog.at_level(logging.ERROR):
        await handlers.button_callback(update, context)

    query.answer.assert_awaited_once()
    assert "Failed to answer callback query for request req123" in caplog.text


@pytest.mark.asyncio
async def test_mark_resolved_falls_back_to_db_tracked_message():
    """A request created from the web app was never announced by this bot
    process, so it's never in the in-memory notifications map — the only
    way to edit its Telegram message is the notified_chat_id/message_id
    the backend persisted on the request row itself."""
    fake_client = FakeAPIClient(response=(200, {}))
    handlers = BotHandlers(services={"user": UserService(fake_client)})
    context = SimpleNamespace(bot=AsyncMock())

    payload = {
        "request": {
            "id": "web-created-req",
            "status": "approved",
            "resolved_by_username": "admin_bob",
            "notified_chat_id": "555",
            "notified_message_id": 999,
        },
        "user_name": "Test",
        "user_surname": "User",
    }

    await handlers._mark_request_messages_resolved(context, payload)

    context.bot.edit_message_text.assert_awaited_once()
    _, kwargs = context.bot.edit_message_text.call_args
    assert kwargs["chat_id"] == 555
    assert kwargs["message_id"] == 999


@pytest.mark.asyncio
async def test_mark_resolved_prefers_in_memory_notifications_when_present():
    """Bot-created (broadcast) requests are still tracked in-memory —
    that path must keep working unchanged, and must NOT also fall back to
    (nonexistent) notified_chat_id/message_id fields."""
    fake_client = FakeAPIClient(response=(200, {}))
    handlers = BotHandlers(services={"user": UserService(fake_client)})
    handlers.recharge_request_notifications["bot-created-req"] = [
        {"chat_id": 111, "message_id": 222},
        {"chat_id": 333, "message_id": 444},
    ]
    context = SimpleNamespace(bot=AsyncMock())

    payload = {
        "request": {
            "id": "bot-created-req",
            "status": "rejected",
            "resolved_by_username": "admin_bob",
        },
        "user_name": "Test",
        "user_surname": "User",
    }

    await handlers._mark_request_messages_resolved(context, payload)

    assert context.bot.edit_message_text.await_count == 2
    assert "bot-created-req" not in handlers.recharge_request_notifications


@pytest.mark.asyncio
async def test_button_callback_resolves_purchase_and_edits_every_admin_notification():
    """A purchase is broadcast to every admin, not just one — resolving it
    from a Telegram button must edit every one of those messages, using
    the notifications list the backend hands back (there's no in-memory
    tracking for this at all, since the backend always sent these)."""
    response = (200, {
        "purchase": {
            "id": "purchase123",
            "username": "alice",
            "product_name": "Spiral Binding",
            "quantity": 2,
            "total_amount": 2.0,
            "status": "fulfilled",
            "admin_message": None,
            "resolved_by_username": "admin_bob",
        },
        "notifications": [
            {"chat_id": "111", "message_id": 222},
            {"chat_id": "333", "message_id": 444},
        ],
    })
    fake_client = FakeAPIClient(response=response)
    handlers = BotHandlers(services={"user": UserService(fake_client)})

    query = make_query("pp:fulfill:purchase123")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=AsyncMock())

    await handlers.button_callback(update, context)

    assert ("resolve_product_purchase", (123, "purchase123", "fulfill"), {}) in fake_client.calls
    assert context.bot.edit_message_text.await_count == 2
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_button_callback_purchase_already_resolved_shows_alert():
    response = (409, {"detail": "Purchase has already been resolved", "resolved_by_username": "admin_ana"})
    fake_client = FakeAPIClient(response=response)
    handlers = BotHandlers(services={"user": UserService(fake_client)})

    query = make_query("pp:reject:purchase123")
    update = SimpleNamespace(callback_query=query)
    context = SimpleNamespace(bot=AsyncMock())

    await handlers.button_callback(update, context)

    query.answer.assert_awaited_once()
    args, kwargs = query.answer.call_args
    assert "admin_ana" in args[0]
    assert kwargs["show_alert"] is True
    assert context.bot.edit_message_text.await_count == 0


class TestStockFlow:
    @pytest.mark.asyncio
    async def test_entry_fallback_args_applies_immediately(self):
        fake_client = FakeAPIClient(response=(200, {"name": "A4 Paper", "current_stock": 70.0, "unit": "sheets"}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=["-30", "A4", "Paper"])

        result = await handlers.stock_entry(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("adjust_stock", (123, "A4 Paper", -30.0), {})]
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_no_args_lists_items_and_enters_choose_item(self):
        items = [{"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets", "is_low_stock": False}]
        fake_client = FakeAPIClient(response=(200, items))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.stock_entry(update, context)

        assert result == STOCK_CHOOSE_ITEM
        assert context.chat_data["stock_items"] == {"item1": items[0]}
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_forbidden_ends_conversation(self):
        fake_client = FakeAPIClient(response=(403, {"detail": "nope"}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.stock_entry(update, context)

        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_choose_item_stores_target_and_shows_stepper(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        item = {"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets"}
        query = make_query("stock:item:item1")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_items": {"item1": item}})

        result = await handlers.stock_choose_item(update, context)

        assert result == STOCK_ADJUST_DELTA
        assert context.chat_data["stock_target"] == item
        assert context.chat_data["stock_delta"] == 0.0
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_accumulates_delta_and_stays_in_state(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        item = {"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets"}
        query = make_query("stock:step:10")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_target": item, "stock_delta": 5.0})

        result = await handlers.stock_step(update, context)

        assert result == STOCK_ADJUST_DELTA
        assert context.chat_data["stock_delta"] == 15.0
        assert fake_client.calls == []
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_step_with_negative_button_subtracts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        item = {"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets"}
        query = make_query("stock:step:-50")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_target": item, "stock_delta": 0.0})

        result = await handlers.stock_step(update, context)

        assert result == STOCK_ADJUST_DELTA
        assert context.chat_data["stock_delta"] == -50.0

    @pytest.mark.asyncio
    async def test_confirm_with_zero_delta_shows_alert_and_stays(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        item = {"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets"}
        query = make_query("stock:confirm")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_target": item, "stock_delta": 0.0})

        result = await handlers.stock_confirm(update, context)

        assert result == STOCK_ADJUST_DELTA
        assert fake_client.calls == []
        query.answer.assert_awaited_once()
        args, kwargs = query.answer.call_args
        assert kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_confirm_with_delta_applies_and_ends(self):
        fake_client = FakeAPIClient(response=(200, {"name": "A4 Paper", "current_stock": 70.0, "unit": "sheets"}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        item = {"id": "item1", "name": "A4 Paper", "current_stock": 100.0, "unit": "sheets"}
        query = make_query("stock:confirm")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_target": item, "stock_delta": -30.0, "stock_items": {}})

        result = await handlers.stock_confirm(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("adjust_stock", (123, "A4 Paper", -30.0), {})]
        assert "stock_target" not in context.chat_data
        assert "stock_delta" not in context.chat_data
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("stock:cancel")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"stock_target": {}, "stock_items": {}, "stock_delta": 5.0})

        result = await handlers.stock_cancel(update, context)

        assert result == ConversationHandler.END
        assert context.chat_data == {}
        query.message.edit_text.assert_awaited_once()


class TestExpenseFlow:
    @pytest.mark.asyncio
    async def test_entry_fallback_valid_args_shows_confirm(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=["toner", "15.5", "cartridge"])

        result = await handlers.expense_entry(update, context)

        assert result == EXPENSE_CONFIRM
        assert fake_client.calls == []  # not submitted yet — confirm required
        assert context.chat_data["pending_expense"] == {"category": "toner", "amount": 15.5, "description": "cartridge"}
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_fallback_invalid_args_ends_without_confirm(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=["snacks", "15.5"])

        result = await handlers.expense_entry(update, context)

        assert result == ConversationHandler.END
        assert "pending_expense" not in context.chat_data

    @pytest.mark.asyncio
    async def test_entry_no_args_enters_choose_category(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.expense_entry(update, context)

        assert result == EXPENSE_CHOOSE_CATEGORY
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_choose_category_stores_pending_and_prompts_for_amount(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("expense:cat:toner")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={})

        result = await handlers.expense_choose_category(update, context)

        assert result == EXPENSE_AWAIT_AMOUNT
        assert context.chat_data["pending_expense"] == {"category": "toner"}

    @pytest.mark.asyncio
    async def test_receive_amount_invalid_reprompts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("not-a-number", chat_data={"pending_expense": {"category": "toner"}})

        result = await handlers.expense_receive_amount(update, context)

        assert result == EXPENSE_AWAIT_AMOUNT

    @pytest.mark.asyncio
    async def test_receive_amount_non_positive_reprompts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("0", chat_data={"pending_expense": {"category": "toner"}})

        result = await handlers.expense_receive_amount(update, context)

        assert result == EXPENSE_AWAIT_AMOUNT

    @pytest.mark.asyncio
    async def test_receive_amount_valid_moves_to_description(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        pending = {"category": "toner"}
        update, context, message = make_text_update("15.5", chat_data={"pending_expense": pending})

        result = await handlers.expense_receive_amount(update, context)

        assert result == EXPENSE_AWAIT_DESCRIPTION
        assert pending["amount"] == 15.5

    @pytest.mark.asyncio
    async def test_skip_description_moves_to_confirm(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("expense:skip_desc")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"pending_expense": {"category": "toner", "amount": 15.5}})

        result = await handlers.expense_skip_description(update, context)

        assert result == EXPENSE_CONFIRM
        assert context.chat_data["pending_expense"]["description"] is None
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_receive_description_moves_to_confirm(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        pending = {"category": "toner", "amount": 15.5}
        update, context, message = make_text_update("cartridge", chat_data={"pending_expense": pending})

        result = await handlers.expense_receive_description(update, context)

        assert result == EXPENSE_CONFIRM
        assert pending["description"] == "cartridge"

    @pytest.mark.asyncio
    async def test_confirm_submits_once_and_ends(self):
        fake_client = FakeAPIClient(response=(201, {"success": True}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("expense:confirm")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            chat_data={"pending_expense": {"category": "toner", "amount": 15.5, "description": "cartridge"}}
        )

        result = await handlers.expense_confirm(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("create_expense", (123, "toner", 15.5, "cartridge"), {})]
        assert "pending_expense" not in context.chat_data
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancel_never_calls_backend(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("expense:cancel")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"pending_expense": {"category": "toner", "amount": 15.5}})

        result = await handlers.expense_cancel(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == []
        assert "pending_expense" not in context.chat_data
        query.message.edit_text.assert_awaited_once()


USERS = [
    {"username": "alice_p", "name": "Alice", "surname": "Perez", "balance": 5.0},
    {"username": "bruno_g", "name": "Bruno", "surname": "Gomez", "balance": 10.0},
]

MANY_USERS = [
    {"username": f"user{i}", "name": f"Name{i}", "surname": f"Surname{i}", "balance": 0.0}
    for i in range(60)
]


class TestRechargeFlow:
    @pytest.mark.asyncio
    async def test_entry_fallback_args_applies_immediately(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=["alice_p", "10"])

        result = await handlers.recharge_entry(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("recharge_user", (123, "alice_p", 10.0), {})]
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_no_args_lists_users_and_enters_search(self):
        fake_client = FakeAPIClient(response=(200, USERS))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.recharge_entry(update, context)

        assert result == RECHARGE_SEARCH
        assert context.chat_data["recharge_all_users"] == USERS
        # Small list (<= threshold) — shows tappable buttons immediately.
        _, kwargs = message.reply_text.call_args
        assert "reply_markup" in kwargs

    @pytest.mark.asyncio
    async def test_entry_with_many_users_asks_to_search_first_no_buttons(self):
        fake_client = FakeAPIClient(response=(200, MANY_USERS))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.recharge_entry(update, context)

        assert result == RECHARGE_SEARCH
        assert context.chat_data["recharge_all_users"] == MANY_USERS
        # Too many to browse — no button list, just a prompt to type a search.
        args, kwargs = message.reply_text.call_args
        assert "reply_markup" not in kwargs
        assert "60 users" in args[0]

    @pytest.mark.asyncio
    async def test_search_query_too_short_reprompts_without_filtering(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("a", chat_data={"recharge_all_users": MANY_USERS})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_SEARCH
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_too_many_matches_asks_to_narrow_no_buttons(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("name", chat_data={"recharge_all_users": MANY_USERS})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_SEARCH
        args, kwargs = message.reply_text.call_args
        assert "reply_markup" not in kwargs
        assert "narrow" in args[0]

    @pytest.mark.asyncio
    async def test_search_narrowed_to_few_matches_shows_buttons(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        # 60 unrelated users + 3 sharing a distinctive substring in their name.
        pool = MANY_USERS + [
            {"username": f"zzz{i}", "name": "Zelda", "surname": f"Q{i}", "balance": 0.0} for i in range(3)
        ]
        update, context, message = make_text_update("zelda", chat_data={"recharge_all_users": pool})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_SEARCH
        args, kwargs = message.reply_text.call_args
        assert "reply_markup" in kwargs
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_forbidden_ends_conversation(self):
        fake_client = FakeAPIClient(response=(403, {"detail": "nope"}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.recharge_entry(update, context)

        assert result == ConversationHandler.END

    @pytest.mark.asyncio
    async def test_search_no_match_reprompts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("zzz", chat_data={"recharge_all_users": USERS})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_SEARCH

    @pytest.mark.asyncio
    async def test_search_single_match_moves_to_amount(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("alice", chat_data={"recharge_all_users": USERS})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_AWAIT_AMOUNT
        assert context.chat_data["recharge_target"]["username"] == "alice_p"

    @pytest.mark.asyncio
    async def test_search_multiple_matches_shows_picker(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("_", chat_data={"recharge_all_users": USERS})

        result = await handlers.recharge_search(update, context)

        assert result == RECHARGE_SEARCH
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_choose_user_moves_to_amount(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("recharge:user:alice_p")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"recharge_all_users": USERS})

        result = await handlers.recharge_choose_user(update, context)

        assert result == RECHARGE_AWAIT_AMOUNT
        assert context.chat_data["recharge_target"]["username"] == "alice_p"
        query.message.edit_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_receive_amount_applies_and_ends(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update(
            "10", chat_data={"recharge_target": USERS[0], "recharge_all_users": USERS}
        )

        result = await handlers.recharge_receive_amount(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("recharge_user", (123, "alice_p", 10.0), {})]
        assert "recharge_target" not in context.chat_data

    @pytest.mark.asyncio
    async def test_receive_amount_invalid_reprompts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("not-a-number", chat_data={"recharge_target": USERS[0]})

        result = await handlers.recharge_receive_amount(update, context)

        assert result == RECHARGE_AWAIT_AMOUNT
        assert context.chat_data["recharge_target"] == USERS[0]

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("recharge:cancel")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"recharge_target": {}, "recharge_all_users": []})

        result = await handlers.recharge_cancel(update, context)

        assert result == ConversationHandler.END
        assert context.chat_data == {}


class TestAdjustFlow:
    @pytest.mark.asyncio
    async def test_entry_fallback_args_applies_immediately(self):
        fake_client = FakeAPIClient(response=(200, {"name": "Alice", "balance": 0.0}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=["alice_p", "0"])

        result = await handlers.adjust_entry(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("adjust_balance", (123, "alice_p", 0.0), {})]
        message.reply_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_entry_no_args_lists_users_and_enters_search(self):
        fake_client = FakeAPIClient(response=(200, USERS))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])

        result = await handlers.adjust_entry(update, context)

        assert result == ADJUST_SEARCH
        assert context.chat_data["adjust_all_users"] == USERS

    @pytest.mark.asyncio
    async def test_search_single_match_moves_to_amount(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("bruno", chat_data={"adjust_all_users": USERS})

        result = await handlers.adjust_search(update, context)

        assert result == ADJUST_AWAIT_AMOUNT
        assert context.chat_data["adjust_target"]["username"] == "bruno_g"

    @pytest.mark.asyncio
    async def test_choose_user_moves_to_amount(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("adjust:user:bruno_g")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"adjust_all_users": USERS})

        result = await handlers.adjust_choose_user(update, context)

        assert result == ADJUST_AWAIT_AMOUNT
        assert context.chat_data["adjust_target"]["username"] == "bruno_g"

    @pytest.mark.asyncio
    async def test_receive_amount_applies_and_ends(self):
        fake_client = FakeAPIClient(response=(200, {"name": "Bruno", "balance": 0.0}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update(
            "0", chat_data={"adjust_target": USERS[1], "adjust_all_users": USERS}
        )

        result = await handlers.adjust_receive_amount(update, context)

        assert result == ConversationHandler.END
        assert fake_client.calls == [("adjust_balance", (123, "bruno_g", 0.0), {})]
        assert "adjust_target" not in context.chat_data

    @pytest.mark.asyncio
    async def test_receive_amount_negative_reprompts(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_text_update("-5", chat_data={"adjust_target": USERS[1]})

        result = await handlers.adjust_receive_amount(update, context)

        assert result == ADJUST_AWAIT_AMOUNT

    @pytest.mark.asyncio
    async def test_cancel_clears_state(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        query = make_query("adjust:cancel")
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(chat_data={"adjust_target": {}, "adjust_all_users": []})

        result = await handlers.adjust_cancel(update, context)

        assert result == ConversationHandler.END
        assert context.chat_data == {}


class TestCancelCommand:
    @pytest.mark.asyncio
    async def test_clears_all_flow_state(self):
        fake_client = FakeAPIClient(response=(200, {}))
        handlers = BotHandlers(services={"user": UserService(fake_client)})
        update, context, message = make_command_update(args=[])
        context.chat_data.update({
            "stock_target": {}, "stock_items": {}, "stock_delta": 5.0,
            "pending_expense": {}, "recharge_target": {}, "recharge_all_users": [],
            "adjust_target": {}, "adjust_all_users": [],
        })

        result = await handlers.cancel_command(update, context)

        assert result == ConversationHandler.END
        assert context.chat_data == {}
        message.reply_text.assert_awaited_once()
