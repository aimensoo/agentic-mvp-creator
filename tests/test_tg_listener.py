import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _load_listener(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC")
    monkeypatch.setenv("BACKEND_API_URL", "http://backend/api/v1")
    monkeypatch.setenv("WEBHOOK_SECRET", "secret")

    path = Path(__file__).resolve().parents[1] / "telegram_bot" / "listener.py"
    spec = importlib.util.spec_from_file_location("telegram_bot_listener_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_pipeline_callback(monkeypatch):
    listener = _load_listener(monkeypatch)

    assert listener._parse_pipeline_callback("pipeline_approve:job-1") == ("approve", "job-1")
    assert listener._parse_pipeline_callback("pipeline_reject:job-1") == ("reject", "job-1")
    assert listener._parse_pipeline_callback("pipeline_kill:job-1") == ("kill", "job-1")


async def test_send_pipeline_action_posts_customer_user_id(monkeypatch):
    listener = _load_listener(monkeypatch)
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"status": "approved"}

    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(listener.httpx, "AsyncClient", return_value=client):
        result = await listener.send_pipeline_action(
            job_id="job-1",
            action="approve",
            telegram_user_id=67890,
        )

    assert result == {"status": "approved"}
    client.post.assert_called_once_with(
        "/jobs/job-1/approve",
        json={"telegram_user_id": 67890},
    )


async def test_send_pipeline_feedback_posts_feedback(monkeypatch):
    listener = _load_listener(monkeypatch)
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"status": "replan_queued"}

    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(listener.httpx, "AsyncClient", return_value=client):
        result = await listener.send_pipeline_feedback(
            job_id="job-1",
            feedback="fix the plan",
            telegram_user_id=67890,
        )

    assert result == {"status": "replan_queued"}
    client.post.assert_called_once_with(
        "/jobs/job-1/feedback",
        json={"telegram_user_id": 67890, "feedback": "fix the plan"},
    )


async def test_pipeline_approve_callback_edits_message(monkeypatch):
    listener = _load_listener(monkeypatch)
    query = MagicMock()
    query.message.text = "Manual approval required"
    query.edit_message_text = AsyncMock()

    with patch.object(
        listener,
        "send_pipeline_action",
        new=AsyncMock(return_value={"status": "approved"}),
    ) as send_pipeline_action:
        await listener.handle_pipeline_action_callback(
            query=query,
            callback_data="pipeline_approve:job-1",
            telegram_user_id=67890,
        )

    send_pipeline_action.assert_called_once_with(
        job_id="job-1",
        action="approve",
        telegram_user_id=67890,
    )
    query.edit_message_text.assert_called_once()
    assert "Pipeline approve: approved" in query.edit_message_text.call_args.kwargs["text"]


async def test_pipeline_reject_callback_requests_feedback(monkeypatch):
    listener = _load_listener(monkeypatch)
    query = MagicMock()
    query.message.text = "Manual approval required"
    query.message.chat.id = 123
    query.edit_message_text = AsyncMock()

    with patch.object(
        listener,
        "send_pipeline_action",
        new=AsyncMock(return_value={"status": "feedback_requested"}),
    ):
        await listener.handle_pipeline_action_callback(
            query=query,
            callback_data="pipeline_reject:job-1",
            telegram_user_id=67890,
        )

    assert listener.PENDING_REJECT_FEEDBACK[(123, 67890)] == "job-1"
    assert "Please send" in query.edit_message_text.call_args.kwargs["text"]


async def test_pending_feedback_message_requeues_plan(monkeypatch):
    listener = _load_listener(monkeypatch)
    update = MagicMock()
    update.message.text = "Переделай API контракт"
    update.message.chat.id = 123
    update.message.from_user.id = 67890
    update.message.reply_text = AsyncMock()
    listener.PENDING_REJECT_FEEDBACK[(123, 67890)] = "job-1"

    with patch.object(
        listener,
        "send_pipeline_feedback",
        new=AsyncMock(return_value={"status": "replan_queued"}),
    ) as send_pipeline_feedback:
        await listener.handle_text_message(update, MagicMock())

    send_pipeline_feedback.assert_called_once_with(
        job_id="job-1",
        feedback="Переделай API контракт",
        telegram_user_id=67890,
    )
    assert (123, 67890) not in listener.PENDING_REJECT_FEEDBACK
    assert "Feedback accepted" in update.message.reply_text.call_args.args[0]


async def test_new_text_message_starts_pipeline(monkeypatch):
    listener = _load_listener(monkeypatch)
    update = MagicMock()
    update.message.text = "Build a CRM dashboard"
    update.message.chat.id = 123
    update.message.from_user.id = 67890
    update.message.reply_text = AsyncMock()

    with patch.object(
        listener,
        "trigger_pipeline",
        new=AsyncMock(return_value="job-1"),
    ) as trigger_pipeline:
        await listener.handle_text_message(update, MagicMock())

    trigger_pipeline.assert_called_once_with(
        user_request="Build a CRM dashboard",
        chat_id=123,
        telegram_user_id=67890,
    )
    assert "Pipeline job queued: job-1" in update.message.reply_text.call_args.args[0]


async def test_trigger_pipeline_posts_user_request(monkeypatch):
    listener = _load_listener(monkeypatch)
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"job_id": "job-1"}

    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(listener.httpx, "AsyncClient", return_value=client):
        result = await listener.trigger_pipeline(
            user_request="Build a CRM dashboard",
            chat_id=123,
            telegram_user_id=67890,
        )

    assert result == "job-1"
    client.post.assert_called_once_with(
        "/webhook/trigger",
        json={
            "user_request": "Build a CRM dashboard",
            "input_source": "telegram",
            "chat_id": 123,
            "telegram_user_id": 67890,
        },
        headers={"X-Webhook-Secret": "secret"},
    )
