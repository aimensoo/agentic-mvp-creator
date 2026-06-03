import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import subprocess

import httpx

from services.test_service import (
    CIResult,
    TestResult,
    TestService,
    ci_failure_signature_from_logs,
    classify_ci_failure_logs,
)


@pytest.fixture
def service():
    return TestService(
        github_token="ghp_test",
        polling_timeout=300,
        polling_interval=1,
    )


def test_classify_ci_failure_logs_detects_auth_redirect_after_login():
    issues = classify_ci_failure_logs(
        [
            """
browser smoke failure diagnostics
failing_step: smoke_flow[6]
action: expect_url
payload: {"action": "expect_url", "url": "**/clients"}
current_url: http://localhost:3000/login?redirect=%2Fclients
localStorage:
- salesflow_token: ***
- salesflow_user: {"email":"manager@demo.com"}
sessionStorage:
- <empty>
cookies:
- {"domain": "localhost", "httpOnly": false, "name": "salesflow_token", "path": "/"}
recent_network:
- {"event": "response", "method": "POST", "status": 201, "url": "http://localhost:8080/api/auth/login"}
- {"event": "response", "method": "GET", "status": 307, "url": "http://localhost:3000/clients?_rsc=abc"}
"""
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "protected_route_auth_redirect_after_login"
    assert issue["severity"] == "critical"
    assert issue["target_path"] == "/clients"
    assert any("/api/auth/login" in item for item in issue["evidence"])
    assert any("middleware.ts" in item for item in issue["related_checks"])
    assert any("Playwright flow" in item for item in issue["acceptance_checks"])


def test_classify_ci_failure_logs_detects_prisma_migration_sql_syntax_error():
    issues = classify_ci_failure_logs(
        [
            """
backend-1 | Applying migration `20240101000001_partial_task_index`
backend-1 | Error: P3018
backend-1 |
backend-1 | Migration name: 20240101000001_partial_task_index
backend-1 |
backend-1 | Database error code: 42601
backend-1 | Database error:
backend-1 | ERROR: syntax error at or near "#"
backend-1 |
backend-1 |   1 # Please make sure to read the migration rules before editing this file.
dependency failed to start: container backend-1 exited (1)
"""
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "prisma_migration_sql_syntax_error"
    assert issue["severity"] == "critical"
    assert issue["migration_name"] == "20240101000001_partial_task_index"
    assert any("migration.sql" in item for item in issue["related_checks"])
    assert any("docker compose up -d" in item for item in issue["acceptance_checks"])


def test_classify_ci_failure_logs_falls_back_to_generic_structured_issue():
    issues = classify_ci_failure_logs(
        [
            """
CI: failure (https://github.com/owner/repo/actions/runs/1)
- docker-runtime: failure
  - Start Docker runtime: failure

--- logs: docker-runtime ---
Run docker compose -f "$MVP_COMPOSE_FILE" up -d
Container salesflow-backend-1 Error
dependency failed to start: container salesflow-backend-1 exited (1)
backend-1 | Error: Cannot find module './dist/main.js'
##[error]Process completed with exit code 1.
"""
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "ci_failure_diagnostic"
    assert issue["severity"] == "critical"
    assert any("docker-runtime" in item for item in issue["evidence"])
    assert any("Cannot find module" in item for item in issue["evidence"])
    assert any("docker-compose.yml" in item for item in issue["related_checks"])
    assert any("Docker runtime verification" in item for item in issue["acceptance_checks"])


def test_ci_failure_signature_groups_same_stage_module_and_family():
    first = ci_failure_signature_from_logs(
        [
            """
CI: failure
- docker-build: failure
  - Build backend image: failure
Run docker compose build backend
ERROR: Could not find a version that satisfies the requirement replicate==0.27.1
"""
        ]
    )
    second = ci_failure_signature_from_logs(
        [
            """
CI: failure
- docker-build: failure
  - Build backend image: failure
Run docker compose build backend
ERROR: No matching distribution found for scikit-learn==1.4.1
"""
        ]
    )

    assert first["signature"] == second["signature"]
    assert first["stage"] == "docker_build"
    assert first["module"] == "backend"
    assert first["family"] == "python_dependency_resolution"


def test_ci_failure_signature_separates_runtime_from_readiness_checks():
    runtime = ci_failure_signature_from_logs(
        [
            """
CI: failure
- docker-runtime: failure
  - Start Docker runtime: failure
Run docker compose up -d
Container app-backend-1 is unhealthy
"""
        ]
    )
    readiness = ci_failure_signature_from_logs(
        [
            """
CI: failure
- readiness: failure
  - API readiness check: failure
curl http://localhost:8080/health returned 404; real endpoint is /api/health
"""
        ]
    )

    assert runtime["signature"] != readiness["signature"]
    assert runtime["stage"] == "docker_runtime"
    assert readiness["stage"] == "readiness_check"


def test_classify_ci_failure_logs_detects_unknown_smoke_action():
    issues = classify_ci_failure_logs(
        [
            """
browser smoke failure diagnostics
failing_step: primary_entity.create_flow[0]
action: open
error: SmokeStepError: primary_entity.create_flow[0]: open step failed. error=AssertionError: Unknown smoke action: open
"""
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "unknown_smoke_action"
    assert issue["action"] == "open"
    assert issue["failing_step"] == "primary_entity.create_flow[0]"
    assert any("mvp.config.json" in item for item in issue["related_checks"])


def test_classify_ci_failure_logs_detects_frontend_empty_terminal_dom_after_backend_preview():
    issues = classify_ci_failure_logs(
        [
            """
browser smoke failure diagnostics
failing_step: flows.primary.steps[4]
action: wait_for_outcome
payload: {"action": "wait_for_outcome", "success": {"target": "download_pdf"}, "failure": {"target": "error_message"}}
error: TimeoutError: Timed out waiting for outcome success target: download_pdf
current_url: http://localhost:3000/
page_title: Paint by Numbers Generator
body_text_tail:
Paint by Numbers Generator
Upload an image
localStorage:
- <empty>
sessionStorage:
- <empty>
cookies:
- <empty>
console_errors:
- <empty>
failed_requests:
- <empty>
responses_4xx_5xx:
- <empty>
recent_network:
- {"event": "response", "method": "POST", "status": 201, "url": "http://localhost:8000/api/jobs"}
- {"event": "response", "method": "GET", "status": 200, "url": "http://localhost:8000/api/jobs/job_123"}
- {"event": "response", "method": "GET", "status": 200, "url": "http://localhost:8000/api/jobs/job_123/preview"}
html_tail:
<html><head><title>Paint by Numbers Generator</title></head><body><div id="root"><main><h1>Paint by Numbers Generator</h1><section data-testid="result"></section></main></div></body></html>
Traceback (most recent call last):
"""
        ]
    )

    assert len(issues) == 1
    issue = issues[0]
    assert issue["code"] == "frontend_empty_terminal_dom"
    assert issue["severity"] == "critical"
    assert issue["success_target"] == "download_pdf"
    assert issue["failure_target"] == "error_message"
    assert any("POST /api/jobs" in item for item in issue["evidence"])
    assert any("/preview" in item for item in issue["evidence"])
    assert any("useJobPolling" in item for item in issue["related_checks"])


def test_ci_failure_signature_uses_frontend_empty_terminal_dom_semantics():
    signature = ci_failure_signature_from_logs(
        [
            """
browser smoke failure diagnostics
failing_step: flows.primary.steps[4]
action: wait_for_outcome
payload: {"action": "wait_for_outcome", "success": {"target": "download_pdf"}, "failure": {"target": "error_message"}}
error: TimeoutError: Timed out waiting for outcome success target: download_pdf
console_errors:
- <empty>
failed_requests:
- <empty>
responses_4xx_5xx:
- <empty>
recent_network:
- {"event": "response", "method": "POST", "status": 201, "url": "http://localhost:8000/api/jobs"}
- {"event": "response", "method": "GET", "status": 200, "url": "http://localhost:8000/api/jobs/job_123/preview"}
html_tail:
<html><body><div id="root"><main><section data-testid="result"></section></main></div></body></html>
"""
        ]
    )

    assert signature["signature"] == "browser_smoke:frontend:empty_terminal_dom"
    assert signature["stage"] == "browser_smoke"
    assert signature["module"] == "frontend"
    assert signature["family"] == "empty_terminal_dom"
    assert signature["issue_codes"] == ["frontend_empty_terminal_dom"]
    assert "Backend completed enough to serve preview" in signature["summary"]


def test_ci_failure_signature_separates_unknown_smoke_action_values():
    open_signature = ci_failure_signature_from_logs(
        [
            """
browser smoke failure diagnostics
failing_step: primary_entity.create_flow[0]
action: open
error: AssertionError: Unknown smoke action: open
"""
        ]
    )
    upload_signature = ci_failure_signature_from_logs(
        [
            """
browser smoke failure diagnostics
failing_step: primary_entity.create_flow[1]
action: upload_file
error: AssertionError: Unknown smoke action: upload_file
"""
        ]
    )

    assert open_signature["signature"] != upload_signature["signature"]
    assert open_signature["family"] == "unknown_smoke_action_open"
    assert upload_signature["family"] == "unknown_smoke_action_upload_file"


class TestRunLocal:
    def test_passed(self, service, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1 passed"
        mock_result.stderr = ""

        with patch("services.test_service.subprocess.run", return_value=mock_result) as mock_run:
            result = service.run_local(str(tmp_path))

        assert isinstance(result, TestResult)
        assert result.passed is True
        assert "1 passed" in result.output
        assert mock_run.call_count == 2

    def test_failed(self, service, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        mock_install = MagicMock()
        mock_install.returncode = 0
        mock_test = MagicMock()
        mock_test.returncode = 1
        mock_test.stdout = ""
        mock_test.stderr = "FAILED test_x - AssertionError"

        with patch("services.test_service.subprocess.run", side_effect=[mock_install, mock_test]):
            result = service.run_local(str(tmp_path))

        assert result.passed is False
        assert "FAILED" in result.output

    def test_install_fails(self, service, tmp_path):
        (tmp_path / "requirements.txt").write_text("nonexistent-pkg==99")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ERROR: Could not find"

        with patch("services.test_service.subprocess.run", return_value=mock_result):
            result = service.run_local(str(tmp_path))

        assert result.passed is False
        assert "Could not find" in result.output

    def test_captures_stdout_and_stderr(self, service, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        mock_install = MagicMock(returncode=0, stdout="ok", stderr="")
        mock_test = MagicMock(returncode=0, stdout="2 passed", stderr="warnings")

        with patch("services.test_service.subprocess.run", side_effect=[mock_install, mock_test]):
            result = service.run_local(str(tmp_path))

        assert "2 passed" in result.output

    def test_node_root_test_is_used_when_package_json_exists(self, service, tmp_path):
        (tmp_path / "requirements.txt").write_text("")
        (tmp_path / "package.json").write_text('{"scripts":{"test":"echo node-ok"}}')
        mock_test = subprocess.CompletedProcess(
            args=["npm", "test"],
            returncode=0,
            stdout="node tests passed",
            stderr="",
        )

        with patch("services.test_service.subprocess.run", return_value=mock_test) as mock_run:
            result = service.run_local(str(tmp_path))

        assert result.passed is True
        assert "node tests passed" in result.output
        mock_run.assert_called_once()
        assert mock_run.call_args.args[0] == ["npm", "test"]

    def test_node_runs_root_test_and_child_build(self, service, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (tmp_path / "package.json").write_text('{"scripts":{"test":"npm run backend:test"}}')
        (frontend / "package.json").write_text('{"scripts":{"build":"vite build"}}')

        mock_root_test = subprocess.CompletedProcess(
            args=["npm", "test"],
            returncode=0,
            stdout="backend tests passed",
            stderr="",
        )
        mock_frontend_build = subprocess.CompletedProcess(
            args=["npm", "run", "build"],
            returncode=0,
            stdout="frontend built",
            stderr="",
        )

        with patch(
            "services.test_service.subprocess.run", side_effect=[mock_root_test, mock_frontend_build]
        ) as mock_run:
            result = service.run_local(str(tmp_path))

        assert result.passed is True
        assert "backend tests passed" in result.output
        assert "frontend built" in result.output
        assert mock_run.call_args_list[0].args[0] == ["npm", "test"]
        assert mock_run.call_args_list[1].args[0] == ["npm", "run", "build"]
        assert mock_run.call_args_list[1].kwargs["cwd"] == str(frontend)

    def test_node_install_failure_is_reported(self, service, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts":{"test":"jest"},"devDependencies":{"jest":"^29.0.0"}}')
        (tmp_path / "package-lock.json").write_text("{}")
        mock_install = subprocess.CompletedProcess(
            args=["npm", "ci"],
            returncode=1,
            stdout="",
            stderr="npm ci failed",
        )

        with patch("services.test_service.subprocess.run", return_value=mock_install):
            result = service.run_local(str(tmp_path))

        assert result.passed is False
        assert "npm ci failed" in result.output

    def test_node_without_test_or_build_fails_with_clear_message(self, service, tmp_path):
        (tmp_path / "package.json").write_text('{"scripts":{"dev":"vite"}}')

        with patch("services.test_service.subprocess.run") as mock_run:
            result = service.run_local(str(tmp_path))

        assert result.passed is False
        assert "No runnable Node checks" in result.output
        mock_run.assert_not_called()

    def test_node_checks_can_run_inside_opencode_container(self, tmp_path):
        workspace_root = tmp_path / "host-workspaces"
        workspace = workspace_root / "job-1"
        workspace.mkdir(parents=True)
        (workspace / "package.json").write_text('{"scripts":{"test":"echo ok"}}')
        service = TestService(
            github_token="ghp_test",
            workspace_root=workspace_root,
            container_workspace_root="/workspaces",
            node_test_container_service="opencode",
            docker_compose_project_dir="/project",
        )
        mock_test = subprocess.CompletedProcess(
            args=["docker", "compose", "exec", "-T", "opencode", "sh", "-lc", "cd /workspaces/job-1 && npm test"],
            returncode=0,
            stdout="container node tests passed",
            stderr="",
        )

        with patch("services.test_service.subprocess.run", return_value=mock_test) as mock_run:
            result = service.run_local(str(workspace))

        assert result.passed is True
        assert "container node tests passed" in result.output
        command = mock_run.call_args.args[0]
        assert command[:5] == ["docker", "compose", "exec", "-T", "opencode"]
        assert command[-1] == "cd /workspaces/job-1 && npm test"
        assert mock_run.call_args.kwargs["cwd"] == "/project"
        assert "# cwd: /workspaces/job-1" in result.output


class TestWaitForCI:
    async def test_ci_passed(self, service):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "total_count": 2,
            "workflow_runs": [
                {"status": "completed", "conclusion": "success", "name": "lint"},
                {"status": "completed", "conclusion": "success", "name": "test"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.test_service.httpx.AsyncClient", return_value=mock_client):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert isinstance(result, CIResult)
        assert result.passed is True
        assert result.failed_logs == []
        mock_client.get.assert_called_once()
        assert mock_client.get.call_args.args[0] == "https://api.github.com/repos/owner/repo/actions/runs"
        assert mock_client.get.call_args.kwargs["params"] == {"head_sha": "sha123", "per_page": 100}

    async def test_ci_failed(self, service):
        mock_resp_pending = MagicMock()
        mock_resp_pending.json.return_value = {
            "total_count": 1,
            "workflow_runs": [{"status": "in_progress", "conclusion": None, "name": "test"}],
        }
        mock_resp_pending.raise_for_status = MagicMock()

        mock_resp_done = MagicMock()
        mock_resp_done.json.return_value = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "failure",
                    "name": "test",
                    "html_url": "https://github.com/owner/repo/actions/runs/1",
                    "jobs_url": "https://api.github.com/repos/owner/repo/actions/runs/1/jobs",
                },
            ],
        }
        mock_resp_done.raise_for_status = MagicMock()

        mock_resp_jobs = MagicMock()
        mock_resp_jobs.json.return_value = {
            "jobs": [
                {
                    "id": 100,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Checkout", "status": "completed", "conclusion": "success"},
                        {"name": "Run tests", "status": "completed", "conclusion": "failure"},
                    ],
                },
            ],
        }
        mock_resp_jobs.raise_for_status = MagicMock()

        mock_resp_logs = MagicMock()
        mock_resp_logs.text = "docker compose logs\nPrisma failed to detect the libssl/openssl version\n"
        mock_resp_logs.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_pending, mock_resp_done, mock_resp_jobs, mock_resp_logs])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("services.test_service.httpx.AsyncClient", return_value=mock_client),
            patch("services.test_service.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is False
        assert len(result.failed_logs) > 0
        assert "test: failure" in result.failed_logs[0]
        assert "https://github.com/owner/repo/actions/runs/1" in result.failed_logs[0]
        assert "- test: failure" in result.failed_logs[0]
        assert "Run tests: failure" in result.failed_logs[0]
        assert "Prisma failed to detect the libssl/openssl version" in result.failed_logs[0]
        assert (
            mock_client.get.call_args_list[-1].args[0]
            == "https://api.github.com/repos/owner/repo/actions/jobs/100/logs"
        )
        assert mock_client.get.call_args_list[-1].kwargs["follow_redirects"] is True

    async def test_ci_failed_uses_failed_step_block_instead_of_job_tail(self, service):
        mock_resp_done = MagicMock()
        mock_resp_done.json.return_value = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "id": 1,
                    "status": "completed",
                    "conclusion": "failure",
                    "name": "CI",
                    "html_url": "https://github.com/owner/repo/actions/runs/1",
                    "jobs_url": "https://api.github.com/repos/owner/repo/actions/runs/1/jobs",
                },
            ],
        }
        mock_resp_done.raise_for_status = MagicMock()

        mock_resp_jobs = MagicMock()
        mock_resp_jobs.json.return_value = {
            "jobs": [
                {
                    "id": 100,
                    "name": "docker-runtime",
                    "status": "completed",
                    "conclusion": "failure",
                    "steps": [
                        {"name": "Start Docker runtime", "status": "completed", "conclusion": "success"},
                        {"name": "Run browser smoke flow", "status": "completed", "conclusion": "failure"},
                        {"name": "Upload browser smoke artifacts", "status": "completed", "conclusion": "success"},
                    ],
                },
            ],
        }
        mock_resp_jobs.raise_for_status = MagicMock()

        failed_step_log = "\n".join(
            [
                "2026-05-14T07:00:00.0000000Z ##[group]Run set -euo pipefail",
                "2026-05-14T07:00:00.0000000Z python -m playwright install --with-deps chromium",
                *[f"2026-05-14T07:00:00.0000000Z install noise {index}" for index in range(200)],
                "2026-05-14T07:00:00.0000000Z browser smoke failure diagnostics",
                "2026-05-14T07:00:00.0000000Z failing_step: primary_entity.create_flow[0]",
                "2026-05-14T07:00:00.0000000Z action: open",
                "2026-05-14T07:00:00.0000000Z error: AssertionError: Unknown smoke action: open",
                "2026-05-14T07:00:00.0000000Z ##[error]Process completed with exit code 1.",
            ]
        )
        cleanup_log = "\n".join(
            [
                "2026-05-14T07:00:01.0000000Z ##[group]Run actions/upload-artifact@v4",
                *[f"2026-05-14T07:00:01.0000000Z cleanup tail {index}" for index in range(1500)],
            ]
        )
        mock_resp_logs = MagicMock()
        mock_resp_logs.text = f"{failed_step_log}\n{cleanup_log}"
        mock_resp_logs.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_done, mock_resp_jobs, mock_resp_logs])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.test_service.httpx.AsyncClient", return_value=mock_client):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is False
        assert "--- failed step log: docker-runtime -> Run browser smoke flow ---" in result.failed_logs[0]
        assert "browser smoke failure diagnostics" in result.failed_logs[0]
        assert "Unknown smoke action: open" in result.failed_logs[0]
        assert "Run actions/upload-artifact@v4" not in result.failed_logs[0]
        assert result.issues[0]["code"] == "unknown_smoke_action"

    async def test_ci_timeout(self, service):
        service._polling_timeout = 0
        service._polling_grace_timeout = 0

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "total_count": 1,
            "workflow_runs": [{"status": "in_progress", "conclusion": None}],
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.test_service.httpx.AsyncClient", return_value=mock_client):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is False
        assert result.timed_out is True
        assert "timeout" in result.failed_logs[0].lower()

    async def test_ci_primary_timeout_rechecks_and_detects_failure(self, service):
        service._polling_timeout = 0
        service._polling_grace_timeout = 300

        mock_resp_pending = MagicMock()
        mock_resp_pending.json.return_value = {
            "total_count": 1,
            "workflow_runs": [{"status": "in_progress", "conclusion": None, "name": "CI"}],
        }
        mock_resp_pending.raise_for_status = MagicMock()

        mock_resp_failed = MagicMock()
        mock_resp_failed.json.return_value = {
            "total_count": 1,
            "workflow_runs": [
                {
                    "status": "completed",
                    "conclusion": "failure",
                    "name": "CI",
                    "html_url": "https://github.com/owner/repo/actions/runs/1",
                }
            ],
        }
        mock_resp_failed.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_resp_pending, mock_resp_failed])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.test_service.httpx.AsyncClient", return_value=mock_client):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is False
        assert result.timed_out is False
        assert "CI: failure" in result.failed_logs[0]
        assert mock_client.get.call_count == 2

    async def test_ci_polling_retries_transient_github_error(self, service):
        mock_resp_done = MagicMock()
        mock_resp_done.json.return_value = {
            "total_count": 1,
            "workflow_runs": [{"status": "completed", "conclusion": "success", "name": "test"}],
        }
        mock_resp_done.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[httpx.ReadTimeout(""), mock_resp_done])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("services.test_service.httpx.AsyncClient", return_value=mock_client),
            patch("services.test_service.asyncio.sleep", new_callable=AsyncMock) as sleep,
        ):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is True
        assert result.timed_out is False
        sleep.assert_called_once_with(1)

    async def test_ci_polling_timeout_after_github_error_returns_timed_out(self, service):
        service._polling_timeout = 0
        service._polling_grace_timeout = 0

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ReadTimeout(""))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("services.test_service.httpx.AsyncClient", return_value=mock_client):
            result = await service.wait_for_ci("owner/repo", "sha123")

        assert result.passed is False
        assert result.timed_out is True
        assert "GitHub API error: ReadTimeout" in result.failed_logs[0]
