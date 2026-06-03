import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from services.opencode_client import OpenCodeClient, _SseParser


@pytest.fixture
def client():
    return OpenCodeClient(
        base_url="http://localhost:4096",
        password="secret123",
    )


async def test_health(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "ok"}
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.health()
    assert result == {"status": "ok"}


async def test_create_session(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"id": "sess-abc"}}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        session_id = await client.create_session(title="job_123")

    assert session_id == "sess-abc"
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "/session"
    body = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
    assert body == {"title": "job_123"}


async def test_create_session_sets_workspace_directory(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"id": "sess-abc"}}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        session_id = await client.create_session(title="job_123", workspace_path="/workspaces/job-123")

    assert session_id == "sess-abc"
    assert mock_post.call_args.kwargs["params"] == {"directory": "/workspaces/job-123"}
    assert mock_post.call_args.kwargs["json"] == {"title": "job_123"}


async def test_create_session_retries_body_directory_for_older_api(client):
    request = httpx.Request("POST", "http://localhost:4096/session")
    bad_response = httpx.Response(422, request=request)
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError("bad payload", request=request, response=bad_response)

    good_resp = MagicMock()
    good_resp.json.return_value = {"data": {"id": "sess-abc"}}
    good_resp.raise_for_status = MagicMock()

    with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=[bad_resp, good_resp]) as mock_post:
        session_id = await client.create_session(title="job_123", workspace_path="/workspaces/job-123")

    assert session_id == "sess-abc"
    assert mock_post.call_args_list[0].kwargs["json"] == {
        "title": "job_123",
    }
    assert mock_post.call_args_list[0].kwargs["params"] == {
        "directory": "/workspaces/job-123",
    }
    assert mock_post.call_args_list[1].kwargs["json"] == {
        "title": "job_123",
        "directory": "/workspaces/job-123",
    }


async def test_create_session_retries_workspace_path_field_for_legacy_api(client):
    request = httpx.Request("POST", "http://localhost:4096/session")
    bad_response = httpx.Response(422, request=request)
    bad_resp = MagicMock()
    bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError("bad payload", request=request, response=bad_response)

    second_bad_resp = MagicMock()
    second_bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "bad payload",
        request=request,
        response=bad_response,
    )

    good_resp = MagicMock()
    good_resp.json.return_value = {"data": {"id": "sess-abc"}}
    good_resp.raise_for_status = MagicMock()

    with patch.object(
        client._client,
        "post",
        new_callable=AsyncMock,
        side_effect=[bad_resp, second_bad_resp, good_resp],
    ) as mock_post:
        session_id = await client.create_session(title="job_123", workspace_path="/workspaces/job-123")

    assert session_id == "sess-abc"
    assert mock_post.call_args_list[2].kwargs["json"] == {
        "title": "job_123",
        "path": "/workspaces/job-123",
    }


async def test_create_session_supports_top_level_session_shape(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"id": "sess-top-level"}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp):
        session_id = await client.create_session(title="job_123")

    assert session_id == "sess-top-level"


async def test_send_message(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"content": "done"}}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        result = await client.send_message("sess-abc", "Hello agent")

    assert result["data"]["content"] == "done"
    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "/session/sess-abc/message"
    body = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
    assert body == {"parts": [{"type": "text", "text": "Hello agent"}]}


async def test_send_prompt_async(client):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        await client.send_prompt_async("sess-abc", "Hello agent")

    call_kwargs = mock_post.call_args
    assert call_kwargs[0][0] == "/session/sess-abc/prompt_async"
    body = call_kwargs[1].get("json") or call_kwargs.kwargs.get("json")
    assert body == {"parts": [{"type": "text", "text": "Hello agent"}]}


async def test_get_session_statuses(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"sess-abc": {"type": "busy"}}}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.get_session_statuses()

    assert result == {"sess-abc": {"type": "busy"}}


async def test_is_session_busy(client):
    client.get_session_statuses = AsyncMock(return_value={"sess-abc": {"type": "busy"}})

    assert await client.is_session_busy("sess-abc") is True


async def test_is_session_busy_returns_false_when_status_absent(client):
    client.get_session_statuses = AsyncMock(return_value={})

    assert await client.is_session_busy("sess-abc") is False


async def test_get_diff(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": [{"file": "main.py", "changes": []}]}
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.get_diff("sess-abc")
    assert len(result) == 1
    assert result[0]["file"] == "main.py"


async def test_get_diff_supports_top_level_array_shape(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = [{"file": "main.py", "changes": []}]
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.get_diff("sess-abc")
    assert len(result) == 1
    assert result[0]["file"] == "main.py"


async def test_get_session_messages_supports_wrapped_shape(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": {"messages": [{"id": "msg-1"}]}}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
        result = await client.get_session_messages("sess-abc")

    assert result == [{"id": "msg-1"}]
    assert mock_get.call_args[0][0] == "/session/sess-abc/message"


async def test_reply_permission(client):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"data": True}
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        result = await client.reply_permission(
            "sess-abc",
            "perm-1",
            response="reject",
            remember=False,
        )

    assert result is True
    assert mock_post.call_args[0][0] == "/session/sess-abc/permissions/perm-1"
    assert mock_post.call_args.kwargs["json"] == {"response": "reject", "remember": False}


def test_sse_parser_parses_json_event():
    parser = _SseParser()

    assert parser.feed("event: permission.updated") is None
    assert parser.feed('data: {"sessionID":"sess-abc","type":"external_directory"}') is None
    event = parser.feed("")

    assert event == {
        "event": "permission.updated",
        "data": {"sessionID": "sess-abc", "type": "external_directory"},
    }


async def test_abort_session(client):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        await client.abort_session("sess-abc")

    assert mock_post.call_args[0][0] == "/session/sess-abc/abort"


async def test_delete_session(client):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(client._client, "delete", new_callable=AsyncMock, return_value=mock_resp) as mock_delete:
        await client.delete_session("sess-abc")

    assert mock_delete.call_args[0][0] == "/session/sess-abc"


async def test_close(client):
    with patch.object(client._client, "aclose", new_callable=AsyncMock):
        await client.close()
        client._client.aclose.assert_called_once()
