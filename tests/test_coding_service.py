import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.coding_service import (
    CodingService,
    FIX_PROMPT_TEMPLATE,
    OpenCodeRunState,
    TASK_PROMPT_TEMPLATE,
    _diff_log_fields,
)


@pytest.fixture
def mock_opencode():
    client = AsyncMock()
    client.get_session_messages = AsyncMock(return_value=[])
    client.read_events = AsyncMock(return_value=[])
    return client


@pytest.fixture
def service(mock_opencode):
    return CodingService(mock_opencode, timeout=10)


async def test_run_creates_session_and_sends_prompt(service, mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value="sess-123")
    mock_opencode.send_message = AsyncMock(return_value={"data": {}})
    mock_opencode.get_diff = AsyncMock(return_value=[])

    session_id = await service.run(
        job_id="job-456",
        workspace_path="/workspaces/job-456",
    )

    assert session_id == "sess-123"
    mock_opencode.create_session.assert_called_once_with(
        title="job_job-456",
        workspace_path="/workspaces/job-456",
    )
    mock_opencode.send_message.assert_called_once()
    sent_text = mock_opencode.send_message.call_args[0][1]
    assert "/workspaces/job-456" in sent_text
    assert "SPEC.md" in sent_text
    assert "PLAN.md" in sent_text
    assert "TDD" in sent_text


async def test_start_creates_session_and_dispatches_async_prompt(service, mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value="sess-123")
    mock_opencode.send_prompt_async = AsyncMock()

    session_id = await service.start(
        job_id="job-456",
        workspace_path="/workspaces/job-456",
    )

    assert session_id == "sess-123"
    mock_opencode.create_session.assert_called_once_with(
        title="job_job-456",
        workspace_path="/workspaces/job-456",
    )
    mock_opencode.send_prompt_async.assert_called_once()
    sent_text = mock_opencode.send_prompt_async.call_args[0][1]
    assert "/workspaces/job-456" in sent_text
    assert "SPEC.md" in sent_text
    assert "PLAN.md" in sent_text


async def test_is_complete_returns_false_when_session_busy(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[])

    result = await service.is_complete("job-1", "sess-1")

    assert result is False
    mock_opencode.get_diff.assert_called_once_with("sess-1")


async def test_is_complete_overrides_stuck_busy_status_after_stable_diff(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[{"file": "main.py", "changes": ["x"]}])

    assert await service.is_complete("job-1", "sess-1") is False

    now = 1029.0
    assert await service.is_complete("job-1", "sess-1") is False

    now = 1030.0
    assert await service.is_complete("job-1", "sess-1") is True


async def test_is_complete_resets_busy_observation_when_diff_changes(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(
        side_effect=[
            [{"file": "main.py", "changes": ["x"]}],
            [{"file": "main.py", "changes": ["x", "y"]}],
            [{"file": "main.py", "changes": ["x", "y"]}],
        ]
    )

    assert await service.is_complete("job-1", "sess-1") is False

    now = 1029.0
    assert await service.is_complete("job-1", "sess-1") is False

    now = 1058.0
    assert await service.is_complete("job-1", "sess-1") is False


async def test_is_complete_resets_busy_observation_when_messages_change(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    message_a = [{"info": {"id": "msg-a", "role": "assistant"}, "parts": [{"type": "text", "text": "Starting"}]}]
    message_b = [{"info": {"id": "msg-b", "role": "assistant"}, "parts": [{"type": "tool", "tool": "bash"}]}]
    mock_opencode.get_session_messages = AsyncMock(
        side_effect=[
            message_a,
            message_a,
            message_b,
            message_b,
            message_b,
            message_b,
        ]
    )

    assert await service.is_complete("job-1", "sess-1") is False

    now = 1029.0
    assert await service.is_complete("job-1", "sess-1") is False

    now = 1058.0
    assert await service.is_complete("job-1", "sess-1") is False


async def test_check_state_returns_stuck_when_busy_empty_diff_is_stable_too_long(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        stuck_seconds=60,
        max_empty_diff_followups=0,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[])

    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1059.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1060.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.STUCK


async def test_check_state_sends_followup_when_busy_empty_diff_is_stable(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        stuck_seconds=600,
        empty_diff_followup_min_stable_seconds=500,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.send_prompt_async = AsyncMock()

    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1499.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING
    mock_opencode.send_prompt_async.assert_not_called()

    now = 1500.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    mock_opencode.send_prompt_async.assert_called_once()
    session_id, prompt = mock_opencode.send_prompt_async.call_args.args
    assert session_id == "sess-1"
    assert "Continue the current task now" in prompt


async def test_check_state_caps_empty_diff_followups(mock_opencode):
    now = 1000.0

    def clock():
        return now

    service = CodingService(
        mock_opencode,
        timeout=10,
        busy_stable_seconds=30,
        stuck_seconds=100,
        empty_diff_followup_min_stable_seconds=30,
        clock=clock,
    )
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.send_prompt_async = AsyncMock()

    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1030.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1035.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1040.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.RUNNING

    now = 1100.0
    assert await service.check_state("job-1", "sess-1") == OpenCodeRunState.STUCK
    mock_opencode.send_prompt_async.assert_called_once()


def test_diff_log_fields_count_patch_size_and_changes():
    fields = _diff_log_fields(
        [
            {
                "file": "src/app.js",
                "patch": (
                    "--- a/src/app.js\n"
                    "+++ b/src/app.js\n"
                    "@@ -1,3 +1,4 @@\n"
                    " const a = 1;\n"
                    "-const b = 2;\n"
                    "+const b = 3;\n"
                    "+const c = 4;\n"
                ),
            }
        ]
    )

    assert fields["files_changed"] == 1
    assert fields["diff_insertions"] == 2
    assert fields["diff_deletions"] == 1
    assert fields["diff_patch_chars"] > 0
    assert fields["diff_payload_chars"] > fields["diff_patch_chars"]
    assert len(fields["diff_signature"]) == 12


async def test_check_state_detects_waiting_permission_from_session_messages(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "id": "msg-1",
                "parts": [
                    {
                        "type": "text",
                        "text": "Permission required: Access external directory /tmp",
                    }
                ],
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.WAITING_PERMISSION
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_detects_waiting_permission_from_pending_env_read(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "id": "msg-1",
                "parts": [
                    {
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "running",
                            "input": {"filePath": "/workspaces/job-1/.env"},
                        },
                    }
                ],
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.WAITING_PERMISSION
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_auto_denies_sensitive_env_read_permission_event(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(return_value=[])
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "permission.updated",
                "data": {
                    "sessionID": "sess-1",
                    "id": "perm-1",
                    "type": "read",
                    "pattern": "/workspaces/job-1/.env",
                    "title": "Read /workspaces/job-1/.env",
                },
            }
        ]
    )
    mock_opencode.reply_permission = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.reply_permission.assert_called_once_with(
        "sess-1",
        "perm-1",
        response="reject",
        remember=False,
    )
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_auto_denies_env_read_even_when_message_shows_pending_tool(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "id": "msg-1",
                "parts": [
                    {
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "running",
                            "input": {"filePath": "/workspaces/job-1/.env.local"},
                        },
                    }
                ],
            }
        ]
    )
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "permission.updated",
                "data": {
                    "sessionID": "sess-1",
                    "id": "perm-1",
                    "type": "read",
                    "pattern": "/workspaces/job-1/.env.local",
                    "title": "Read /workspaces/job-1/.env.local",
                },
            }
        ]
    )
    mock_opencode.reply_permission = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.reply_permission.assert_called_once()
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_does_not_auto_deny_env_example_permission(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(return_value=[])
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "permission.updated",
                "data": {
                    "sessionID": "sess-1",
                    "id": "perm-1",
                    "type": "read",
                    "pattern": "/workspaces/job-1/.env.example",
                    "title": "Read /workspaces/job-1/.env.example",
                },
            }
        ]
    )
    mock_opencode.reply_permission = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.WAITING_PERMISSION
    mock_opencode.reply_permission.assert_not_called()
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_ignores_finished_env_read(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "id": "msg-1",
                "parts": [
                    {
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "error",
                            "input": {"filePath": "/workspaces/job-1/.env"},
                        },
                    }
                ],
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock(return_value=[])

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.get_diff.assert_called_once_with("sess-1")


async def test_check_state_detects_waiting_input_from_permission_event(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "permission.updated",
                "data": {
                    "sessionID": "sess-1",
                    "id": "perm-1",
                    "type": "question",
                    "title": "Need confirmation",
                },
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.WAITING_INPUT
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_detects_waiting_permission_from_nested_event_properties(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "message",
                "data": {
                    "type": "permission.updated",
                    "properties": {
                        "sessionID": "sess-1",
                        "id": "perm-1",
                        "type": "external_directory",
                    },
                },
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.WAITING_PERMISSION
    mock_opencode.get_diff.assert_not_called()


async def test_check_state_ignores_old_permission_text_in_message_history(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {"parts": [{"type": "text", "text": "Permission required: Access external directory /tmp"}]},
            {"parts": [{"type": "text", "text": "continued"}]},
            {"parts": [{"type": "text", "text": "continued"}]},
            {"parts": [{"type": "text", "text": "still working"}]},
        ]
    )
    mock_opencode.get_diff = AsyncMock(return_value=[])

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.get_diff.assert_called_once_with("sess-1")


async def test_check_state_ignores_waiting_event_for_other_session(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.read_events = AsyncMock(
        return_value=[
            {
                "event": "permission.updated",
                "data": {
                    "sessionID": "other-session",
                    "id": "perm-1",
                    "type": "external_directory",
                },
            }
        ]
    )
    mock_opencode.get_diff = AsyncMock(return_value=[])

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.get_diff.assert_called_once_with("sess-1")


async def test_is_complete_keeps_waiting_when_busy_diff_check_fails(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=True)
    mock_opencode.get_diff = AsyncMock(side_effect=RuntimeError("diff unavailable"))

    result = await service.is_complete("job-1", "sess-1")

    assert result is False


async def test_is_complete_fetches_diff_when_session_idle(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[{"file": "main.py"}])

    result = await service.is_complete("job-1", "sess-1")

    assert result is True
    mock_opencode.get_diff.assert_called_once_with("sess-1")


async def test_abort_delegates_to_opencode_client(service, mock_opencode):
    mock_opencode.abort_session = AsyncMock()

    await service.abort("sess-1")

    mock_opencode.abort_session.assert_called_once_with("sess-1")


async def test_run_returns_session_id(service, mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value="sess-xyz")
    mock_opencode.send_message = AsyncMock(return_value={})
    mock_opencode.get_diff = AsyncMock(return_value=[])

    result = await service.run(job_id="j1", workspace_path="/workspaces/j1")

    assert result == "sess-xyz"


async def test_fix_sends_issues_to_same_session(service, mock_opencode):
    mock_opencode.send_message = AsyncMock(return_value={})

    issues = [
        {"severity": "critical", "description": "No error handling"},
        {"severity": "minor", "description": "Bad naming"},
    ]
    await service.fix(
        session_id="sess-existing",
        issues=issues,
        context="pytest output:\nFAILED test_x",
    )

    mock_opencode.send_message.assert_called_once()
    sent_text = mock_opencode.send_message.call_args[0][1]
    assert "sess-existing" == mock_opencode.send_message.call_args[0][0]
    assert "critical" in sent_text
    assert "No error handling" in sent_text
    assert "minor" in sent_text
    assert "pytest output" in sent_text
    assert "context.md" in sent_text


async def test_start_fix_creates_new_session_and_dispatches_async_prompt(service, mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value="sess-fix")
    mock_opencode.send_prompt_async = AsyncMock()

    session_id = await service.start_fix(
        job_id="job-1",
        workspace_path="/workspaces/job-1",
        issues=[{"severity": "major", "description": "Dead button"}],
        context="review failed",
    )

    assert session_id == "sess-fix"
    mock_opencode.create_session.assert_called_once_with(
        title="job_job-1_fix",
        workspace_path="/workspaces/job-1",
    )
    mock_opencode.send_prompt_async.assert_called_once()
    sent_text = mock_opencode.send_prompt_async.call_args.args[1]
    assert "/workspaces/job-1" in sent_text
    assert "Dead button" in sent_text
    assert "review failed" in sent_text


async def test_start_fix_includes_structured_ci_issue_guidance(service, mock_opencode):
    mock_opencode.create_session = AsyncMock(return_value="sess-fix")
    mock_opencode.send_prompt_async = AsyncMock()

    await service.start_fix(
        job_id="job-1",
        workspace_path="/workspaces/job-1",
        issues=[
            {
                "severity": "critical",
                "code": "protected_route_auth_redirect_after_login",
                "description": "Login succeeds but /clients redirects back to login.",
                "path": "mvp.config.json",
                "evidence": ["POST /api/auth/login returned 201", "localStorage has salesflow_token"],
                "related_checks": ["Inspect middleware.ts cookie key", "Inspect auth.ts token storage"],
                "acceptance_checks": ["Run Playwright login -> /clients"],
            }
        ],
        context="browser smoke failure diagnostics",
    )

    sent_text = mock_opencode.send_prompt_async.call_args.args[1]
    assert "protected_route_auth_redirect_after_login" in sent_text
    assert "Path: mvp.config.json" in sent_text
    assert "Evidence:" in sent_text
    assert "POST /api/auth/login returned 201" in sent_text
    assert "Related checks:" in sent_text
    assert "Inspect middleware.ts cookie key" in sent_text
    assert "Acceptance checks:" in sent_text
    assert "Run Playwright login -> /clients" in sent_text
    assert "Treat any issue acceptance checks as mandatory" in sent_text


async def test_fix_without_context(service, mock_opencode):
    mock_opencode.send_message = AsyncMock(return_value={})

    await service.fix(session_id="sess-1", issues=[], context="")

    mock_opencode.send_message.assert_called_once()


async def test_run_timeout_aborts_session(service, mock_opencode):
    import asyncio

    mock_opencode.create_session = AsyncMock(return_value="sess-t")
    mock_opencode.send_message = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_opencode.abort_session = AsyncMock()

    with pytest.raises(RuntimeError, match="timeout"):
        await service.run(job_id="j1", workspace_path="/w")

    mock_opencode.abort_session.assert_called_once_with("sess-t")


async def test_fix_timeout_aborts_session(service, mock_opencode):
    import asyncio

    mock_opencode.send_message = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_opencode.abort_session = AsyncMock()

    with pytest.raises(RuntimeError, match="timeout"):
        await service.fix(session_id="sess-t", issues=[], context="")

    mock_opencode.abort_session.assert_called_once_with("sess-t")


def test_task_prompt_template_contains_required_sections():
    assert "SPEC.md" in TASK_PROMPT_TEMPLATE
    assert "PLAN.md" in TASK_PROMPT_TEMPLATE
    assert "REQUEST.md" in TASK_PROMPT_TEMPLATE
    assert "Generate a root `README.md`" in TASK_PROMPT_TEMPLATE
    assert "short plain-language description" in TASK_PROMPT_TEMPLATE
    assert "exact setup and run commands" in TASK_PROMPT_TEMPLATE
    assert "demo login credentials" in TASK_PROMPT_TEMPLATE
    assert "TDD" in TASK_PROMPT_TEMPLATE
    assert "BLOCKERS.md" in TASK_PROMPT_TEMPLATE
    assert "PLAN.md`'s Backend API Contract as authoritative" in TASK_PROMPT_TEMPLATE
    assert "Do not invent alternative backend routes" in TASK_PROMPT_TEMPLATE
    assert "expect_request` method/path/status must match" in TASK_PROMPT_TEMPLATE
    assert "dev-docs" in TASK_PROMPT_TEMPLATE
    assert "honest runnable MVP" in TASK_PROMPT_TEMPLATE
    assert "Every visible button/control" in TASK_PROMPT_TEMPLATE
    assert "basic product-quality bar" in TASK_PROMPT_TEMPLATE
    assert "shared UI primitives" in TASK_PROMPT_TEMPLATE
    assert "overlapping text" in TASK_PROMPT_TEMPLATE
    assert "Docker as part of the deliverable" in TASK_PROMPT_TEMPLATE
    assert ".pipeline/docker-ci.sh" in TASK_PROMPT_TEMPLATE
    assert "freezes `.pipeline/docker-ci.sh` after the first push" in TASK_PROMPT_TEMPLATE
    assert "Never commit a real `.env`" in TASK_PROMPT_TEMPLATE
    assert "Do not read real `.env`" in TASK_PROMPT_TEMPLATE
    assert "copy `.env.example` to `.env`" in TASK_PROMPT_TEMPLATE
    assert "docker compose up -d" in TASK_PROMPT_TEMPLATE
    assert "frontend/backend integration" in TASK_PROMPT_TEMPLATE
    assert "node_modules/" in TASK_PROMPT_TEMPLATE
    assert "Do not read or write `/tmp`" in TASK_PROMPT_TEMPLATE
    assert "If a Node service uses Prisma" in TASK_PROMPT_TEMPLATE
    assert "Do not invent tags such as `postgres:16-slim`" in TASK_PROMPT_TEMPLATE
    assert "version: 2" in TASK_PROMPT_TEMPLATE
    assert "data-testid" in TASK_PROMPT_TEMPLATE
    assert "expect_request" in TASK_PROMPT_TEMPLATE
    assert "wait_for_outcome" in TASK_PROMPT_TEMPLATE
    assert "mock/demo/local provider" in TASK_PROMPT_TEMPLATE
    assert "without real secrets" in TASK_PROMPT_TEMPLATE
    assert "{workspace_path}" in TASK_PROMPT_TEMPLATE


def test_fix_prompt_template_contains_required_sections():
    assert "{workspace_path}" in FIX_PROMPT_TEMPLATE
    assert "context.md" in FIX_PROMPT_TEMPLATE
    assert "visible control" in FIX_PROMPT_TEMPLATE
    assert "low-quality" in FIX_PROMPT_TEMPLATE
    assert "Docker runtime checks" in FIX_PROMPT_TEMPLATE
    assert "Prisma/OpenSSL/libssl errors" in FIX_PROMPT_TEMPLATE
    assert "manifest unknown" in FIX_PROMPT_TEMPLATE
    assert "Keep the root `README.md` accurate" in FIX_PROMPT_TEMPLATE
    assert "Keep `.pipeline/docker-ci.sh` aligned" in FIX_PROMPT_TEMPLATE
    assert "real `.env` stays untracked" in FIX_PROMPT_TEMPLATE
    assert "Do not read real `.env`" in FIX_PROMPT_TEMPLATE
    assert "external API integrations CI-safe" in FIX_PROMPT_TEMPLATE
    assert "REPLICATE_API_TOKEN" in FIX_PROMPT_TEMPLATE
    assert "Do not read or write `/tmp`" in FIX_PROMPT_TEMPLATE
    assert "/login?redirect=..." in FIX_PROMPT_TEMPLATE
    assert "Fix the auth flow" in FIX_PROMPT_TEMPLATE
    assert "frontend_empty_terminal_dom" in FIX_PROMPT_TEMPLATE
    assert "browser smoke diagnostics" in FIX_PROMPT_TEMPLATE
    assert "Backend API Contract" in FIX_PROMPT_TEMPLATE
    assert "do not change endpoint methods, paths, request/response shapes" in FIX_PROMPT_TEMPLATE
    assert "expect_request" in FIX_PROMPT_TEMPLATE
    assert "wait_for_outcome" in FIX_PROMPT_TEMPLATE
    assert "{issues_text}" in FIX_PROMPT_TEMPLATE


# --- EMPTY_RESULT detection ---


async def test_check_state_returns_empty_result_when_idle_no_messages(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(return_value=[])

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.EMPTY_RESULT


async def test_check_state_returns_model_error_when_idle_model_error_in_messages(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "role": "assistant",
                "parts": [{"type": "text", "text": "Model big-pickle not supported for format anthropic"}],
            },
        ]
    )

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.MODEL_ERROR


async def test_check_state_returns_model_error_when_free_model_promotion_ended(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "role": "assistant",
                "error": {
                    "name": "APIError",
                    "data": {
                        "message": (
                            "Free promotion has ended for MiniMax M3 Free. "
                            "You can continue using the model by subscribing to OpenCode Go."
                        )
                    },
                },
            },
        ]
    )

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.MODEL_ERROR


async def test_check_state_completes_when_idle_empty_diff_but_messages_present(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {"role": "user", "parts": [{"type": "text", "text": "Fix the bug"}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "Looks good already."}]},
        ]
    )
    mock_opencode.send_prompt_async = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.COMPLETED
    mock_opencode.send_prompt_async.assert_not_called()


async def test_check_state_waits_when_idle_empty_diff_has_only_user_prompt(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {
                "info": {"role": "user"},
                "parts": [{"type": "text", "text": "Build the MVP"}],
            },
        ]
    )
    mock_opencode.send_prompt_async = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    mock_opencode.send_prompt_async.assert_not_called()


async def test_check_state_does_not_use_followup_for_idle_empty_diff(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(
        return_value=[
            {"role": "user", "parts": [{"type": "text", "text": "Fix the bug"}]},
            {"role": "assistant", "parts": [{"type": "text", "text": "No code change needed."}]},
        ]
    )
    mock_opencode.send_prompt_async = AsyncMock()

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.COMPLETED
    mock_opencode.send_prompt_async.assert_not_called()


async def test_check_state_returns_completed_when_idle_with_diff(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[{"file": "src/App.tsx", "patch": "@@ ..."}])

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.COMPLETED
    mock_opencode.get_session_messages.assert_not_called()


async def test_check_state_empty_result_when_message_check_fails(service, mock_opencode):
    mock_opencode.is_session_busy = AsyncMock(return_value=False)
    mock_opencode.get_diff = AsyncMock(return_value=[])
    mock_opencode.get_session_messages = AsyncMock(side_effect=RuntimeError("API down"))

    state = await service.check_state("job-1", "sess-1")

    assert state == OpenCodeRunState.RUNNING
    assert "{context}" in FIX_PROMPT_TEMPLATE
