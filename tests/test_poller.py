import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from background.poller import next_status, poll_loop


def test_next_status():
    assert next_status("queued") == "loading_request"
    assert next_status("loading_request") == "loading_request"
    assert next_status("drafting_spec") == "drafting_spec"
    assert next_status("planning") == "planning"
    assert next_status("preparing_workspace") == "coding"
    assert next_status("coding") == "coding"
    assert next_status("coding_in_progress") == "coding_in_progress"
    assert next_status("local_testing") == "local_testing"
    assert next_status("git_push") == "git_push"
    assert next_status("ci_testing") == "ci_testing"
    assert next_status("reviewing_code") == "reviewing_code"
    assert next_status("fixing") == "fixing"
    assert next_status("fixing_in_progress") == "fixing_in_progress"


def test_next_status_rejects_unknown_status():
    with pytest.raises(ValueError, match="Unsupported runnable status"):
        next_status("done")


async def test_poll_loop_claims_jobs_and_starts_runner():
    db = AsyncMock()
    db.get_runnable_jobs.return_value = [
        {"id": "job-1", "status": "queued"},
        {"id": "job-2", "status": "preparing_workspace"},
        {"id": "job-3", "status": "coding_in_progress"},
    ]
    runner = AsyncMock()

    with patch("background.poller.asyncio.sleep", side_effect=asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await poll_loop(db, runner, poll_interval=0)

    assert db.update_status.call_args_list[0].args == ("job-1", "loading_request")
    assert db.update_status.call_args_list[1].args == ("job-2", "coding")
    assert db.update_status.call_count == 2

    await asyncio.sleep(0)
    runner.run.assert_any_call("job-1")
    runner.run.assert_any_call("job-2")
    runner.run.assert_any_call("job-3")


async def test_poll_loop_skips_job_that_is_already_active():
    db = AsyncMock()
    db.get_runnable_jobs.return_value = [
        {"id": "job-1", "status": "local_testing"},
    ]

    blocker = asyncio.Event()
    runner = AsyncMock()

    async def run_until_cancelled(job_id):
        await blocker.wait()

    runner.run.side_effect = run_until_cancelled
    real_sleep = asyncio.sleep
    sleep_calls = 0

    async def fake_sleep(_):
        nonlocal sleep_calls
        sleep_calls += 1
        await real_sleep(0)
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    with patch("background.poller.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await poll_loop(db, runner, poll_interval=0)

    runner.run.assert_called_once_with("job-1")


async def test_poll_loop_keeps_active_runner_when_tick_fails():
    db = AsyncMock()
    db.get_runnable_jobs.side_effect = [
        [{"id": "job-1", "status": "local_testing"}],
        RuntimeError("temporary db disconnect"),
    ]

    blocker = asyncio.Event()
    runner = AsyncMock()

    async def run_until_cancelled(job_id):
        await blocker.wait()

    runner.run.side_effect = run_until_cancelled
    real_sleep = asyncio.sleep
    sleep_calls = 0

    async def fake_sleep(_):
        nonlocal sleep_calls
        sleep_calls += 1
        await real_sleep(0)
        if sleep_calls >= 2:
            raise asyncio.CancelledError

    with patch("background.poller.asyncio.sleep", side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await poll_loop(db, runner, poll_interval=0)

    runner.run.assert_called_once_with("job-1")
