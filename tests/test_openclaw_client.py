import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from services.openclaw_client import OpenClawClient


@pytest.fixture
def client():
    return OpenClawClient(
        base_url="http://localhost:11434",
        api_key="test-key",
        model="test-model",
    )


async def test_chat_success(client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": "Generated text"}}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
        result = await client.chat("system prompt", "user message")

    assert result == "Generated text"


async def test_chat_sends_correct_payload(client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        await client.chat("sys", "usr", temperature=0.5)

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["model"] == "test-model"
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "sys"
        assert body["messages"][1]["role"] == "user"
        assert body["messages"][1]["content"] == "usr"
        assert body["temperature"] == 0.5


async def test_chat_raises_on_connect_error(client):
    with patch.object(
        client._client,
        "post",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        with pytest.raises(RuntimeError, match="OpenClaw"):
            await client.chat("sys", "usr")


async def test_chat_raises_on_timeout(client):
    with patch.object(
        client._client,
        "post",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("timed out"),
    ):
        with pytest.raises(RuntimeError, match="OpenClaw"):
            await client.chat("sys", "usr")


async def test_chat_retries_on_429(client):
    error_response = MagicMock()
    error_response.status_code = 429
    http_error = httpx.HTTPStatusError("rate limited", request=MagicMock(), response=error_response)

    success_response = MagicMock()
    success_response.json.return_value = {"choices": [{"message": {"content": "retry ok"}}]}
    success_response.raise_for_status = MagicMock()

    with (
        patch.object(
            client._client,
            "post",
            new_callable=AsyncMock,
            side_effect=[http_error, success_response],
        ),
        patch("services.openclaw_client.asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await client.chat("sys", "usr")

    assert result == "retry ok"


async def test_chat_raises_on_non_429_http_error(client):
    error_response = MagicMock()
    error_response.status_code = 500
    http_error = httpx.HTTPStatusError("server error", request=MagicMock(), response=error_response)

    with patch.object(
        client._client,
        "post",
        new_callable=AsyncMock,
        side_effect=http_error,
    ):
        with pytest.raises(RuntimeError, match="OpenClaw"):
            await client.chat("sys", "usr")


async def test_close(client):
    with patch.object(client._client, "aclose", new_callable=AsyncMock):
        await client.close()
        client._client.aclose.assert_called_once()
