import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from utils.logger import get_logger

logger = get_logger(__name__)

TELEGRAM_MESSAGE_LIMIT = 3900
TECHNICAL_TAIL_LIMIT = 1400
CI_RUN_URL_RE = re.compile(r"https://github\.com/[^\s)]+/actions/runs/\d+")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
GITHUB_LOG_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s+")
GITHUB_LOG_PREFIX_RE = re.compile(r"^[A-Z]\d{2}:\d{2}:\d{2}\.\d+Z\s+")


class TelegramService:
    def __init__(
        self,
        bot_token: str,
        approval_chat_id: str,
        escalation_chat_id: str,
        notification_chat_id: str,
        timeout: int = 30,
    ):
        self._approval_chat_id = approval_chat_id
        self._escalation_chat_id = escalation_chat_id
        self._notification_chat_id = notification_chat_id
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{bot_token}",
            timeout=timeout,
        )

    async def send_approval_request(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        text = (
            f"Manual approval required for job {job_id}\n\n"
            f"SPEC summary:\n{_truncate(job.get('spec_text', ''), 1000)}\n\n"
            f"PLAN summary:\n{_truncate(job.get('plan_text', ''), 500)}\n\n"
            "Нажми кнопку ниже, чтобы продолжить pipeline.\n\n"
            f"Для approve: POST /jobs/{job_id}/approve\n"
            f"Для reject: POST /jobs/{job_id}/reject\n"
            f"Для kill: POST /jobs/{job_id}/kill"
        )

        chat_id = await self._send_message_with_fallback(
            job,
            self._approval_chat_id,
            text,
            reply_markup=_approval_keyboard(job_id),
        )

        workspace_path = _workspace_path(job)
        await self._send_document(
            chat_id=chat_id,
            workspace_path=workspace_path,
            filename="SPEC.md",
            fallback_text=job.get("spec_text", ""),
        )
        await self._send_document(
            chat_id=chat_id,
            workspace_path=workspace_path,
            filename="PLAN.md",
            fallback_text=job.get("plan_text", ""),
        )
        await self._send_document(
            chat_id=chat_id,
            workspace_path=workspace_path,
            filename="REQUEST.md",
            fallback_text=job.get("request_snapshot", ""),
        )

        logger.info("telegram.approval_request_sent", job_id=job_id)

    async def send_escalation(
        self,
        job: dict[str, Any],
        reason: str,
        details: Any = "",
    ) -> None:
        text = _format_escalation_message(job, reason, details)

        await self._send_message_with_fallback(job, self._escalation_chat_id, text)
        logger.info("telegram.escalation_sent", job_id=str(job.get("id")), reason=reason)

    async def send_done(self, job: dict[str, Any]) -> None:
        text = (
            f"Pipeline done for job {job.get('id')}\n\n"
            f"Repository: {job.get('github_repo', '')}\n"
            f"Branch: {job.get('github_branch', '')}\n"
            f"PR: {job.get('github_pr_url', '')}\n"
            f"Commit: {job.get('github_commit', '')}"
        )
        await self._send_message_with_fallback(job, self._notification_chat_id, text)
        logger.info("telegram.done_sent", job_id=str(job.get("id")))

    async def send_error(
        self,
        job: dict[str, Any],
        error_step: str,
        error_message: str,
    ) -> None:
        text = f"Pipeline failed for job {job.get('id')}\n\nStep: {error_step}\nError:\n{error_message}"
        await self._send_message_with_fallback(job, self._escalation_chat_id, text)
        logger.info("telegram.error_sent", job_id=str(job.get("id")), step=error_step)

    async def close(self) -> None:
        await self._client.aclose()
        logger.info("telegram.client_closed")

    async def _send_message(
        self,
        chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        data = {
            "chat_id": chat_id,
            "text": _truncate_for_telegram(text),
            "disable_web_page_preview": "true",
        }
        if reply_markup:
            data["reply_markup"] = json.dumps(reply_markup)

        response = await self._client.post(
            "/sendMessage",
            data=data,
        )
        response.raise_for_status()

    async def _send_message_with_fallback(
        self,
        job: dict[str, Any],
        fallback_chat_id: str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> str:
        return await _send_to_first_available(
            _chat_candidates(job, fallback_chat_id),
            lambda chat_id: self._send_message(chat_id, text, reply_markup=reply_markup),
        )

    async def _send_document(
        self,
        chat_id: str,
        workspace_path: Path,
        filename: str,
        fallback_text: str,
    ) -> None:
        file_path = workspace_path / filename
        if file_path.exists():
            content = file_path.read_bytes()
        else:
            content = fallback_text.encode("utf-8")

        response = await self._client.post(
            "/sendDocument",
            data={"chat_id": chat_id},
            files={"document": (filename, content)},
        )
        response.raise_for_status()


async def _send_to_first_available(
    chat_ids: list[str],
    send: Callable[[str], Awaitable[None]],
) -> str:
    last_error: Exception | None = None
    for chat_id in chat_ids:
        try:
            await send(chat_id)
            return chat_id
        except httpx.HTTPStatusError as exc:
            last_error = exc
            logger.warning(
                "telegram.delivery_failed",
                chat_id=chat_id,
                status_code=exc.response.status_code,
            )

    if last_error:
        raise last_error
    raise RuntimeError("No Telegram chat id configured")


def _chat_candidates(job: dict[str, Any], fallback_chat_id: str) -> list[str]:
    origin_candidates = _dedupe_chat_ids(
        [
            job.get("chat_id"),
            job.get("telegram_user_id"),
        ]
    )
    if origin_candidates:
        return origin_candidates

    return _dedupe_chat_ids([fallback_chat_id])


def _dedupe_chat_ids(candidates: list[Any]) -> list[str]:
    result = []
    for candidate in candidates:
        if candidate is None:
            continue
        chat_id = str(candidate).strip()
        if chat_id and chat_id not in result:
            result.append(chat_id)
    return result


def _workspace_path(job: dict[str, Any]) -> Path:
    if job.get("workspace_path"):
        return Path(job["workspace_path"])
    return Path("workspaces") / str(job["id"])


def _approval_keyboard(job_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Approve",
                    "callback_data": f"pipeline_approve:{job_id}",
                },
                {
                    "text": "Reject",
                    "callback_data": f"pipeline_reject:{job_id}",
                },
                {
                    "text": "KILL",
                    "callback_data": f"pipeline_kill:{job_id}",
                },
            ],
        ],
    }


def _truncate(value: str | None, limit: int) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _truncate_for_telegram(value: str) -> str:
    if len(value) <= TELEGRAM_MESSAGE_LIMIT:
        return value

    suffix = f"\n\n... [truncated {len(value) - TELEGRAM_MESSAGE_LIMIT} chars]"
    return value[: TELEGRAM_MESSAGE_LIMIT - len(suffix)] + suffix


def _format_details(details: Any) -> str:
    if isinstance(details, list):
        return "\n".join(str(item) for item in details)
    if isinstance(details, dict):
        return "\n".join(f"{key}: {value}" for key, value in details.items())
    return str(details)


def _format_escalation_message(job: dict[str, Any], reason: str, details: Any) -> str:
    details_text = _clean_log_text(_format_details(details))
    summary = _extract_escalation_summary(details_text)

    sections = [
        f"Нужна ручная проверка по job {job.get('id')}",
        _reason_sentence(reason),
    ]

    failed_path = _format_failed_path(summary)
    if failed_path:
        sections.append(f"Где упало:\n{failed_path}")

    observations = _format_observations(summary)
    if observations:
        sections.append(f"Что видно по диагностике:\n{observations}")

    likely_area = _likely_fix_area(summary, reason)
    if likely_area:
        sections.append(f"Вероятная зона правки:\n{likely_area}")

    links = _format_escalation_links(job, summary)
    if links:
        sections.append(f"Ссылки:\n{links}")

    technical_tail = _technical_tail(details_text)
    if technical_tail:
        sections.append(f"Технический хвост:\n{technical_tail}")

    return "\n\n".join(section for section in sections if section)


def _extract_escalation_summary(details_text: str) -> dict[str, Any]:
    return {
        "ci_run_url": _first_match(CI_RUN_URL_RE, details_text),
        "failed_steps": _extract_failed_steps(details_text),
        "failing_step": _extract_field(details_text, "failing_step"),
        "action": _extract_field(details_text, "action"),
        "current_url": _extract_field(details_text, "current_url"),
        "page_title": _extract_field(details_text, "page_title"),
        "status_codes": _extract_status_codes(details_text),
        "failed_requests": _extract_failed_request_urls(details_text),
        "console_errors": _extract_console_errors(details_text),
    }


def _reason_sentence(reason: str) -> str:
    human_reasons = {
        "ci_fix_retries_exceeded": "Автопочинка остановлена: CI всё ещё падает после нескольких попыток.",
        "ci_failure_streak_exceeded": "Автопочинка остановлена: CI несколько раз подряд падает с одной и той же ошибкой.",
        "ci_timeout_exceeded": "CI не дал завершённый результат в отведённое время.",
        "local_failure_streak_exceeded": "Автопочинка остановлена: pre-push проверка несколько раз подряд падает с одной и той же ошибкой.",
        "smoke_test_failed": "Локальный браузерный smoke-flow упал после попыток автопочинки.",
        "test_retries_exceeded": "Локальные тесты всё ещё падают после попыток автопочинки.",
        "quality_gate_failed": "Quality gate не прошёл после попыток автопочинки.",
        "review_retries_exceeded": "Код-ревью всё ещё требует правок после нескольких попыток.",
        "opencode_stuck_retries_exceeded": "OpenCode застрял несколько раз подряд и дальше лучше смотреть вручную.",
        "watchdog_stale": "Pipeline слишком долго не подавал признаков прогресса.",
    }
    sentence = human_reasons.get(reason, f"Причина эскалации: {reason}.")
    return f"{sentence}\nКод причины: {reason}"


def _format_failed_path(summary: dict[str, Any]) -> str:
    steps = summary.get("failed_steps") or []
    if not steps:
        return ""
    return "\n".join(f"- {step}" for step in steps[:5])


def _format_observations(summary: dict[str, Any]) -> str:
    lines = []
    failing_step = summary.get("failing_step")
    action = summary.get("action")
    if failing_step:
        suffix = f" ({action})" if action else ""
        lines.append(f"- failing step: {failing_step}{suffix}")

    current_url = summary.get("current_url")
    if current_url:
        lines.append(f"- current URL: {current_url}")

    page_title = summary.get("page_title")
    if page_title:
        lines.append(f"- page title: {page_title}")

    status_codes = summary.get("status_codes") or []
    if status_codes:
        lines.append(f"- HTTP >=400: {', '.join(status_codes[:6])}")

    failed_requests = summary.get("failed_requests") or []
    if failed_requests:
        lines.append(f"- failed request: {failed_requests[0]}")

    console_errors = summary.get("console_errors") or []
    if console_errors:
        lines.append(f"- console error: {_truncate(console_errors[0], 220)}")

    if current_url and "/login?redirect=" in current_url:
        lines.append("- после login браузер остался на protected redirect обратно к login")

    return "\n".join(lines)


def _likely_fix_area(summary: dict[str, Any], reason: str) -> str:
    current_url = summary.get("current_url") or ""
    failed_steps = " ".join(summary.get("failed_steps") or [])
    failed_requests = " ".join(summary.get("failed_requests") or [])
    status_codes = set(summary.get("status_codes") or [])

    if "/login?redirect=" in current_url:
        return (
            "auth flow: login API, token/cookie storage key, route guard, session hydration, seeded demo credentials."
        )
    if "Run browser smoke flow" in failed_steps or summary.get("failing_step"):
        return "browser smoke flow: смотри failing step, network >=400, storage/cookies и HTML artifact."
    if "401" in status_codes or "403" in status_codes or "/login" in failed_requests:
        return "auth/API permissions: demo user, backend auth response, frontend token handling."
    if reason.startswith("ci_"):
        return "CI/runtime: открыть CI run, смотреть первый failing step и Docker/runtime logs."
    return ""


def _format_escalation_links(job: dict[str, Any], summary: dict[str, Any]) -> str:
    links = []
    if summary.get("ci_run_url"):
        links.append(f"- CI run: {summary['ci_run_url']}")
    if job.get("github_pr_url"):
        links.append(f"- PR: {job['github_pr_url']}")
    elif job.get("github_repo"):
        links.append(f"- repo: {job['github_repo']}")
    return "\n".join(links)


def _technical_tail(details_text: str) -> str:
    diagnostic = _diagnostic_excerpt(details_text)
    if diagnostic:
        return _truncate_with_marker(diagnostic, TECHNICAL_TAIL_LIMIT)
    if not details_text.strip():
        return ""
    return _tail_with_marker(details_text.strip(), TECHNICAL_TAIL_LIMIT)


def _diagnostic_excerpt(details_text: str) -> str:
    if "browser smoke failure diagnostics" not in details_text:
        return ""

    lines = details_text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if "browser smoke failure diagnostics" in line),
        None,
    )
    if start is None:
        return ""

    keep_prefixes = (
        "browser smoke failure diagnostics",
        "failing_step:",
        "action:",
        "payload:",
        "error:",
        "current_url:",
        "page_title:",
        "body_text_tail:",
        "localStorage:",
        "sessionStorage:",
        "cookies:",
        "console_errors:",
        "failed_requests:",
        "responses_4xx_5xx:",
        "recent_network:",
        "html_tail:",
        "- ",
    )
    excerpt = []
    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("--- logs:") and excerpt:
            break
        if stripped.startswith(keep_prefixes):
            excerpt.append(stripped)
        if len("\n".join(excerpt)) >= TECHNICAL_TAIL_LIMIT:
            break
    return "\n".join(excerpt)


def _extract_failed_steps(details_text: str) -> list[str]:
    result = []
    current_job = ""
    for raw_line in details_text.splitlines():
        line = raw_line.rstrip()
        job_match = re.match(r"^- ([^:]+): (failure|cancelled|timed_out|action_required|failed|unknown)", line)
        if job_match:
            current_job = job_match.group(1)
            result.append(f"{current_job}: {job_match.group(2)}")
            continue

        step_match = re.match(r"^\s+- ([^:]+): (failure|cancelled|timed_out|action_required|failed|unknown)", line)
        if step_match:
            step = step_match.group(1)
            state = step_match.group(2)
            result.append(f"{current_job} -> {step}: {state}" if current_job else f"{step}: {state}")

    return result


def _extract_field(details_text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", details_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_status_codes(details_text: str) -> list[str]:
    codes = re.findall(r'"status":\s*(4\d\d|5\d\d)', details_text)
    return _dedupe(codes)


def _extract_failed_request_urls(details_text: str) -> list[str]:
    urls = []
    for line in details_text.splitlines():
        if "requestfailed" not in line and "failed_requests" not in line:
            continue
        urls.extend(re.findall(r'"url":\s*"([^"]+)"', line))
    return _dedupe(urls)


def _extract_console_errors(details_text: str) -> list[str]:
    errors = []
    for line in details_text.splitlines():
        if '"type": "error"' not in line:
            continue
        match = re.search(r'"text":\s*"([^"]+)"', line)
        if match:
            errors.append(match.group(1))
    return _dedupe(errors)


def _first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else ""


def _clean_log_text(value: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", value)
    lines = []
    for line in text.splitlines():
        line = GITHUB_LOG_TS_RE.sub("", line)
        line = GITHUB_LOG_PREFIX_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _tail_with_marker(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = f"\n... [truncated {len(text) - limit} chars before this technical tail]"
    return text[-(limit - len(marker)) :] + marker


def _truncate_with_marker(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = f"\n... [truncated {len(text) - limit} chars]"
    return text[: limit - len(marker)] + marker


def _dedupe(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
