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
