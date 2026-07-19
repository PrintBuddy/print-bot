import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.bot_handlers import BotHandlers
from src.services import UserService
from tests.conftest import FakeAPIClient


def make_query(data, answer_side_effect=None):
    answer = AsyncMock(side_effect=answer_side_effect)
    message = SimpleNamespace(chat=SimpleNamespace(id=123), reply_text=AsyncMock())
    return SimpleNamespace(data=data, message=message, answer=answer)


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
