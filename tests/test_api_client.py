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
