import re
import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from services.git_service import GitResult, NoChangesError
from services.coding_service import OpenCodeRunState
from services.quality_gate_service import QualityGateService
from services.test_service import classify_ci_failure_logs, ci_failure_signature_from_logs
from utils.logger import get_logger
from utils.redaction import redact_secrets

logger = get_logger(__name__)
DOCKER_CI_SCRIPT_PATH = ".pipeline/docker-ci.sh"


class JobStatus(StrEnum):
    QUEUED = "queued"
    LOADING_REQUEST = "loading_request"
    DRAFTING_SPEC = "drafting_spec"
    PLANNING = "planning"
    AWAITING_HUMAN_APPROVAL = "awaiting_human_approval"
    AWAITING_HUMAN_FEEDBACK = "awaiting_human_feedback"
    PREPARING_WORKSPACE = "preparing_workspace"
    CODING = "coding"
    CODING_IN_PROGRESS = "coding_in_progress"
    LOCAL_TESTING = "local_testing"
    GIT_PUSH = "git_push"
    CI_TESTING = "ci_testing"
    REVIEWING_CODE = "reviewing_code"
    FIXING = "fixing"
    FIXING_IN_PROGRESS = "fixing_in_progress"
    AWAITING_HUMAN_ESCALATION = "awaiting_human_escalation"
    DONE = "done"
    FAILED = "failed"


TERMINAL_STATUSES = {
    JobStatus.AWAITING_HUMAN_APPROVAL,
    JobStatus.AWAITING_HUMAN_FEEDBACK,
    JobStatus.AWAITING_HUMAN_ESCALATION,
    JobStatus.DONE,
    JobStatus.FAILED,
}

RECOVERABLE_OPENCODE_STATES = {
    OpenCodeRunState.STUCK,
    OpenCodeRunState.WAITING_PERMISSION,
    OpenCodeRunState.WAITING_INPUT,
    OpenCodeRunState.EMPTY_RESULT,
}

HEARTBEAT_ON_SAME_STATUS = {
    JobStatus.LOCAL_TESTING,
    JobStatus.GIT_PUSH,
    JobStatus.CI_TESTING,
    JobStatus.REVIEWING_CODE,
    JobStatus.FIXING,
}


class PipelineRunner:
    def __init__(
        self,
        db,
        spec_service,
        plan_service,
        coding_service,
        test_service,
        git_service,
        review_service,
        telegram_service,
        quality_gate_service=None,
        smoke_test_service=None,
        max_review_retries: int = 3,
        max_test_retries: int = 3,
        max_local_same_failure_retries: int | None = None,
        max_ci_same_failure_retries: int | None = None,
        max_opencode_stuck_retries: int = 2,
        workspace_root: Path | str = "workspaces",
        opencode_workspace_root: Path | str | None = None,
    ):
        self._db = db
        self._spec_service = spec_service
        self._plan_service = plan_service
        self._coding_service = coding_service
        self._test_service = test_service
        self._git_service = git_service
        self._review_service = review_service
        self._telegram = telegram_service
        self._quality_gate = quality_gate_service or QualityGateService()
        self._smoke_test = smoke_test_service
        self._max_review_retries = max_review_retries
        self._max_test_retries = max_test_retries
        self._max_local_same_failure_retries = (
            max_test_retries if max_local_same_failure_retries is None else max_local_same_failure_retries
        )
        self._max_ci_same_failure_retries = (
            max_test_retries if max_ci_same_failure_retries is None else max_ci_same_failure_retries
        )
        self._max_opencode_stuck_retries = max_opencode_stuck_retries
        self._workspace_root = Path(workspace_root)
        self._opencode_workspace_root = (
            Path(opencode_workspace_root) if opencode_workspace_root else self._workspace_root
        )

    async def run(self, job_id: str) -> None:
        try:
            await self._run_pipeline(job_id)
        except Exception as exc:
            job = await self._db.get_job(job_id)
            error_step = job.get("status", "unknown") if job else "unknown"
            error_message = redact_secrets(exc)
            if job:
                await self._db.update_job(
                    job_id,
                    status=JobStatus.FAILED.value,
                    error_step=error_step,
                    error_message=error_message,
                )
                failed_job = {**job, "status": JobStatus.FAILED.value}
                try:
                    await self._telegram.send_error(failed_job, error_step, error_message)
                except Exception as telegram_exc:
                    logger.warning(
                        "pipeline.telegram_error_notification_failed",
                        job_id=job_id,
                        error=redact_secrets(telegram_exc),
                    )

            logger.error("pipeline.failed", job_id=job_id, error=error_message, step=error_step)

    async def _run_pipeline(self, job_id: str) -> None:
        job = await self._require_job(job_id)
        status = JobStatus(job["status"])
        logger.info("pipeline.run_started", job_id=job_id, status=status.value)

        if status in TERMINAL_STATUSES:
            logger.info("pipeline.status_terminal", job_id=job_id, status=status.value)
            return

        if status in {JobStatus.QUEUED, JobStatus.LOADING_REQUEST}:
            job = await self._load_request(job)

        if JobStatus(job["status"]) == JobStatus.DRAFTING_SPEC:
            job = await self._draft_spec(job)

        if JobStatus(job["status"]) == JobStatus.PLANNING:
            job = await self._plan(job)

        if JobStatus(job["status"]) == JobStatus.AWAITING_HUMAN_APPROVAL:
            await self._send_approval_and_stop(job)
            return

        if JobStatus(job["status"]) == JobStatus.PREPARING_WORKSPACE:
            job = await self._prepare_workspace(job)

        if JobStatus(job["status"]) == JobStatus.CODING:
            job = await self._run_coding(job)
            if JobStatus(job["status"]) == JobStatus.CODING_IN_PROGRESS:
                return

        if JobStatus(job["status"]) == JobStatus.CODING_IN_PROGRESS:
            job = await self._check_coding(job)
            if JobStatus(job["status"]) == JobStatus.CODING_IN_PROGRESS:
                return

        if JobStatus(job["status"]) == JobStatus.FIXING_IN_PROGRESS:
            job = await self._check_fixing(job)
            if JobStatus(job["status"]) == JobStatus.FIXING_IN_PROGRESS:
                return

        if JobStatus(job["status"]) in {
            JobStatus.LOCAL_TESTING,
            JobStatus.GIT_PUSH,
            JobStatus.CI_TESTING,
            JobStatus.REVIEWING_CODE,
            JobStatus.FIXING,
        }:
            await self._run_fix_loop(job)
            return

    async def _load_request(self, job: dict[str, Any]) -> dict[str, Any]:
        job = await self._set_status(job, JobStatus.LOADING_REQUEST)
        request_text = (job.get("input_text") or "").strip()
        if not request_text:
            raise RuntimeError("Job input_text is empty")

        await self._db.update_job(job["id"], request_snapshot=request_text)
        job["request_snapshot"] = request_text
        return await self._set_status(job, JobStatus.DRAFTING_SPEC)

    async def _draft_spec(self, job: dict[str, Any]) -> dict[str, Any]:
        job = await self._set_status(job, JobStatus.DRAFTING_SPEC)
        spec_text = await self._spec_service.generate(job["request_snapshot"])
        await self._db.update_job(job["id"], spec_text=spec_text)
        job["spec_text"] = spec_text
        return await self._set_status(job, JobStatus.PLANNING)

    async def _plan(self, job: dict[str, Any]) -> dict[str, Any]:
        job = await self._set_status(job, JobStatus.PLANNING)
        feedback = job.get("approval_feedback")
        if feedback:
            plan_text = await self._plan_service.generate(
                job["spec_text"],
                feedback=feedback,
                previous_plan=job.get("plan_text"),
            )
        else:
            plan_text = await self._plan_service.generate(job["spec_text"])
        await self._db.update_job(job["id"], plan_text=plan_text)
        job["plan_text"] = plan_text
        return await self._set_status(job, JobStatus.AWAITING_HUMAN_APPROVAL)

    async def _send_approval_and_stop(self, job: dict[str, Any]) -> None:
        await self._telegram.send_approval_request(job)
        logger.info("pipeline.awaiting_human_approval", job_id=str(job["id"]))

    async def _prepare_workspace(self, job: dict[str, Any]) -> dict[str, Any]:
        job = await self._set_status(job, JobStatus.PREPARING_WORKSPACE)
        workspace_path = self._ensure_workspace(job)
        job["workspace_path"] = str(workspace_path)
        await self._db.update_job(job["id"], opencode_session_id=None)
        job["opencode_session_id"] = None
        return await self._set_status(job, JobStatus.CODING)

    async def _run_coding(self, job: dict[str, Any]) -> dict[str, Any]:
        job = await self._set_status(job, JobStatus.CODING)
        self._ensure_workspace(job)
        opencode_workspace_path = str(self._opencode_workspace_path(job["id"]))
        session_id = job.get("opencode_session_id")
        if session_id:
            return await self._set_status(job, JobStatus.CODING_IN_PROGRESS)

        session_id = await self._coding_service.start(str(job["id"]), opencode_workspace_path)
        await self._db.save_opencode_session_id(
            job["id"],
            session_id,
            phase="coding",
            workspace_path=opencode_workspace_path,
        )
        job["opencode_session_id"] = session_id
        return await self._set_status(job, JobStatus.CODING_IN_PROGRESS)

    async def _check_coding(self, job: dict[str, Any]) -> dict[str, Any]:
        session_id = job.get("opencode_session_id")
        if not session_id:
            raise RuntimeError("OpenCode session id is missing")

        state = await self._coding_service.check_state(str(job["id"]), session_id)
        if state == OpenCodeRunState.MODEL_ERROR:
            return await self._handle_opencode_model_error(job, session_id, phase="coding")

        if state in RECOVERABLE_OPENCODE_STATES:
            return await self._handle_stuck_opencode(job, session_id, phase="coding", state=state)

        if state == OpenCodeRunState.RUNNING:
            await self._db.touch_job(str(job["id"]))
            logger.info(
                "pipeline.coding_in_progress",
                job_id=str(job["id"]),
                session_id=session_id,
            )
            return job

        return await self._complete_opencode_session(job)

    async def _check_fixing(self, job: dict[str, Any]) -> dict[str, Any]:
        session_id = job.get("opencode_session_id")
        if not session_id:
            raise RuntimeError("OpenCode fix session id is missing")

        state = await self._coding_service.check_state(str(job["id"]), session_id)
        if state == OpenCodeRunState.MODEL_ERROR:
            return await self._handle_opencode_model_error(job, session_id, phase="fixing")

        if state in RECOVERABLE_OPENCODE_STATES:
            return await self._handle_stuck_opencode(job, session_id, phase="fixing", state=state)

        if state == OpenCodeRunState.RUNNING:
            await self._db.touch_job(str(job["id"]))
            logger.info(
                "pipeline.fixing_in_progress",
                job_id=str(job["id"]),
                session_id=session_id,
            )
            return job

        return await self._complete_opencode_session(job)

    async def _complete_opencode_session(self, job: dict[str, Any]) -> dict[str, Any]:
        await self._db.update_job(
            job["id"],
            opencode_session_id=None,
            opencode_stuck_retries=0,
        )
        job["opencode_session_id"] = None
        job["opencode_stuck_retries"] = 0
        return await self._set_status(job, JobStatus.LOCAL_TESTING)

    async def _handle_opencode_model_error(
        self,
        job: dict[str, Any],
        session_id: str,
        phase: str,
    ) -> dict[str, Any]:
        logger.warning(
            "pipeline.opencode_model_error_detected",
            job_id=str(job["id"]),
            session_id=session_id,
            phase=phase,
        )

        try:
            await self._coding_service.abort(session_id)
        except Exception as exc:
            logger.warning(
                "pipeline.opencode_abort_after_model_error_failed",
                job_id=str(job["id"]),
                session_id=session_id,
                error=str(exc),
            )

        await self._db.update_job(
            job["id"],
            opencode_session_id=None,
            opencode_stuck_retries=0,
        )
        job["opencode_session_id"] = None
        job["opencode_stuck_retries"] = 0
        await self._escalate(
            job,
            reason="opencode_model_error",
            details=(
                "OpenCode returned a model/provider error and produced no code changes.\n"
                "Check OPENCODE_MODEL, provider credentials/subscription, and current model availability."
            ),
        )
        return job

    async def _handle_stuck_opencode(
        self,
        job: dict[str, Any],
        session_id: str,
        phase: str,
        state: OpenCodeRunState,
    ) -> dict[str, Any]:
        stuck_retries = int(job.get("opencode_stuck_retries") or 0) + 1
        logger.warning(
            "pipeline.opencode_stuck_detected",
            job_id=str(job["id"]),
            session_id=session_id,
            phase=phase,
            state=state.value,
            stuck_retries=stuck_retries,
        )

        try:
            await self._coding_service.abort(session_id)
        except Exception as exc:
            logger.warning(
                "pipeline.opencode_abort_failed",
                job_id=str(job["id"]),
                session_id=session_id,
                error=str(exc),
            )

        if stuck_retries > self._max_opencode_stuck_retries:
            await self._db.update_job(
                job["id"],
                opencode_stuck_retries=stuck_retries,
                opencode_session_id=None,
            )
            job["opencode_stuck_retries"] = stuck_retries
            job["opencode_session_id"] = None
            await self._escalate(
                job,
                reason="opencode_stuck_retries_exceeded",
                details=f"OpenCode session {session_id} entered {state.value} during {phase}.",
            )
            return job

        await self._db.update_job(
            job["id"],
            opencode_stuck_retries=stuck_retries,
            opencode_session_id=None,
        )
        job["opencode_stuck_retries"] = stuck_retries
        job["opencode_session_id"] = None
        return await self._set_status(job, JobStatus.LOCAL_TESTING)

    async def _run_fix_loop(self, job: dict[str, Any]) -> None:
        workspace_path = self._workspace_path(job["id"])
        git_result = _git_result_from_job(job)
        review_attempt = int(job.get("review_retries") or 0)
        ci_attempt = int(job.get("ci_fix_retries") or 0)
        status = JobStatus(job["status"])

        if status == JobStatus.CI_TESTING:
            if git_result is None:
                raise RuntimeError("Cannot resume CI testing without saved GitHub repo, branch, commit, and PR URL")
            await self._wait_for_ci_and_continue(job, git_result, ci_attempt)
            return

        if status == JobStatus.REVIEWING_CODE:
            if git_result is None:
                raise RuntimeError("Cannot resume review without saved GitHub repo, branch, commit, and PR URL")
            await self._review_after_ci(job, git_result, review_attempt)
            return

        while review_attempt <= self._max_review_retries:
            self._restore_frozen_docker_ci(job, workspace_path)
            job = await self._set_status(job, JobStatus.LOCAL_TESTING)

            quality_result = self._quality_gate.run(workspace_path)
            if not quality_result.passed:
                await self._handle_pre_push_failure(
                    job,
                    source="quality_gate",
                    issues=[_jsonable(issue) for issue in quality_result.issues],
                    context=quality_result.output,
                )
                return

            local_result = self._test_service.run_local(str(workspace_path))
            if not local_result.passed:
                await self._handle_pre_push_failure(
                    job,
                    source="local_tests",
                    issues=[],
                    context=local_result.output,
                )
                return

            if self._smoke_test is not None:
                smoke_result = self._smoke_test.run(workspace_path)
                if not smoke_result.passed:
                    await self._handle_pre_push_failure(
                        job,
                        source="local_smoke",
                        issues=[],
                        context=smoke_result.output,
                    )
                    return

            await _reset_local_failure_streak(self._db, job)
            await self._capture_docker_ci_if_needed(job, workspace_path)
            job = await self._set_status(job, JobStatus.GIT_PUSH)
            if git_result is None:
                git_result = self._git_service.push_and_create_pr(
                    str(job["id"]),
                    workspace_path,
                    spec_text=job.get("spec_text") or "",
                )
                await self._save_git_result(job, git_result)
                job.update(git_result.model_dump())
            else:
                try:
                    commit_sha = self._git_service.force_push(str(job["id"]), workspace_path)
                except NoChangesError as exc:
                    if self._workspace_already_at_saved_commit(job, workspace_path, git_result):
                        logger.warning(
                            "pipeline.force_push_no_changes_using_saved_commit",
                            job_id=str(job["id"]),
                            commit=git_result.commit_sha,
                            error=str(exc),
                        )
                        job = await self._set_status(job, JobStatus.CI_TESTING)
                        await self._wait_for_ci_and_continue(job, git_result, ci_attempt)
                        return

                    logger.warning(
                        "pipeline.force_push_no_changes",
                        job_id=str(job["id"]),
                        error=str(exc),
                    )
                    await self._escalate(
                        job,
                        reason="opencode_empty_result",
                        details=str(exc),
                    )
                    return
                git_result = git_result.model_copy(update={"commit_sha": commit_sha})
                await self._db.update_job(job["id"], github_commit=commit_sha)
                job["github_commit"] = commit_sha

            job = await self._set_status(job, JobStatus.CI_TESTING)
            await self._wait_for_ci_and_continue(job, git_result, ci_attempt)
            return

        await self._escalate(job, reason="review_retries_exceeded", details=job.get("review_result", ""))

    def _restore_frozen_docker_ci(self, job: dict[str, Any], workspace_path: Path) -> None:
        content = job.get("docker_ci_script_content")
        if not isinstance(content, str) or not content:
            return

        rel_path = job.get("docker_ci_script_path") or DOCKER_CI_SCRIPT_PATH
        script_path = workspace_path / rel_path
        current = script_path.read_text() if script_path.exists() else None
        if current == content:
            return

        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(content)
        script_path.chmod(script_path.stat().st_mode | 0o111)
        logger.warning(
            "pipeline.docker_ci_restored_from_snapshot",
            job_id=str(job["id"]),
            path=rel_path,
            sha256=job.get("docker_ci_script_sha256"),
        )

    async def _capture_docker_ci_if_needed(self, job: dict[str, Any], workspace_path: Path) -> None:
        if job.get("docker_ci_script_content"):
            return

        rel_path = DOCKER_CI_SCRIPT_PATH
        script_path = workspace_path / rel_path
        if not script_path.exists():
            logger.warning(
                "pipeline.docker_ci_snapshot_skipped",
                job_id=str(job["id"]),
                reason="missing_script",
                path=rel_path,
            )
            return

        content = script_path.read_text()
        digest = hashlib.sha256(content.encode()).hexdigest()
        await self._db.update_job(
            job["id"],
            docker_ci_script_path=rel_path,
            docker_ci_script_sha256=digest,
            docker_ci_script_content=content,
        )
        job["docker_ci_script_path"] = rel_path
        job["docker_ci_script_sha256"] = digest
        job["docker_ci_script_content"] = content
        logger.info(
            "pipeline.docker_ci_snapshot_saved",
            job_id=str(job["id"]),
            path=rel_path,
            sha256=digest,
        )

    def _workspace_already_at_saved_commit(
        self,
        job: dict[str, Any],
        workspace_path: Path,
        git_result: GitResult | None,
    ) -> bool:
        if git_result is None or not git_result.commit_sha:
            return False
        try:
            current_commit = self._git_service.current_commit(workspace_path)
        except Exception as exc:
            logger.warning(
                "pipeline.current_commit_check_failed",
                job_id=str(job["id"]),
                error=redact_secrets(exc),
            )
            return False
        return current_commit == git_result.commit_sha

    async def _handle_pre_push_failure(
        self,
        job: dict[str, Any],
        *,
        source: str,
        issues: list[dict],
        context: str,
    ) -> None:
        failure_signature = local_failure_signature_from_context(source, context, issues)
        failure_streak = _next_local_failure_streak(job, failure_signature["signature"])
        attempt = int(job.get("local_fix_retries") or 0) + 1

        await self._db.update_job(
            job["id"],
            local_fix_retries=attempt,
            local_failure_signature=failure_signature["signature"],
            local_failure_streak=failure_streak,
            local_failure_summary=failure_signature,
        )
        job["local_fix_retries"] = attempt
        job["local_failure_signature"] = failure_signature["signature"]
        job["local_failure_streak"] = failure_streak
        job["local_failure_summary"] = failure_signature

        if failure_streak > self._max_local_same_failure_retries:
            await self._escalate(
                job,
                reason="local_failure_streak_exceeded",
                details=_local_failure_streak_details(context, failure_signature, failure_streak),
            )
            return

        job = await self._set_status(job, JobStatus.FIXING)
        await self._dispatch_fix_and_stop(
            job,
            issues=issues,
            context=context,
        )

    async def _wait_for_ci_and_continue(
        self,
        job: dict[str, Any],
        git_result: GitResult,
        attempt: int,
    ) -> None:
        job = await self._set_status(job, JobStatus.CI_TESTING)
        ci_result = await self._test_service.wait_for_ci(git_result.repo, git_result.commit_sha)
        if ci_result.timed_out:
            await self._escalate(job, reason="ci_timeout_exceeded", details=ci_result.failed_logs)
            return

        if not ci_result.passed:
            issues = ci_result.issues or classify_ci_failure_logs(ci_result.failed_logs)
            failure_signature = ci_failure_signature_from_logs(ci_result.failed_logs, issues)
            failure_streak = _next_ci_failure_streak(job, failure_signature["signature"])
            attempt += 1
            await self._db.update_job(job["id"], ci_fix_retries=attempt)
            job["ci_fix_retries"] = attempt
            await self._db.update_job(
                job["id"],
                ci_failure_signature=failure_signature["signature"],
                ci_failure_streak=failure_streak,
                ci_failure_summary=failure_signature,
            )
            job["ci_failure_signature"] = failure_signature["signature"]
            job["ci_failure_streak"] = failure_streak
            job["ci_failure_summary"] = failure_signature

            if failure_streak > self._max_ci_same_failure_retries:
                await self._escalate(
                    job,
                    reason="ci_failure_streak_exceeded",
                    details=_ci_failure_streak_details(ci_result.failed_logs, failure_signature, failure_streak),
                )
                return

            job = await self._set_status(job, JobStatus.FIXING)
            await self._dispatch_fix_and_stop(
                job,
                issues=issues,
                context="\n".join(ci_result.failed_logs),
            )
            return

        await _reset_ci_failure_streak(self._db, job)
        review_attempt = int(job.get("review_retries") or 0)
        await self._review_after_ci(job, git_result, review_attempt)

    async def _review_after_ci(
        self,
        job: dict[str, Any],
        git_result: GitResult,
        attempt: int,
    ) -> None:
        job = await self._set_status(job, JobStatus.REVIEWING_CODE)
        pr_diff = self._git_service.get_pr_diff(git_result.repo, git_result.branch)
        review = await self._review_service.run(job.get("spec_text") or "", pr_diff)
        await self._db.update_job(job["id"], review_result=_jsonable(review))
        job["review_result"] = _jsonable(review)

        if review.approved:
            job = await self._set_status(job, JobStatus.DONE)
            await self._telegram.send_done(job)
            logger.info("pipeline.done", job_id=str(job["id"]))
            return

        if attempt >= self._max_review_retries:
            await self._escalate(
                job,
                reason="review_retries_exceeded",
                details=_jsonable(review).get("issues", []),
            )
            return

        attempt += 1
        await self._db.update_job(job["id"], review_retries=attempt)
        job["review_retries"] = attempt
        job = await self._set_status(job, JobStatus.FIXING)
        await self._dispatch_fix_and_stop(
            job,
            issues=_issues_as_dicts(review.issues),
            context="",
        )

    async def _dispatch_fix_and_stop(self, job: dict[str, Any], issues: list[dict], context: str) -> None:
        workspace_path = str(self._opencode_workspace_path(job["id"]))
        session_id = await self._coding_service.start_fix(
            str(job["id"]),
            workspace_path,
            issues=issues,
            context=context,
        )
        await self._db.save_opencode_session_id(
            job["id"],
            session_id,
            phase="fixing",
            workspace_path=workspace_path,
        )
        job["opencode_session_id"] = session_id
        await self._set_status(job, JobStatus.FIXING_IN_PROGRESS)

    async def _escalate(self, job: dict[str, Any], reason: str, details: Any) -> None:
        job = await self._set_status(job, JobStatus.AWAITING_HUMAN_ESCALATION)
        try:
            await self._telegram.send_escalation(job, reason=reason, details=details)
        except Exception as exc:
            logger.warning(
                "pipeline.telegram_escalation_failed",
                job_id=str(job["id"]),
                reason=reason,
                error=redact_secrets(exc),
            )
        logger.info("pipeline.awaiting_human_escalation", job_id=str(job["id"]), reason=reason)

    async def _save_git_result(self, job: dict[str, Any], result: GitResult) -> None:
        await self._db.update_job(
            job["id"],
            github_repo=result.repo,
            github_branch=result.branch,
            github_commit=result.commit_sha,
            github_pr_url=result.pr_url,
        )
        job["github_repo"] = result.repo
        job["github_branch"] = result.branch
        job["github_commit"] = result.commit_sha
        job["github_pr_url"] = result.pr_url

    async def _set_status(self, job: dict[str, Any], status: JobStatus) -> dict[str, Any]:
        if job.get("status") == status.value:
            if status in HEARTBEAT_ON_SAME_STATUS:
                await self._db.update_status(job["id"], status.value)
            return job

        await self._db.update_status(job["id"], status.value)
        job["status"] = status.value
        return job

    async def _require_job(self, job_id: str) -> dict[str, Any]:
        job = await self._db.get_job(job_id)
        if not job:
            raise RuntimeError(f"Job not found: {job_id}")
        return job

    def _workspace_path(self, job_id: str) -> Path:
        return self._workspace_root / str(job_id)

    def _opencode_workspace_path(self, job_id: str) -> Path:
        return self._opencode_workspace_root / str(job_id)

    def _ensure_workspace(self, job: dict[str, Any]) -> Path:
        workspace_path = self._workspace_path(job["id"])
        workspace_path.mkdir(parents=True, exist_ok=True)

        files = {
            "REQUEST.md": job.get("request_snapshot") or job.get("input_text") or "",
            "SPEC.md": job.get("spec_text") or "",
            "PLAN.md": job.get("plan_text") or "",
            "TASK.md": _task_text(job),
        }
        for filename, content in files.items():
            path = workspace_path / filename
            if not path.exists():
                path.write_text(content)

        return workspace_path


def _task_text(job: dict[str, Any]) -> str:
    return (
        "# TASK\n\n"
        f"Implement the MVP for job `{job['id']}` using `SPEC.md`, `PLAN.md`, "
        "and `REQUEST.md` as the source of truth.\n\n"
        "Treat `PLAN.md`'s Backend API Contract as authoritative for routes, methods, payloads, "
        "responses, status codes, validation, auth behavior, and persistence side effects. "
        "Implement it directly; do not invent a different backend request contract.\n\n"
        "You still own the concrete smoke-test UI contract: create `mvp.config.json` v2 targets "
        "and flows that match the implemented UI, while keeping every `expect_request` aligned "
        "with the Backend API Contract from `PLAN.md`.\n"
    )


def _git_result_from_job(job: dict[str, Any]) -> GitResult | None:
    if not all(job.get(key) for key in ("github_repo", "github_branch", "github_commit", "github_pr_url")):
        return None
    return GitResult(
        repo=job["github_repo"],
        branch=job["github_branch"],
        commit_sha=job["github_commit"],
        pr_url=job["github_pr_url"],
    )


def local_failure_signature_from_context(
    source: str,
    context: str,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    issues = issues or []
    stage = source or "pre_push"
    module = _infer_local_failure_module(context, issues)
    family = _infer_local_failure_family(context, issues)
    issue_codes = [str(issue.get("code", "")).strip() for issue in issues if issue.get("code")]
    failed_path = _first_issue_path(issues) or _extract_local_failure_path(context)
    signature = ":".join(
        _normalize_signature_part(part)
        for part in ("pre_push", stage, module, family)
        if _normalize_signature_part(part)
    )
    if not signature:
        signature = "pre_push:unknown:generic_failure"

    return {
        "signature": signature,
        "stage": stage,
        "module": module,
        "family": family,
        "issue_codes": issue_codes,
        "failed_path": failed_path,
        "summary": _format_local_failure_signature_summary(stage, module, family),
    }


def _infer_local_failure_module(context: str, issues: list[dict[str, Any]]) -> str:
    text = context.lower()
    issue_text = " ".join(
        str(issue.get(key, "")) for issue in issues for key in ("code", "path", "description")
    ).lower()
    combined = f"{text}\n{issue_text}"

    if any(keyword in combined for keyword in ("mvp.config.json", "smoke_flow", "app_url", "api_url")):
        return "runtime_config"
    if any(keyword in combined for keyword in ("prisma", "migration.sql", "schema.prisma", "migrate")):
        return "backend_prisma"
    if any(keyword in combined for keyword in ("docker", "compose", "dockerfile", "container", "image tag")):
        return "docker_runtime"
    if any(keyword in combined for keyword in ("backend", "server", "api/", "requirements.txt", "pytest")):
        return "backend"
    if any(
        keyword in combined for keyword in ("frontend", "src/app", "app/", "vite", "next", "react", "css", "tailwind")
    ):
        return "frontend"
    if any(keyword in combined for keyword in ("postgres", "postgresql", "database")):
        return "database"
    if "redis" in combined:
        return "redis"
    return "unknown"


def _infer_local_failure_family(context: str, issues: list[dict[str, Any]]) -> str:
    issue_codes = [str(issue.get("code", "")).strip() for issue in issues if issue.get("code")]
    if issue_codes:
        return issue_codes[0]

    text = context.lower()
    unknown_smoke_action = re.search(r"unknown smoke action:\s*([a-z0-9_-]+)", context, re.IGNORECASE)
    if unknown_smoke_action:
        return f"unknown_smoke_action_{unknown_smoke_action.group(1)}"
    if any(
        keyword in text for keyword in ("no matching distribution found", "could not find a version that satisfies")
    ):
        return "python_dependency_resolution"
    if any(keyword in text for keyword in ("npm err! code etarget", "no matching version found")):
        return "node_dependency_resolution"
    if "cannot find module" in text:
        return "missing_module"
    if any(keyword in text for keyword in ("typescript", "tsc", "type error")):
        return "typescript_compile"
    if any(keyword in text for keyword in ("eslint", "lint")):
        return "lint_failure"
    if any(keyword in text for keyword in ("syntaxerror", "syntax error")):
        return "syntax_error"
    if any(keyword in text for keyword in ("pytest", "traceback")):
        return "pytest_failure"
    if any(keyword in text for keyword in ("timeout", "timed out")):
        return "timeout"
    if any(keyword in text for keyword in ("expected url", "expect_url", "assertionerror", "assertion failed")):
        return "assertion_failed"
    if any(keyword in text for keyword in ("unhealthy", "healthcheck")):
        return "container_healthcheck"
    if any(keyword in text for keyword in ("econnrefused", "connection refused")):
        return "connection_refused"
    return "generic_failure"


def _first_issue_path(issues: list[dict[str, Any]]) -> str:
    for issue in issues:
        path = str(issue.get("path") or "").strip()
        if path:
            line = issue.get("line")
            return f"{path}:{line}" if line else path
    return ""


def _extract_local_failure_path(context: str) -> str:
    path_match = re.search(r"([A-Za-z0-9_./-]+\.(?:py|js|jsx|ts|tsx|vue|svelte|sql|json|ya?ml))(?::(\d+))?", context)
    if not path_match:
        return ""
    path = path_match.group(1)
    line = path_match.group(2)
    return f"{path}:{line}" if line else path


def _format_local_failure_signature_summary(stage: str, module: str, family: str) -> str:
    return f"stage={stage}; module={module}; family={family}"


def _next_local_failure_streak(job: dict[str, Any], signature: str) -> int:
    previous_signature = str(job.get("local_failure_signature") or "")
    previous_streak = int(job.get("local_failure_streak") or 0)
    if signature and signature == previous_signature:
        return previous_streak + 1
    return 1


def _next_ci_failure_streak(job: dict[str, Any], signature: str) -> int:
    previous_signature = str(job.get("ci_failure_signature") or "")
    previous_streak = int(job.get("ci_failure_streak") or 0)
    if signature and signature == previous_signature:
        return previous_streak + 1
    return 1


async def _reset_local_failure_streak(db: Any, job: dict[str, Any]) -> None:
    if not (
        job.get("local_failure_signature")
        or int(job.get("local_failure_streak") or 0)
        or job.get("local_failure_summary") is not None
    ):
        return

    await db.update_job(
        job["id"],
        local_failure_signature=None,
        local_failure_streak=0,
        local_failure_summary=None,
    )
    job["local_failure_signature"] = None
    job["local_failure_streak"] = 0
    job["local_failure_summary"] = None


async def _reset_ci_failure_streak(db: Any, job: dict[str, Any]) -> None:
    if not (
        job.get("ci_failure_signature")
        or int(job.get("ci_failure_streak") or 0)
        or job.get("ci_failure_summary") is not None
    ):
        return

    await db.update_job(
        job["id"],
        ci_failure_signature=None,
        ci_failure_streak=0,
        ci_failure_summary=None,
    )
    job["ci_failure_signature"] = None
    job["ci_failure_streak"] = 0
    job["ci_failure_summary"] = None


def _local_failure_streak_details(
    context: str,
    failure_signature: dict[str, Any],
    failure_streak: int,
) -> list[str]:
    header = (
        "Pre-push checks failed repeatedly with the same failure signature.\n"
        f"Consecutive repeats: {failure_streak}\n"
        f"Signature: {failure_signature.get('signature')}\n"
        f"Summary: {failure_signature.get('summary')}"
    )
    return [header, context[-12000:]]


def _ci_failure_streak_details(
    failed_logs: list[str],
    failure_signature: dict[str, Any],
    failure_streak: int,
) -> list[str]:
    header = (
        "CI failed repeatedly with the same failure signature.\n"
        f"Consecutive repeats: {failure_streak}\n"
        f"Signature: {failure_signature.get('signature')}\n"
        f"Summary: {failure_signature.get('summary')}"
    )
    return [header, *failed_logs]


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _issues_as_dicts(issues: list[Any]) -> list[dict]:
    return [_jsonable(issue) for issue in issues]


def _normalize_signature_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80]
