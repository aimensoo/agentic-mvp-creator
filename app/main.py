import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.v1.router import router
from background.poller import poll_loop
from dependencies import (
    get_db_service,
    get_openclaw_client,
    get_opencode_client,
    get_pipeline_runner,
    get_telegram_service,
)
from app.config import settings
from services.watchdog_service import watchdog_loop
from utils.logger import setup_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.ENV)
    logger.info("app.starting")

    db = get_db_service()
    openclaw_client = get_openclaw_client()
    opencode_client = get_opencode_client()
    runner = get_pipeline_runner()
    telegram = get_telegram_service()
    poller_task = None
    watchdog_task = None

    try:
        await db.connect()
        logger.info("app.db_connected")
    except Exception as e:
        logger.error("app.db_connect_failed", error=str(e))

    await _check_opencode_health(opencode_client)
    await _check_openclaw_health(openclaw_client)

    poller_task = asyncio.create_task(poll_loop(db, runner))
    watchdog_task = asyncio.create_task(
        watchdog_loop(
            db,
            telegram,
            stale_timeout=settings.JOB_STALE_TIMEOUT,
        )
    )

    try:
        yield
    finally:
        for task in (poller_task, watchdog_task):
            if task:
                task.cancel()
        for task in (poller_task, watchdog_task):
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        await openclaw_client.close()
        await opencode_client.close()
        await telegram.close()
        await db.close()
        logger.info("app.stopped")


async def _check_opencode_health(opencode_client) -> None:
    try:
        await opencode_client.health()
        logger.info("app.opencode_healthy")
    except Exception as e:
        logger.warning("app.opencode_health_failed", error=str(e))


async def _check_openclaw_health(openclaw_client) -> None:
    try:
        await openclaw_client.chat(
            system_prompt="Health check. Reply with ok.",
            user_message="ok",
            temperature=0,
        )
        logger.info("app.openclaw_healthy")
    except Exception as e:
        logger.warning("app.openclaw_health_failed", error=str(e))


app = FastAPI(title="Telegram AI Pipeline Runner", lifespan=lifespan)
app.include_router(router)
