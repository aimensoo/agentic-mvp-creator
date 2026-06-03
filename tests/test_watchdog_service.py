import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.watchdog_service import watchdog_loop


async def test_watchdog_escalates_stale_jobs_and_updates_status():
    db = AsyncMock()
    telegram = AsyncMock()
    db.get_stale_jobs.return_value = [
        {"id": "job-1", "status": "coding", "telegram_user_id": 123},
    ]

    with patch("services.watchdog_service.asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(db, telegram, stale_timeout=1800, check_interval=0, startup_delay=0)

    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.args[0]["id"] == "job-1"
    assert telegram.send_escalation.call_args.kwargs["reason"] == "watchdog_stale"
    assert "No activity for 1800s at step coding" in telegram.send_escalation.call_args.kwargs["details"]
    db.update_status.assert_called_once_with("job-1", "awaiting_human_escalation")


async def test_watchdog_waits_before_first_stale_check():
    db = AsyncMock()
    telegram = AsyncMock()

    with patch("services.watchdog_service.asyncio.sleep", side_effect=asyncio.CancelledError) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await watchdog_loop(db, telegram, stale_timeout=1800, check_interval=60)

    sleep.assert_called_once_with(60)
    db.get_stale_jobs.assert_not_called()
    telegram.send_escalation.assert_not_called()
