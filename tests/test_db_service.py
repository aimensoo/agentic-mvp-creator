import json

import pytest
from unittest.mock import AsyncMock, MagicMock


def make_mock_pool(conn):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


@pytest.fixture
def mock_conn():
    return AsyncMock()


@pytest.fixture
def db(mock_conn):
    pool = make_mock_pool(mock_conn)
    from services.db_service import DatabaseService

    service = DatabaseService.__new__(DatabaseService)
    service._pool = pool
    service._db_url = "postgresql://test:test@localhost:5432/test"
    return service


async def test_create_job(db, mock_conn):
    fake_id = "550e8400-e29b-41d4-a716-446655440000"
    mock_conn.fetchval.return_value = fake_id

    result = await db.create_job(
        input_text="Build a CRM dashboard",
        input_source="telegram",
        chat_id=12345,
        telegram_user_id=67890,
    )

    assert result == fake_id
    mock_conn.fetchval.assert_called_once()
    call_args = mock_conn.fetchval.call_args
    assert "INSERT INTO pipeline_jobs" in call_args[0][0]
    assert call_args[0][1] == "Build a CRM dashboard"
    assert call_args[0][2] == "telegram"


async def test_get_job_found(db, mock_conn):
    fake_row = {"id": "abc", "status": "queued", "input_text": "t1"}
    mock_conn.fetchrow.return_value = fake_row

    result = await db.get_job("abc")

    assert result == fake_row
    mock_conn.fetchrow.assert_called_once()


async def test_get_job_not_found(db, mock_conn):
    mock_conn.fetchrow.return_value = None

    result = await db.get_job("nonexistent")

    assert result is None


async def test_update_status(db, mock_conn):
    await db.update_status("abc", "drafting_spec")

    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0][0]
    assert "UPDATE pipeline_jobs" in call_args
    assert "status" in call_args
    assert "last_activity_at" in call_args


async def test_update_status_retries_transient_connection_close(db, mock_conn):
    mock_conn.execute.side_effect = [ConnectionError("connection was closed in the middle of operation"), None]

    await db.update_status("abc", "git_push")

    assert mock_conn.execute.call_count == 2


async def test_update_status_does_not_retry_non_transient_error(db, mock_conn):
    mock_conn.execute.side_effect = RuntimeError("syntax error")

    with pytest.raises(RuntimeError, match="syntax error"):
        await db.update_status("abc", "git_push")

    mock_conn.execute.assert_called_once()


async def test_touch_job(db, mock_conn):
    await db.touch_job("abc")

    mock_conn.execute.assert_called_once()
    call_sql = mock_conn.execute.call_args[0][0]
    assert "last_activity_at" in call_sql
    assert "updated_at" in call_sql


async def test_update_job(db, mock_conn):
    await db.update_job(
        "abc",
        spec_text="some spec",
        plan_text="some plan",
    )

    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args[0][0]
    assert "spec_text" in call_args
    assert "plan_text" in call_args


async def test_update_job_encodes_review_result_json(db, mock_conn):
    review_result = {"approved": True, "issues": []}

    await db.update_job("abc", review_result=review_result)

    values = mock_conn.execute.call_args.args
    assert values[2] == json.dumps(review_result)


async def test_update_job_encodes_ci_failure_summary_json(db, mock_conn):
    summary = {"signature": "docker_runtime:backend:container_healthcheck"}

    await db.update_job("abc", ci_failure_summary=summary)

    values = mock_conn.execute.call_args.args
    assert values[2] == json.dumps(summary)


async def test_update_job_encodes_local_failure_summary_json(db, mock_conn):
    summary = {"signature": "pre_push:quality_gate:frontend:placeholder_code"}

    await db.update_job("abc", local_failure_summary=summary)

    values = mock_conn.execute.call_args.args
    assert values[2] == json.dumps(summary)


async def test_get_runnable_jobs(db, mock_conn):
    fake_rows = [{"id": "1", "status": "queued"}, {"id": "2", "status": "preparing_workspace"}]
    mock_conn.fetch.return_value = fake_rows

    result = await db.get_runnable_jobs()

    assert len(result) == 2
    call_sql = mock_conn.fetch.call_args[0][0]
    assert "FOR UPDATE SKIP LOCKED" in call_sql
    assert "loading_request" in call_sql
    assert "coding_in_progress" in call_sql
    assert "local_testing" in call_sql
    assert "ci_testing" in call_sql
    assert "reviewing_code" in call_sql
    assert "fixing" in call_sql
    assert "fixing_in_progress" in call_sql


async def test_save_opencode_session_id(db, mock_conn):
    await db.save_opencode_session_id(
        "abc",
        "session_xyz",
        phase="fixing",
        workspace_path="/workspaces/abc",
    )

    mock_conn.execute.assert_called_once()
    call_sql = mock_conn.execute.call_args[0][0]
    assert "opencode_session_id" in call_sql
    assert "opencode_session_history" in call_sql
    assert "jsonb_build_object" in call_sql
    assert "$2::text" in call_sql
    assert "$3::text" in call_sql
    assert "$4::text" in call_sql
    assert mock_conn.execute.call_args.args[1:] == (
        "abc",
        "session_xyz",
        "fixing",
        "/workspaces/abc",
    )


async def test_get_stale_jobs(db, mock_conn):
    fake_rows = [{"id": "1", "status": "coding", "last_activity_at": "old"}]
    mock_conn.fetch.return_value = fake_rows

    result = await db.get_stale_jobs(1800)

    assert len(result) == 1
    call_sql = mock_conn.fetch.call_args[0][0]
    assert "last_activity_at" in call_sql
    assert mock_conn.fetch.call_args[0][1] == 1800


async def test_update_job_touches_updated_at(db, mock_conn):
    await db.update_job("abc", error_message="fail")

    call_sql = mock_conn.execute.call_args[0][0]
    assert "updated_at" in call_sql


async def test_close(db, mock_conn):
    await db.close()
    db._pool.close.assert_called_once()
