import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.telegram_service import TelegramService


@pytest.fixture
def service():
    return TelegramService(
        bot_token="123456:ABC",
        approval_chat_id="-100",
        escalation_chat_id="-200",
        notification_chat_id="-300",
    )


def _mock_client():
    response = MagicMock()
    response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.aclose = AsyncMock()
    return client


async def test_send_approval_request_sends_summary_and_documents(tmp_path):
    client = _mock_client()
    job = {
        "id": "job-1",
        "workspace_path": str(tmp_path),
        "spec_text": "s" * 1200,
        "plan_text": "p" * 700,
        "request_snapshot": "Build a CRM dashboard",
    }
    (tmp_path / "SPEC.md").write_text("spec file")
    (tmp_path / "PLAN.md").write_text("plan file")
    (tmp_path / "REQUEST.md").write_text("request file")

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_approval_request(job)

    assert client.post.call_count == 4
    message_call = client.post.call_args_list[0]
    assert message_call.args[0] == "/sendMessage"
    assert message_call.kwargs["data"]["chat_id"] == "-100"
    assert "POST /jobs/job-1/approve" in message_call.kwargs["data"]["text"]
    reply_markup = json.loads(message_call.kwargs["data"]["reply_markup"])
    keyboard = reply_markup["inline_keyboard"][0]
    assert keyboard[0]["text"] == "Approve"
    assert keyboard[0]["callback_data"] == "pipeline_approve:job-1"
    assert keyboard[1]["text"] == "Reject"
    assert keyboard[1]["callback_data"] == "pipeline_reject:job-1"
    assert keyboard[2]["text"] == "KILL"
    assert keyboard[2]["callback_data"] == "pipeline_kill:job-1"
    assert "s" * 1000 in message_call.kwargs["data"]["text"]
    assert "p" * 500 in message_call.kwargs["data"]["text"]

    document_calls = client.post.call_args_list[1:]
    assert [call.args[0] for call in document_calls] == [
        "/sendDocument",
        "/sendDocument",
        "/sendDocument",
    ]
    assert document_calls[0].kwargs["files"]["document"][0] == "SPEC.md"
    assert document_calls[1].kwargs["files"]["document"][0] == "PLAN.md"
    assert document_calls[2].kwargs["files"]["document"][0] == "REQUEST.md"


async def test_send_approval_request_uses_job_fields_when_files_are_missing(tmp_path):
    client = _mock_client()
    job = {
        "id": "job-1",
        "workspace_path": str(tmp_path),
        "spec_text": "spec fallback",
        "plan_text": "plan fallback",
        "request_snapshot": "request fallback",
    }

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_approval_request(job)

    assert client.post.call_args_list[1].kwargs["files"]["document"][1] == b"spec fallback"
    assert client.post.call_args_list[2].kwargs["files"]["document"][1] == b"plan fallback"
    assert client.post.call_args_list[3].kwargs["files"]["document"][1] == b"request fallback"


async def test_send_escalation_routes_to_escalation_chat():
    client = _mock_client()
    job = {"id": "job-1", "github_pr_url": "https://github.com/o/r/pull/1"}

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_escalation(job, "review_retries_exceeded", ["issue 1"])

    call = client.post.call_args
    assert call.args[0] == "/sendMessage"
    assert call.kwargs["data"]["chat_id"] == "-200"
    assert "review_retries_exceeded" in call.kwargs["data"]["text"]
    assert "issue 1" in call.kwargs["data"]["text"]
    assert "https://github.com/o/r/pull/1" in call.kwargs["data"]["text"]


async def test_send_escalation_truncates_long_details():
    client = _mock_client()
    job = {"id": "job-1"}

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_escalation(job, "ci_fix_retries_exceeded", ["x" * 10000])

    text = client.post.call_args.kwargs["data"]["text"]
    assert len(text) <= 3900
    assert "[truncated" in text


async def test_send_escalation_humanizes_ci_smoke_failure():
    client = _mock_client()
    job = {
        "id": "c2ab915c-613c-47b4-87be-7ce089d586bd",
        "github_pr_url": "https://github.com/o/r/pull/1",
    }
    details = [
        """
CI: failure (https://github.com/o/r/actions/runs/25763033251)
- docker-runtime: failure
  - Run browser smoke flow: failure

--- logs: docker-runtime ---
browser smoke failure diagnostics
failing_step: smoke_flow[3]
action: open_primary_entity_list
payload: {}
error: SmokeStepError: redirected
current_url: http://localhost:5174/login?redirect=%2Fclients
page_title: Login
body_text_tail:
Login page
localStorage:
- authToken: ***
sessionStorage:
- redirect: /clients
cookies:
- {"domain": "localhost", "name": "session"}
console_errors:
- {"text": "Login failed", "type": "error"}
failed_requests:
- {"event": "requestfailed", "method": "POST", "url": "http://localhost:5174/api/auth/login"}
responses_4xx_5xx:
- {"event": "response", "method": "POST", "status": 401, "url": "http://localhost:5174/api/auth/login"}
recent_network:
- {"event": "request", "method": "GET", "url": "http://localhost:5174/clients"}
html_tail:
<html>...</html>
backend-1 | Mapped {/api/messages, POST} route
""".strip()
    ]

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_escalation(job, "ci_fix_retries_exceeded", details)

    text = client.post.call_args.kwargs["data"]["text"]
    assert text.startswith("Нужна ручная проверка по job c2ab915c-613c-47b4-87be-7ce089d586bd")
    assert "Автопочинка остановлена" in text
    assert "docker-runtime -> Run browser smoke flow: failure" in text
    assert "failing step: smoke_flow[3] (open_primary_entity_list)" in text
    assert "current URL: http://localhost:5174/login?redirect=%2Fclients" in text
    assert "после login браузер остался" in text
    assert "auth flow: login API" in text
    assert "CI run: https://github.com/o/r/actions/runs/25763033251" in text
    assert "PR: https://github.com/o/r/pull/1" in text
    assert "Технический хвост:" in text
    assert "browser smoke failure diagnostics" in text
    assert "Mapped {/api/messages" not in text


async def test_send_done_routes_to_notification_chat():
    client = _mock_client()
    job = {
        "id": "job-1",
        "github_repo": "owner/repo",
        "github_branch": "job_1",
        "github_pr_url": "https://github.com/o/r/pull/1",
        "github_commit": "abc123",
    }

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_done(job)

    call = client.post.call_args
    assert call.kwargs["data"]["chat_id"] == "-300"
    assert "owner/repo" in call.kwargs["data"]["text"]
    assert "abc123" in call.kwargs["data"]["text"]


async def test_send_error_routes_to_escalation_chat():
    client = _mock_client()
    job = {"id": "job-1"}

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_error(job, "git_push", "boom")

    call = client.post.call_args
    assert call.kwargs["data"]["chat_id"] == "-200"
    assert "git_push" in call.kwargs["data"]["text"]
    assert "boom" in call.kwargs["data"]["text"]


async def test_send_done_prefers_source_chat_for_telegram_jobs():
    client = _mock_client()
    job = {
        "id": "job-1",
        "telegram_user_id": 67890,
        "chat_id": 12345,
        "github_repo": "owner/repo",
    }

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_done(job)

    call = client.post.call_args
    assert call.kwargs["data"]["chat_id"] == "12345"


async def test_send_done_falls_back_to_customer_user_when_source_chat_fails():
    request = httpx.Request("POST", "https://api.telegram.org/bottoken/sendMessage")
    forbidden_response = httpx.Response(403, request=request)
    ok_response = MagicMock()
    ok_response.raise_for_status = MagicMock()

    client = AsyncMock()
    client.post = AsyncMock()
    client.post.side_effect = [
        httpx.HTTPStatusError("Forbidden", request=request, response=forbidden_response),
        ok_response,
    ]

    job = {
        "id": "job-1",
        "telegram_user_id": 67890,
        "chat_id": 12345,
        "github_repo": "owner/repo",
    }

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.send_done(job)

    assert client.post.call_args_list[0].kwargs["data"]["chat_id"] == "12345"
    assert client.post.call_args_list[1].kwargs["data"]["chat_id"] == "67890"


async def test_send_done_does_not_use_configured_fallback_for_telegram_jobs():
    request = httpx.Request("POST", "https://api.telegram.org/bottoken/sendMessage")
    forbidden_response = httpx.Response(403, request=request)

    client = AsyncMock()
    client.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("Forbidden", request=request, response=forbidden_response)
    )

    job = {
        "id": "job-1",
        "chat_id": 12345,
        "github_repo": "owner/repo",
    }

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        with pytest.raises(httpx.HTTPStatusError):
            await service.send_done(job)

    assert client.post.call_count == 1
    assert client.post.call_args.kwargs["data"]["chat_id"] == "12345"


async def test_close_closes_http_client():
    client = _mock_client()

    with patch("services.telegram_service.httpx.AsyncClient", return_value=client):
        service = TelegramService("token", "-100", "-200", "-300")
        await service.close()

    client.aclose.assert_called_once()
