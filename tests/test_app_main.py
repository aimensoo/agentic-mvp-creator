from unittest.mock import AsyncMock, patch

from app.main import _check_openclaw_health, _check_opencode_health


async def test_check_opencode_health_does_not_raise_on_failure():
    client = AsyncMock()
    client.health.side_effect = RuntimeError("down")

    await _check_opencode_health(client)

    client.health.assert_called_once()


async def test_check_openclaw_health_does_not_raise_on_failure():
    client = AsyncMock()
    client.chat.side_effect = RuntimeError("down")

    await _check_openclaw_health(client)

    client.chat.assert_called_once()


async def test_lifespan_starts_and_cleans_background_tasks():
    from app.main import lifespan

    db = AsyncMock()
    openclaw = AsyncMock()
    opencode = AsyncMock()
    telegram = AsyncMock()
    runner = AsyncMock()

    with (
        patch("app.main.get_db_service", return_value=db),
        patch("app.main.get_openclaw_client", return_value=openclaw),
        patch("app.main.get_opencode_client", return_value=opencode),
        patch("app.main.get_pipeline_runner", return_value=runner),
        patch("app.main.get_telegram_service", return_value=telegram),
        patch("app.main.poll_loop", new_callable=AsyncMock),
        patch("app.main.watchdog_loop", new_callable=AsyncMock),
    ):
        async with lifespan(object()):
            pass

    db.connect.assert_called_once()
    openclaw.close.assert_called_once()
    opencode.close.assert_called_once()
    telegram.close.assert_called_once()
    db.close.assert_called_once()
