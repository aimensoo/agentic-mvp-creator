import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg

from utils.logger import get_logger
from utils.redaction import redact_secrets

logger = get_logger(__name__)

_TERMINAL_STATUSES = (
    "done",
    "failed",
    "awaiting_human_approval",
    "awaiting_human_feedback",
    "awaiting_human_escalation",
)
_JSON_FIELDS = {
    "review_result",
    "opencode_session_history",
    "ci_failure_summary",
    "local_failure_summary",
}
_TRANSIENT_DB_ERROR_TEXT = (
    "connection was closed",
    "connection is closed",
    "server closed the connection",
    "connection reset by peer",
)
T = TypeVar("T")


class DatabaseService:
    def __init__(self, db_url: str):
        self._db_url = db_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._db_url, min_size=2, max_size=10)
        logger.info("db.connected")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("db.closed")

    async def create_job(
        self,
        input_text: str,
        input_source: str = "api",
        chat_id: int | None = None,
        telegram_user_id: int | None = None,
    ) -> str:
        async def _create(conn):
            job_id = await conn.fetchval(
                """
                INSERT INTO pipeline_jobs (input_text, input_source, chat_id, telegram_user_id)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                input_text,
                input_source,
                chat_id,
                telegram_user_id,
            )
            logger.info("db.job_created", job_id=str(job_id))
            return str(job_id)

        return await self._with_connection_retry("create_job", _create)

    async def get_job(self, job_id: str) -> dict | None:
        async def _get(conn):
            row = await conn.fetchrow(
                "SELECT * FROM pipeline_jobs WHERE id = $1::uuid",
                job_id,
            )
            return dict(row) if row else None

        return await self._with_connection_retry("get_job", _get)

    async def update_status(self, job_id: str, status: str) -> None:
        async def _update(conn):
            await conn.execute(
                """
                UPDATE pipeline_jobs
                SET status = $2,
                    last_activity_at = now(),
                    updated_at = now()
                WHERE id = $1::uuid
                """,
                job_id,
                status,
            )
            logger.info("db.status_updated", job_id=job_id, status=status)

        await self._with_connection_retry("update_status", _update)

    async def touch_job(self, job_id: str) -> None:
        async def _touch(conn):
            await conn.execute(
                """
                UPDATE pipeline_jobs
                SET last_activity_at = now(),
                    updated_at = now()
                WHERE id = $1::uuid
                """,
                job_id,
            )
            logger.info("db.job_touched", job_id=job_id)

        await self._with_connection_retry("touch_job", _touch)

    async def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        set_clauses = []
        values = [job_id]
        for i, (key, value) in enumerate(fields.items(), start=2):
            set_clauses.append(f"{key} = ${i}")
            values.append(_encode_update_value(key, value))
        set_clauses.append("updated_at = now()")
        sql = f"UPDATE pipeline_jobs SET {', '.join(set_clauses)} WHERE id = $1::uuid"

        async def _update(conn):
            await conn.execute(sql, *values)

        await self._with_connection_retry("update_job", _update)

    async def list_jobs(self, limit: int = 20) -> list[dict]:
        async def _list(conn):
            rows = await conn.fetch(
                "SELECT * FROM pipeline_jobs ORDER BY created_at DESC LIMIT $1",
                limit,
            )
            return [dict(r) for r in rows]

        return await self._with_connection_retry("list_jobs", _list)

    async def get_runnable_jobs(self) -> list[dict]:
        async def _get(conn):
            rows = await conn.fetch(
                """
                SELECT * FROM pipeline_jobs
                WHERE status IN (
                    'queued',
                    'loading_request',
                    'drafting_spec',
                    'planning',
                    'preparing_workspace',
                    'coding',
                    'coding_in_progress',
                    'local_testing',
                    'git_push',
                    'ci_testing',
                    'reviewing_code',
                    'fixing',
                    'fixing_in_progress'
                )
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                """
            )
            return [dict(r) for r in rows]

        return await self._with_connection_retry("get_runnable_jobs", _get)

    async def save_opencode_session_id(
        self,
        job_id: str,
        session_id: str,
        *,
        phase: str | None = None,
        workspace_path: str | None = None,
    ) -> None:
        async def _save(conn):
            await conn.execute(
                """
                UPDATE pipeline_jobs
                SET opencode_session_id = $2,
                    opencode_session_history =
                        COALESCE(opencode_session_history, '[]'::jsonb)
                        || jsonb_build_array(
                            jsonb_strip_nulls(
                                jsonb_build_object(
                                    'session_id', $2::text,
                                    'phase', $3::text,
                                    'workspace_path', $4::text,
                                    'recorded_at', now()
                                )
                            )
                        ),
                    updated_at = now()
                WHERE id = $1::uuid
                """,
                job_id,
                session_id,
                phase,
                workspace_path,
            )

        await self._with_connection_retry("save_opencode_session_id", _save)

    async def get_stale_jobs(self, timeout_seconds: int) -> list[dict]:
        async def _get(conn):
            rows = await conn.fetch(
                """
                SELECT * FROM pipeline_jobs
                WHERE status NOT IN ('done', 'failed', 'awaiting_human_approval', 'awaiting_human_escalation')
                  AND last_activity_at < now() - ($1 * interval '1 second')
                """,
                timeout_seconds,
            )
            return [dict(r) for r in rows]

        return await self._with_connection_retry("get_stale_jobs", _get)

    async def _with_connection_retry(
        self,
        operation: str,
        callback: Callable[[asyncpg.Connection], Awaitable[T]],
    ) -> T:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                async with self._pool.acquire() as conn:
                    return await callback(conn)
            except Exception as exc:
                last_exc = exc
                if attempt or not _is_transient_connection_error(exc):
                    raise
                logger.warning(
                    "db.connection_retry",
                    operation=operation,
                    error=redact_secrets(exc),
                )

        if last_exc is not None:  # pragma: no cover
            raise last_exc
        raise RuntimeError(f"Database operation did not run: {operation}")  # pragma: no cover


def _encode_update_value(key: str, value):
    if key in _JSON_FIELDS and value is not None:
        return json.dumps(value)
    return value


def _is_transient_connection_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncpg.PostgresConnectionError, ConnectionError, OSError)):
        return True

    message = str(exc).lower()
    return any(fragment in message for fragment in _TRANSIENT_DB_ERROR_TEXT)
