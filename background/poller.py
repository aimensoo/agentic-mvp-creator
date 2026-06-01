import asyncio

from utils.logger import get_logger
from utils.redaction import redact_secrets

logger = get_logger(__name__)

POLL_INTERVAL = 5

_NEXT_STATUS = {
    "queued": "loading_request",
    "loading_request": "loading_request",
    "drafting_spec": "drafting_spec",
    "planning": "planning",
    "preparing_workspace": "coding",
    "coding": "coding",
    "coding_in_progress": "coding_in_progress",
    "local_testing": "local_testing",
    "git_push": "git_push",
    "ci_testing": "ci_testing",
    "reviewing_code": "reviewing_code",
    "fixing": "fixing",
    "fixing_in_progress": "fixing_in_progress",
}


def next_status(status: str) -> str:
    try:
        return _NEXT_STATUS[status]
    except KeyError as exc:
        raise ValueError(f"Unsupported runnable status: {status}") from exc


async def poll_loop(db, runner, poll_interval: int = POLL_INTERVAL) -> None:
    logger.info("poller.started", poll_interval=poll_interval)
    active_tasks: dict[str, asyncio.Task] = {}
    try:
        while True:
            try:
                jobs = await db.get_runnable_jobs()
                logger.info("poller.tick", jobs=len(jobs))

                for job in jobs:
                    job_id = str(job["id"])
                    if job_id in active_tasks:
                        logger.info("poller.job_already_active", job_id=job_id, status=job["status"])
                        continue

                    status = next_status(job["status"])
                    if status != job["status"]:
                        await db.update_status(job_id, status)

                    task = asyncio.create_task(runner.run(job_id))
                    active_tasks[job_id] = task
                    task.add_done_callback(
                        lambda completed_task, completed_job_id=job_id: _job_done(
                            active_tasks,
                            completed_job_id,
                            completed_task,
                        )
                    )
                    logger.info("poller.job_started", job_id=job_id, status=status)

                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("poller.tick_failed", error=redact_secrets(exc))
                await asyncio.sleep(poll_interval)
    finally:
        for task in active_tasks.values():
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks.values(), return_exceptions=True)


def _job_done(active_tasks: dict[str, asyncio.Task], job_id: str, task: asyncio.Task) -> None:
    active_tasks.pop(job_id, None)
    if task.cancelled():
        logger.info("poller.job_cancelled", job_id=job_id)
        return

    exc = task.exception()
    if exc:
        logger.error("poller.job_task_failed", job_id=job_id, error=redact_secrets(exc))
