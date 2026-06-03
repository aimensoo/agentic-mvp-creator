from unittest.mock import AsyncMock, MagicMock

import pytest

from pipeline.runner import PipelineRunner
from services.coding_service import OpenCodeRunState
from services.git_service import GitResult
from services.quality_gate_service import QualityGateResult
from services.review_service import ReviewIssue, ReviewResult
from services.test_service import CIResult, TestResult


class FakeDb:
    def __init__(self, job: dict):
        self.job = job
        self.statuses = []
        self.updated_fields = []

    async def connect(self):
        pass

    async def close(self):
        pass

    async def get_job(self, job_id: str):
        return dict(self.job) if self.job["id"] == job_id else None

    async def update_status(self, job_id: str, status: str):
        self.job["status"] = status
        self.statuses.append(status)

    async def touch_job(self, job_id: str):
        self.updated_fields.append({"touched": True})

    async def update_job(self, job_id: str, **fields):
        self.job.update(fields)
        self.updated_fields.append(fields)

    async def save_opencode_session_id(
        self,
        job_id: str,
        session_id: str,
        *,
        phase: str | None = None,
        workspace_path: str | None = None,
    ):
        self.job["opencode_session_id"] = session_id
        history = self.job.setdefault("opencode_session_history", [])
        history.append(
            {
                "session_id": session_id,
                "phase": phase,
                "workspace_path": workspace_path,
            }
        )
        self.updated_fields.append({"opencode_session_id": session_id})


def _runner(db: FakeDb, tmp_path, *, max_review_retries: int = 1) -> tuple:
    spec = AsyncMock()
    spec.generate.return_value = "spec"
    plan = AsyncMock()
    plan.generate.return_value = "plan"
    coding = AsyncMock()
    coding.start.return_value = "sess-1"
    coding.start_fix.return_value = "sess-fix"
    coding.check_state.return_value = OpenCodeRunState.COMPLETED
    test = MagicMock()
    test.run_local.return_value = TestResult(passed=True, output="ok")
    test.wait_for_ci = AsyncMock(return_value=CIResult(passed=True, failed_logs=[]))
    git = MagicMock()
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    git.force_push.return_value = "sha2"
    git.get_pr_diff.return_value = "diff"
    review = AsyncMock()
    telegram = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.run.return_value = QualityGateResult(passed=True, output="ok", issues=[])

    runner = PipelineRunner(
        db=db,
        spec_service=spec,
        plan_service=plan,
        coding_service=coding,
        test_service=test,
        git_service=git,
        review_service=review,
        telegram_service=telegram,
        quality_gate_service=quality_gate,
        max_review_retries=max_review_retries,
        max_test_retries=1,
        workspace_root=tmp_path,
    )
    return runner, spec, plan, coding, test, git, review, telegram


async def test_pipeline_full_flow_stops_for_approval_then_completes(tmp_path):
    db = FakeDb(
        {
            "id": "job-1",
            "status": "queued",
            "input_text": "t1",
            "chat_id": 123,
            "telegram_user_id": 456,
            "review_retries": 0,
        }
    )
    runner, spec, plan, coding, test, git, review, telegram = _runner(db, tmp_path)
    review.run.return_value = ReviewResult(
        approved=True,
        summary="approved",
        issues=[],
        tests_present=True,
    )

    await runner.run("job-1")

    assert db.job["status"] == "awaiting_human_approval"
    telegram.send_approval_request.assert_called_once()

    db.job["status"] = "preparing_workspace"
    await runner.run("job-1")

    assert db.job["status"] == "coding_in_progress"

    await runner.run("job-1")

    assert db.job["status"] == "done"
    assert db.statuses == [
        "loading_request",
        "drafting_spec",
        "planning",
        "awaiting_human_approval",
        "coding",
        "coding_in_progress",
        "local_testing",
        "local_testing",
        "git_push",
        "ci_testing",
        "ci_testing",
        "reviewing_code",
        "done",
    ]
    coding.start.assert_called_once()
    coding.check_state.assert_called_once()
    git.push_and_create_pr.assert_called_once()
    review.run.assert_called_once()
    telegram.send_done.assert_called_once()


async def test_pipeline_review_failure_runs_fix_and_force_pushes(tmp_path):
    db = FakeDb(
        {
            "id": "job-1",
            "status": "preparing_workspace",
            "input_text": "t1",
            "request_snapshot": "Build a CRM dashboard",
            "spec_text": "spec",
            "plan_text": "plan",
            "review_retries": 0,
        }
    )
    runner, spec, plan, coding, test, git, review, telegram = _runner(db, tmp_path)
    review.run.side_effect = [
        ReviewResult(
            approved=False,
            summary="fix required",
            issues=[ReviewIssue(severity="high", description="bug")],
            tests_present=True,
        ),
        ReviewResult(
            approved=True,
            summary="approved",
            issues=[],
            tests_present=True,
        ),
    ]

    await runner.run("job-1")
    await runner.run("job-1")
    assert db.job["status"] == "fixing_in_progress"
    coding.start_fix.assert_called_once()

    await runner.run("job-1")

    git.force_push.assert_called_once()
    assert db.job["github_commit"] == "sha2"
    assert db.job["review_retries"] == 1
    assert db.job["status"] == "done"


async def test_pipeline_escalates_when_review_retry_limit_is_exhausted(tmp_path):
    db = FakeDb(
        {
            "id": "job-1",
            "status": "preparing_workspace",
            "input_text": "t1",
            "request_snapshot": "Build a CRM dashboard",
            "spec_text": "spec",
            "plan_text": "plan",
            "review_retries": 0,
        }
    )
    runner, spec, plan, coding, test, git, review, telegram = _runner(
        db,
        tmp_path,
        max_review_retries=0,
    )
    review.run.return_value = ReviewResult(
        approved=False,
        summary="not approved",
        issues=[ReviewIssue(severity="critical", description="unsafe")],
        tests_present=True,
    )

    await runner.run("job-1")
    await runner.run("job-1")

    assert db.job["status"] == "awaiting_human_escalation"
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "review_retries_exceeded"
