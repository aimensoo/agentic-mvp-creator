import asyncio

from utils.logger import get_logger

logger = get_logger(__name__)

WATCHDOG_INTERVAL = 60


async def watchdog_loop(
    db,
    telegram,
    stale_timeout: int,
    check_interval: int = WATCHDOG_INTERVAL,
    startup_delay: int | None = None,
) -> None:
    logger.info(
        "watchdog.started",
        stale_timeout=stale_timeout,
        check_interval=check_interval,
        startup_delay=startup_delay if startup_delay is not None else check_interval,
    )
    await asyncio.sleep(check_interval if startup_delay is None else startup_delay)

    while True:
        stale_jobs = await db.get_stale_jobs(stale_timeout)
        logger.info("watchdog.tick", stale_jobs=len(stale_jobs))

        for job in stale_jobs:
            details = f"No activity for {stale_timeout}s at step {job['status']}"
            await telegram.send_escalation(job, reason="watchdog_stale", details=details)
            await db.update_status(str(job["id"]), "awaiting_human_escalation")
            logger.warning("watchdog.job_escalated", job_id=str(job["id"]), status=job["status"])

        await asyncio.sleep(check_interval)
