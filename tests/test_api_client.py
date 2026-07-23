import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api_client import APIClient


def make_client():
    return APIClient(base_url="http://example.test", secret="secret")


def test_safe_json_logs_on_parse_failure(caplog):
    client = make_client()
    res = MagicMock()
    res.json.side_effect = ValueError("not json")
    res.text = "plain text body"
    res.url = "http://example.test/x"

    with caplog.at_level(logging.ERROR):
        result = client._safe_json(res)

    assert result == {"detail": "plain text body"}
    assert "Failed to parse JSON response" in caplog.text


def test_safe_json_logs_on_text_failure_too(caplog):
    client = make_client()
    res = MagicMock()
    res.json.side_effect = ValueError("not json")
    type(res).text = property(lambda self: (_ for _ in ()).throw(RuntimeError("no text either")))
    res.url = "http://example.test/x"

    with caplog.at_level(logging.ERROR):
        result = client._safe_json(res)

    assert result == {"detail": "Invalid response from server"}
    assert "Failed to parse JSON response" in caplog.text
    assert "Failed to read response text" in caplog.text


@pytest.mark.asyncio
async def test_close_logs_when_aclose_fails(caplog):
    client = make_client()
    client._client.aclose = AsyncMock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.ERROR):
        await client.close()

    assert "Failed to close the HTTP client cleanly" in caplog.text


@pytest.mark.asyncio
async def test_get_inventory_calls_expected_path():
    client = make_client()
    client._get = AsyncMock(return_value=(200, []))

    status, res = await client.get_inventory(123)

    assert status == 200
    client._get.assert_awaited_once_with("/telegram/inventory", json={"chat_id": "123"})


@pytest.mark.asyncio
async def test_adjust_stock_calls_expected_path():
    client = make_client()
    client._patch = AsyncMock(return_value=(200, {}))

    status, res = await client.adjust_stock(123, "A4 Paper", -50.0)

    assert status == 200
    client._patch.assert_awaited_once_with(
        "/telegram/stock-adjust", json={"chat_id": "123", "item_name": "A4 Paper", "delta": -50.0}
    )


@pytest.mark.asyncio
async def test_create_expense_calls_expected_path():
    client = make_client()
    client._post = AsyncMock(return_value=(201, {"success": True}))

    status, res = await client.create_expense(123, "toner", 15.5, "cartridge")

    assert status == 201
    client._post.assert_awaited_once_with(
        "/telegram/expenses",
        json={"chat_id": "123", "category": "toner", "amount": 15.5, "description": "cartridge"},
    )
