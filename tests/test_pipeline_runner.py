from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pipeline.runner import PipelineRunner
from pipeline.runner import local_failure_signature_from_context
from services.coding_service import OpenCodeRunState
from services.git_service import GitResult, NoChangesError
from services.quality_gate_service import QualityGateIssue, QualityGateResult
from services.review_service import ReviewResult
from services.smoke_test_service import SmokeTestResult
from services.test_service import CIResult, TestResult, ci_failure_signature_from_logs


@pytest.fixture
def services(tmp_path):
    db = AsyncMock()
    spec = AsyncMock()
    plan = AsyncMock()
    coding = AsyncMock()
    test = MagicMock()
    test.wait_for_ci = AsyncMock()
    git = MagicMock()
    review = AsyncMock()
    telegram = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.run.return_value = QualityGateResult(passed=True, output="ok", issues=[])
    coding.start_fix.return_value = "sess-fix"
    coding.check_state.return_value = OpenCodeRunState.COMPLETED

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
        max_review_retries=1,
        max_test_retries=1,
        workspace_root=tmp_path,
    )
    return runner, db, spec, plan, coding, test, git, review, telegram, tmp_path


@pytest.fixture
def split_workspace_services(tmp_path):
    db = AsyncMock()
    spec = AsyncMock()
    plan = AsyncMock()
    coding = AsyncMock()
    test = MagicMock()
    test.wait_for_ci = AsyncMock()
    git = MagicMock()
    review = AsyncMock()
    telegram = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.run.return_value = QualityGateResult(passed=True, output="ok", issues=[])
    coding.start_fix.return_value = "sess-fix"
    coding.check_state.return_value = OpenCodeRunState.COMPLETED
    opencode_root = tmp_path / "docker-workspaces"

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
        max_review_retries=1,
        max_test_retries=1,
        workspace_root=tmp_path / "host-workspaces",
        opencode_workspace_root=opencode_root,
    )
    return runner, db, coding, tmp_path / "host-workspaces", opencode_root


async def test_run_from_queued_stops_at_human_approval(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "queued",
        "input_text": "Build a CRM dashboard",
        "chat_id": 1,
        "telegram_user_id": 2,
    }
    db.get_job.return_value = job
    spec.generate.return_value = "spec"
    plan.generate.return_value = "plan"

    await runner.run("job-1")

    spec.generate.assert_called_once_with("Build a CRM dashboard")
    plan.generate.assert_called_once_with("spec")
    telegram.send_approval_request.assert_called_once()
    coding.start.assert_not_called()
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses == [
        "loading_request",
        "drafting_spec",
        "planning",
        "awaiting_human_approval",
    ]


async def test_run_from_planning_uses_human_feedback_for_replan(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "planning",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "old plan",
        "approval_feedback": "change API contract",
    }
    db.get_job.return_value = job
    plan.generate.return_value = "revised plan"

    await runner.run("job-1")

    plan.generate.assert_called_once_with(
        "spec",
        feedback="change API contract",
        previous_plan="old plan",
    )
    db.update_job.assert_called_once_with("job-1", plan_text="revised plan")
    telegram.send_approval_request.assert_called_once()
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses == ["awaiting_human_approval"]


async def test_run_from_preparing_workspace_completes_successfully(services):
    runner, db, spec, plan, coding, test, git, review, telegram, tmp_path = services
    job = {
        "id": "job-1",
        "status": "preparing_workspace",
        "input_text": "t1",
        "request_snapshot": "Build a CRM dashboard",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.start.return_value = "sess-1"
    coding.check_state.return_value = OpenCodeRunState.COMPLETED
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(
        approved=True,
        summary="ok",
        issues=[],
        tests_present=True,
    )

    await runner.run("job-1")
    coding.check_state.assert_not_called()

    workspace = tmp_path / "job-1"
    (workspace / ".pipeline").mkdir()
    (workspace / ".pipeline" / "docker-ci.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\ndocker compose config\ndocker compose build\ndocker compose up -d\n"
    )

    await runner.run("job-1")

    assert (workspace / "REQUEST.md").read_text() == "Build a CRM dashboard"
    assert (workspace / "SPEC.md").read_text() == "spec"
    assert (workspace / "PLAN.md").read_text() == "plan"
    assert "Backend API Contract as authoritative" in (workspace / "TASK.md").read_text()
    assert "expect_request` aligned" in (workspace / "TASK.md").read_text()
    coding.start.assert_called_once_with("job-1", str(workspace))
    coding.check_state.assert_called_once_with("job-1", "sess-1")
    db.save_opencode_session_id.assert_called_once_with(
        "job-1",
        "sess-1",
        phase="coding",
        workspace_path=str(workspace),
    )
    git.push_and_create_pr.assert_called_once()
    test.wait_for_ci.assert_called_once_with("owner/repo", "sha1")
    review.run.assert_called_once_with("spec", "diff")
    telegram.send_done.assert_called_once()
    docker_ci_updates = [
        call.kwargs for call in db.update_job.call_args_list if "docker_ci_script_content" in call.kwargs
    ]
    assert docker_ci_updates
    assert docker_ci_updates[-1]["docker_ci_script_path"] == ".pipeline/docker-ci.sh"
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[-1] == "done"


async def test_run_from_coding_prepares_workspace_when_poller_claimed_job(services):
    runner, db, spec, plan, coding, test, git, review, telegram, tmp_path = services
    job = {
        "id": "job-1",
        "status": "coding",
        "input_text": "t1",
        "request_snapshot": "Build a CRM dashboard",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.start.return_value = "sess-1"
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(
        approved=True,
        summary="ok",
        issues=[],
        tests_present=True,
    )

    await runner.run("job-1")

    workspace = tmp_path / "job-1"
    assert (workspace / "REQUEST.md").read_text() == "Build a CRM dashboard"
    assert (workspace / "SPEC.md").read_text() == "spec"
    coding.start.assert_called_once_with("job-1", str(workspace))
    coding.check_state.assert_not_called()
    test.run_local.assert_not_called()


async def test_run_from_preparing_workspace_stops_while_coding_in_progress(services):
    runner, db, spec, plan, coding, test, git, review, telegram, tmp_path = services
    job = {
        "id": "job-1",
        "status": "preparing_workspace",
        "input_text": "t1",
        "request_snapshot": "Build a CRM dashboard",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.start.return_value = "sess-1"

    await runner.run("job-1")

    workspace = tmp_path / "job-1"
    coding.start.assert_called_once_with("job-1", str(workspace))
    coding.check_state.assert_not_called()
    test.run_local.assert_not_called()
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[-1] == "coding_in_progress"


async def test_run_sends_opencode_workspace_path_when_roots_are_split(split_workspace_services):
    runner, db, coding, host_root, opencode_root = split_workspace_services
    job = {
        "id": "job-1",
        "status": "preparing_workspace",
        "input_text": "t1",
        "request_snapshot": "Build a CRM dashboard",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.start.return_value = "sess-1"

    await runner.run("job-1")

    assert (host_root / "job-1" / "SPEC.md").read_text() == "spec"
    coding.start.assert_called_once_with("job-1", str(opencode_root / "job-1"))


async def test_run_from_coding_in_progress_resumes_after_restart(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "coding_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.RUNNING

    await runner.run("job-1")

    coding.start.assert_not_called()
    coding.check_state.assert_called_once_with("job-1", "sess-1")
    db.touch_job.assert_called_once_with("job-1")
    test.run_local.assert_not_called()


async def test_run_dispatches_detached_fix_after_local_test_failure(services):
    runner, db, spec, plan, coding, test, git, review, telegram, tmp_path = services
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=False, output="failed tests")

    await runner.run("job-1")

    coding.start_fix.assert_called_once()
    git.push_and_create_pr.assert_not_called()
    telegram.send_escalation.assert_not_called()
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[-1] == "fixing_in_progress"


async def test_run_from_local_testing_heartbeats_same_status_before_work(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=False, output="failed tests")

    await runner.run("job-1")

    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[0] == "local_testing"
    coding.start_fix.assert_called_once()


async def test_run_from_local_testing_escalates_after_same_pre_push_failure_streak(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    output = "npm run build failed: Cannot find module './dist/main.js'"
    signature = local_failure_signature_from_context("local_tests", output)["signature"]
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
        "local_fix_retries": 1,
        "local_failure_signature": signature,
        "local_failure_streak": 1,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=False, output=output)

    await runner.run("job-1")

    coding.start_fix.assert_not_called()
    git.push_and_create_pr.assert_not_called()
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "local_failure_streak_exceeded"
    db.update_job.assert_any_call(
        "job-1",
        local_fix_retries=2,
        local_failure_signature=signature,
        local_failure_streak=2,
        local_failure_summary=local_failure_signature_from_context("local_tests", output),
    )


async def test_run_from_local_testing_resets_streak_for_new_pre_push_failure_signature(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    previous_signature = local_failure_signature_from_context(
        "quality_gate",
        "placeholder detected",
        [{"code": "placeholder_code", "path": "frontend/src/App.tsx"}],
    )["signature"]
    output = "npm ERR! code ETARGET No matching version found for replicate@0.27.1"
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
        "local_fix_retries": 7,
        "local_failure_signature": previous_signature,
        "local_failure_streak": 7,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=False, output=output)

    await runner.run("job-1")

    coding.start_fix.assert_called_once()
    telegram.send_escalation.assert_not_called()
    signature_updates = [
        call.kwargs for call in db.update_job.call_args_list if call.kwargs.get("local_failure_signature")
    ]
    assert signature_updates[-1]["local_failure_streak"] == 1
    assert signature_updates[-1]["local_failure_signature"] != previous_signature
    assert signature_updates[-1]["local_fix_retries"] == 8


async def test_run_from_local_testing_resets_pre_push_streak_after_checks_pass(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    signature = local_failure_signature_from_context(
        "quality_gate",
        "placeholder detected",
        [{"code": "placeholder_code", "path": "frontend/src/App.tsx"}],
    )["signature"]
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 0,
        "local_fix_retries": 3,
        "local_failure_signature": signature,
        "local_failure_streak": 3,
        "local_failure_summary": {"signature": signature},
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    db.update_job.assert_any_call(
        "job-1",
        local_failure_signature=None,
        local_failure_streak=0,
        local_failure_summary=None,
    )
    git.push_and_create_pr.assert_called_once()
    telegram.send_done.assert_called_once()


async def test_run_from_fixing_in_progress_waits_when_opencode_is_busy(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.RUNNING

    await runner.run("job-1")

    coding.check_state.assert_called_once_with("job-1", "sess-fix")
    db.touch_job.assert_called_once_with("job-1")
    test.run_local.assert_not_called()


async def test_run_from_fixing_in_progress_escalates_opencode_model_error(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "opencode_stuck_retries": 1,
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.MODEL_ERROR

    await runner.run("job-1")

    coding.abort.assert_called_once_with("sess-fix")
    db.update_job.assert_any_call("job-1", opencode_session_id=None, opencode_stuck_retries=0)
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "opencode_model_error"
    test.run_local.assert_not_called()
    git.push_and_create_pr.assert_not_called()


async def test_run_from_fixing_in_progress_recovers_from_stuck_opencode_session(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "opencode_stuck_retries": 0,
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.STUCK
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    coding.abort.assert_called_once_with("sess-fix")
    db.update_job.assert_any_call("job-1", opencode_stuck_retries=1, opencode_session_id=None)
    test.run_local.assert_called_once()
    telegram.send_done.assert_called_once()


async def test_run_from_fixing_in_progress_escalates_after_stuck_retry_limit(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "opencode_stuck_retries": 2,
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.STUCK

    await runner.run("job-1")

    coding.abort.assert_called_once_with("sess-fix")
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "opencode_stuck_retries_exceeded"
    test.run_local.assert_not_called()


async def test_run_from_fixing_in_progress_recovers_from_waiting_permission(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "opencode_stuck_retries": 0,
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.WAITING_PERMISSION
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    coding.abort.assert_called_once_with("sess-fix")
    db.update_job.assert_any_call("job-1", opencode_stuck_retries=1, opencode_session_id=None)
    test.run_local.assert_called_once()


async def test_run_from_fixing_in_progress_returns_to_local_testing(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "fixing_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-fix",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.COMPLETED
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    coding.check_state.assert_called_once_with("job-1", "sess-fix")
    db.update_job.assert_any_call("job-1", opencode_session_id=None, opencode_stuck_retries=0)
    test.run_local.assert_called_once()
    git.push_and_create_pr.assert_called_once()
    telegram.send_done.assert_called_once()


async def test_ci_polling_timeout_escalates_without_retry_increment(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "review_retries": 1,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=["CI polling timeout exceeded"],
        timed_out=True,
    )

    await runner.run("job-1")

    db.touch_job.assert_not_called()
    assert not any(call.kwargs.get("review_retries") == 2 for call in db.update_job.call_args_list)
    review.run.assert_not_called()
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "ci_timeout_exceeded"
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[-1] == "awaiting_human_escalation"


async def test_run_from_ci_testing_reuses_saved_commit_and_dispatches_fix(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=["docker-runtime failed"],
    )

    await runner.run("job-1")

    test.run_local.assert_not_called()
    git.force_push.assert_not_called()
    git.push_and_create_pr.assert_not_called()
    test.wait_for_ci.assert_called_once_with("owner/repo", "sha-existing")
    db.update_job.assert_any_call("job-1", ci_fix_retries=1)
    coding.start_fix.assert_called_once()
    assert "docker-runtime failed" in coding.start_fix.call_args.kwargs["context"]
    telegram.send_escalation.assert_not_called()


async def test_run_from_ci_testing_classifies_auth_redirect_failure_for_fix_prompt(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=[
            """
browser smoke failure diagnostics
failing_step: smoke_flow[6]
action: expect_url
payload: {"action": "expect_url", "url": "**/clients"}
current_url: http://localhost:3000/login?redirect=%2Fclients
localStorage:
- salesflow_token: ***
cookies:
- {"domain": "localhost", "name": "salesflow_token", "path": "/"}
recent_network:
- {"event": "response", "method": "POST", "status": 201, "url": "http://localhost:8080/api/auth/login"}
- {"event": "response", "method": "GET", "status": 307, "url": "http://localhost:3000/clients?_rsc=abc"}
"""
        ],
    )

    await runner.run("job-1")

    issues = coding.start_fix.call_args.kwargs["issues"]
    assert issues[0]["code"] == "protected_route_auth_redirect_after_login"
    assert issues[0]["severity"] == "critical"
    assert "middleware.ts" in "\n".join(issues[0]["related_checks"])
    assert "Playwright" in issues[0]["acceptance_checks"][0]
    telegram.send_escalation.assert_not_called()


async def test_run_from_ci_testing_passes_generic_ci_failure_issue_to_fix_prompt(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 99,
        "ci_fix_retries": 0,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=[
            """
CI: failure (https://github.com/owner/repo/actions/runs/1)
- docker-runtime: failure
  - Start Docker runtime: failure
backend-1 | Error: Cannot find module './dist/main.js'
dependency failed to start: container backend-1 exited (1)
"""
        ],
    )

    await runner.run("job-1")

    issues = coding.start_fix.call_args.kwargs["issues"]
    assert issues[0]["code"] == "ci_failure_diagnostic"
    assert any("Cannot find module" in item for item in issues[0]["evidence"])
    assert any("docker-compose.yml" in item for item in issues[0]["related_checks"])
    telegram.send_escalation.assert_not_called()


async def test_run_from_ci_testing_escalates_after_same_ci_failure_streak(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    failed_logs = ["docker-runtime still failed"]
    signature = ci_failure_signature_from_logs(failed_logs)["signature"]
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
        "ci_fix_retries": 1,
        "ci_failure_signature": signature,
        "ci_failure_streak": 1,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=failed_logs,
    )

    await runner.run("job-1")

    coding.start_fix.assert_not_called()
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "ci_failure_streak_exceeded"


async def test_run_from_ci_testing_resets_streak_for_new_failure_signature(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    previous_logs = [
        """
CI: failure
- docker-build: failure
  - Build backend image: failure
ERROR: Could not find a version that satisfies the requirement replicate==0.27.1
"""
    ]
    failed_logs = [
        """
CI: failure
- readiness: failure
  - API readiness check: failure
curl http://localhost:8080/health returned 404; real endpoint is /api/health
"""
    ]
    previous_signature = ci_failure_signature_from_logs(previous_logs)["signature"]
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
        "ci_fix_retries": 99,
        "ci_failure_signature": previous_signature,
        "ci_failure_streak": 4,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=failed_logs,
    )

    await runner.run("job-1")

    coding.start_fix.assert_called_once()
    telegram.send_escalation.assert_not_called()
    signature_updates = [
        call.kwargs for call in db.update_job.call_args_list if call.kwargs.get("ci_failure_signature")
    ]
    assert signature_updates[-1]["ci_failure_streak"] == 1
    assert signature_updates[-1]["ci_failure_signature"] != previous_signature


async def test_run_from_ci_testing_does_not_consume_review_retry_budget_for_ci_fix(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 99,
        "ci_fix_retries": 0,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=["docker-runtime failed"],
    )

    await runner.run("job-1")

    db.update_job.assert_any_call("job-1", ci_fix_retries=1)
    coding.start_fix.assert_called_once()
    telegram.send_escalation.assert_not_called()


async def test_run_from_git_push_reuses_saved_commit_when_workspace_already_pushed(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "git_push",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
        "ci_fix_retries": 1,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.force_push.side_effect = NoChangesError("workspace has no staged changes")
    git.current_commit.return_value = "sha-existing"
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    git.force_push.assert_called_once()
    git.current_commit.assert_called_once()
    test.wait_for_ci.assert_called_once_with("owner/repo", "sha-existing")
    telegram.send_escalation.assert_not_called()
    telegram.send_done.assert_called_once()
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert "ci_testing" in statuses
    assert statuses[-1] == "done"


async def test_run_from_git_push_escalates_no_changes_when_saved_commit_differs(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "git_push",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.force_push.side_effect = NoChangesError("workspace has no staged changes")
    git.current_commit.return_value = "different-sha"

    await runner.run("job-1")

    test.wait_for_ci.assert_not_called()
    telegram.send_escalation.assert_called_once()
    assert telegram.send_escalation.call_args.kwargs["reason"] == "opencode_empty_result"


async def test_run_restores_frozen_docker_ci_before_force_push(services):
    runner, db, spec, plan, coding, test, git, review, telegram, tmp_path = services
    frozen_script = (
        "#!/usr/bin/env bash\nset -euo pipefail\ndocker compose config\ndocker compose build\ndocker compose up -d\n"
    )
    job = {
        "id": "job-1",
        "status": "git_push",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 0,
        "docker_ci_script_path": ".pipeline/docker-ci.sh",
        "docker_ci_script_sha256": "saved-sha",
        "docker_ci_script_content": frozen_script,
    }
    workspace = tmp_path / "job-1"
    (workspace / ".pipeline").mkdir(parents=True)
    (workspace / ".pipeline" / "docker-ci.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.force_push.return_value = "sha2"
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    assert (workspace / ".pipeline" / "docker-ci.sh").read_text() == frozen_script
    git.force_push.assert_called_once()
    telegram.send_done.assert_called_once()


async def test_escalation_delivery_failure_does_not_fail_pipeline(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    failed_logs = ["docker-runtime still failed"]
    signature = ci_failure_signature_from_logs(failed_logs)["signature"]
    job = {
        "id": "job-1",
        "status": "ci_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "github_repo": "owner/repo",
        "github_branch": "job_job-1",
        "github_commit": "sha-existing",
        "github_pr_url": "https://github.com/owner/repo/pull/1",
        "review_retries": 1,
        "ci_fix_retries": 1,
        "ci_failure_signature": signature,
        "ci_failure_streak": 1,
    }
    db.get_job.return_value = job
    test.wait_for_ci.return_value = CIResult(
        passed=False,
        failed_logs=failed_logs,
    )
    telegram.send_escalation.side_effect = RuntimeError("Telegram 400")

    await runner.run("job-1")

    telegram.send_escalation.assert_called_once()
    telegram.send_error.assert_not_called()
    assert not any(call.kwargs.get("status") == "failed" for call in db.update_job.call_args_list)
    statuses = [call.args[1] for call in db.update_status.call_args_list]
    assert statuses[-1] == "awaiting_human_escalation"


async def test_run_from_coding_in_progress_resets_stuck_retries_after_completion(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "coding_in_progress",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-code",
        "opencode_stuck_retries": 2,
        "review_retries": 0,
    }
    db.get_job.return_value = job
    coding.check_state.return_value = OpenCodeRunState.COMPLETED
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    test.wait_for_ci.return_value = CIResult(passed=True, failed_logs=[])
    git.get_pr_diff.return_value = "diff"
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)

    await runner.run("job-1")

    db.update_job.assert_any_call("job-1", opencode_session_id=None, opencode_stuck_retries=0)
    test.run_local.assert_called_once()


async def test_run_records_error_and_sends_telegram_error(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "queued",
        "input_text": " ",
    }
    db.get_job.return_value = job

    await runner.run("job-1")

    db.update_job.assert_called_with(
        "job-1",
        status="failed",
        error_step="loading_request",
        error_message="Job input_text is empty",
    )
    telegram.send_error.assert_called_once()


async def test_run_redacts_secret_values_from_error_messages(services):
    runner, db, spec, plan, coding, test, git, review, telegram, _ = services
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")
    git.push_and_create_pr.side_effect = RuntimeError(
        "fatal: unable to access 'https://github_pat_SECRET123@github.com/owner/repo.git'"
    )

    await runner.run("job-1")

    error_message = db.update_job.call_args.kwargs["error_message"]
    assert "github_pat_SECRET123" not in error_message
    assert "https://***@github.com/owner/repo.git" in error_message
    telegram.send_error.assert_called_once()
    assert "github_pat_SECRET123" not in telegram.send_error.call_args.args[2]


async def test_quality_gate_failure_is_fixed_before_push(tmp_path):
    db = AsyncMock()
    coding = AsyncMock()
    coding.start_fix.return_value = "sess-fix"
    test = MagicMock()
    test.wait_for_ci = AsyncMock(return_value=CIResult(passed=True, failed_logs=[]))
    git = MagicMock()
    git.push_and_create_pr.return_value = GitResult(
        repo="owner/repo",
        branch="job_job-1",
        commit_sha="sha1",
        pr_url="https://github.com/owner/repo/pull/1",
    )
    git.get_pr_diff.return_value = "diff"
    review = AsyncMock()
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)
    telegram = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.run.side_effect = [
        QualityGateResult(
            passed=False,
            output="placeholder detected",
            issues=[
                QualityGateIssue(
                    severity="major",
                    code="placeholder_code",
                    description="placeholder",
                    path="frontend/src/App.tsx",
                    line=10,
                )
            ],
        ),
        QualityGateResult(passed=True, output="ok", issues=[]),
    ]
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-1",
        "review_retries": 0,
    }
    db.get_job.return_value = job
    test.run_local.return_value = TestResult(passed=True, output="ok")

    runner = PipelineRunner(
        db=db,
        spec_service=AsyncMock(),
        plan_service=AsyncMock(),
        coding_service=coding,
        test_service=test,
        git_service=git,
        review_service=review,
        telegram_service=telegram,
        quality_gate_service=quality_gate,
        max_review_retries=1,
        max_test_retries=2,
        workspace_root=tmp_path,
    )

    await runner.run("job-1")

    coding.start_fix.assert_called_once()
    assert "placeholder detected" in coding.start_fix.call_args.kwargs["context"]
    test.run_local.assert_not_called()
    signature_updates = [
        call.kwargs for call in db.update_job.call_args_list if call.kwargs.get("local_failure_signature")
    ]
    assert signature_updates[-1]["local_fix_retries"] == 1
    assert signature_updates[-1]["local_failure_streak"] == 1
    assert signature_updates[-1]["local_failure_summary"]["stage"] == "quality_gate"
    assert signature_updates[-1]["local_failure_summary"]["issue_codes"] == ["placeholder_code"]
    assert quality_gate.run.call_count == 1
    git.push_and_create_pr.assert_not_called()
    telegram.send_done.assert_not_called()


async def test_smoke_test_failure_is_fixed_before_push(tmp_path):
    db = AsyncMock()
    coding = AsyncMock()
    coding.start_fix.return_value = "sess-fix"
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
    git.get_pr_diff.return_value = "diff"
    review = AsyncMock()
    review.run.return_value = ReviewResult(approved=True, summary="ok", issues=[], tests_present=True)
    telegram = AsyncMock()
    quality_gate = MagicMock()
    quality_gate.run.return_value = QualityGateResult(passed=True, output="ok", issues=[])
    smoke_test = MagicMock()
    smoke_test.run.side_effect = [
        SmokeTestResult(passed=False, output="login failed"),
        SmokeTestResult(passed=True, output="ok"),
    ]
    job = {
        "id": "job-1",
        "status": "local_testing",
        "input_text": "t1",
        "spec_text": "spec",
        "plan_text": "plan",
        "opencode_session_id": "sess-1",
        "review_retries": 0,
    }
    db.get_job.return_value = job

    runner = PipelineRunner(
        db=db,
        spec_service=AsyncMock(),
        plan_service=AsyncMock(),
        coding_service=coding,
        test_service=test,
        git_service=git,
        review_service=review,
        telegram_service=telegram,
        quality_gate_service=quality_gate,
        smoke_test_service=smoke_test,
        max_review_retries=1,
        max_test_retries=2,
        workspace_root=tmp_path,
    )

    await runner.run("job-1")

    coding.start_fix.assert_called_once()
    assert "login failed" in coding.start_fix.call_args.kwargs["context"]
    assert smoke_test.run.call_count == 1
    git.push_and_create_pr.assert_not_called()
    telegram.send_done.assert_not_called()
