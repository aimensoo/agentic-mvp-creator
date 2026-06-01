import asyncio
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from utils.logger import get_logger

logger = get_logger(__name__)


class TestResult(BaseModel):
    passed: bool
    output: str


class CIResult(BaseModel):
    passed: bool
    failed_logs: list[str]
    timed_out: bool = False
    issues: list[dict[str, Any]] = Field(default_factory=list)


MAX_CI_JOB_LOG_CHARS = 12000
MAX_CI_FAILED_STEP_LOG_CHARS = 60000
AUTH_REDIRECT_ISSUE_CODE = "protected_route_auth_redirect_after_login"
PRISMA_MIGRATION_SQL_SYNTAX_ISSUE_CODE = "prisma_migration_sql_syntax_error"
UNKNOWN_SMOKE_ACTION_ISSUE_CODE = "unknown_smoke_action"
FRONTEND_EMPTY_TERMINAL_DOM_ISSUE_CODE = "frontend_empty_terminal_dom"
GENERIC_CI_FAILURE_ISSUE_CODE = "ci_failure_diagnostic"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
GITHUB_LOG_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[0-9:.]+Z\s*")
GITHUB_STEP_GROUP_RE = re.compile(r"(?m)^##\[group\]Run .*$")
SMOKE_DIAGNOSTIC_SECTIONS = (
    "body_text_tail",
    "localStorage",
    "sessionStorage",
    "cookies",
    "console_errors",
    "failed_requests",
    "responses_4xx_5xx",
    "recent_network",
    "html_tail",
)


class TestService:
    def __init__(
        self,
        github_token: str,
        polling_timeout: int = 600,
        polling_grace_timeout: int = 300,
        polling_interval: int = 15,
        workspace_root: str | Path = "workspaces",
        container_workspace_root: str | None = None,
        node_test_container_service: str = "",
        docker_compose_project_dir: str | Path = ".",
    ):
        self._github_token = github_token
        self._polling_timeout = polling_timeout
        self._polling_grace_timeout = polling_grace_timeout
        self._polling_interval = polling_interval
        self._workspace_root = Path(workspace_root)
        self._container_workspace_root = PurePosixPath(container_workspace_root) if container_workspace_root else None
        self._node_test_container_service = node_test_container_service
        self._docker_compose_project_dir = Path(docker_compose_project_dir)

    def run_local(self, workspace_path: str) -> TestResult:
        logger.info("test_service.run_local", workspace=workspace_path)
        workspace = Path(workspace_path)

        if _find_package_dirs(workspace):
            return self._run_node(workspace)

        return self._run_python(workspace)

    def _run_python(self, workspace: Path) -> TestResult:
        if (workspace / "requirements.txt").exists():
            install = _run_command(
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                workspace,
            )
            if install.returncode != 0:
                output = _command_output(install)
                logger.error("test_service.install_failed", project_type="python", output=output[:500])
                return TestResult(passed=False, output=output)

        test = _run_command(
            [sys.executable, "-m", "pytest", "--tb=short", "-q"],
            workspace,
        )

        passed = test.returncode == 0
        output = _command_output(test)
        logger.info(
            "test_service.run_local_done",
            project_type="python",
            passed=passed,
            output_len=len(output),
        )
        return TestResult(passed=passed, output=output.strip())

    def _run_node(self, workspace: Path) -> TestResult:
        package_dirs = _find_package_dirs(workspace)
        output_parts = []

        for package_dir in package_dirs:
            package_json = _read_package_json(package_dir)
            if not _needs_node_install(package_dir, package_json):
                continue

            install = self._run_node_command(_node_install_command(package_dir), package_dir)
            output_parts.append(_format_command_result(_display_cwd(install, package_dir), install))
            if install.returncode != 0:
                output = "\n\n".join(output_parts)
                logger.error("test_service.install_failed", project_type="node", output=output[:500])
                return TestResult(passed=False, output=output)

        check_commands = _node_check_commands(workspace, package_dirs)
        if not check_commands:
            output = "No runnable Node checks found. Expected package.json scripts such as test or build."
            logger.error("test_service.no_local_checks", project_type="node")
            return TestResult(passed=False, output=output)

        for package_dir, command in check_commands:
            result = self._run_node_command(command, package_dir)
            output_parts.append(_format_command_result(_display_cwd(result, package_dir), result))
            if result.returncode != 0:
                output = "\n\n".join(output_parts)
                logger.info(
                    "test_service.run_local_done",
                    project_type="node",
                    passed=False,
                    output_len=len(output),
                )
                return TestResult(passed=False, output=output.strip())

        output = "\n\n".join(output_parts)
        logger.info(
            "test_service.run_local_done",
            project_type="node",
            passed=True,
            output_len=len(output),
        )
        return TestResult(passed=True, output=output.strip())

    def _run_node_command(self, command: list[str], package_dir: Path) -> subprocess.CompletedProcess[str]:
        if not self._node_test_container_service:
            return _run_command(command, package_dir)

        container_cwd = self._container_path(package_dir)
        shell_command = f"cd {shlex.quote(str(container_cwd))} && {_shell_join(command)}"
        docker_command = [
            "docker",
            "compose",
            "exec",
            "-T",
            self._node_test_container_service,
            "sh",
            "-lc",
            shell_command,
        ]
        result = _run_command(docker_command, self._docker_compose_project_dir)
        result.display_cwd = str(container_cwd)
        return result

    def _container_path(self, host_path: Path) -> PurePosixPath:
        if not self._container_workspace_root:
            raise RuntimeError("container_workspace_root is required for containerized Node tests")

        try:
            relative = host_path.resolve().relative_to(self._workspace_root.resolve())
        except ValueError:
            relative = host_path.name

        if isinstance(relative, Path):
            return self._container_workspace_root / PurePosixPath(relative.as_posix())
        return self._container_workspace_root / str(relative)

    async def wait_for_ci(self, repo: str, sha: str) -> CIResult:
        logger.info("test_service.wait_for_ci", repo=repo, sha=sha)
        url = f"https://api.github.com/repos/{repo}/actions/runs"
        headers = {
            "Authorization": f"Bearer {self._github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {
            "head_sha": sha,
            "per_page": 100,
        }

        deadline = time.monotonic() + self._polling_timeout
        phase = "primary"
        last_polling_error: Exception | None = None
        last_runs: list[dict[str, Any]] = []
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    response = await client.get(url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()
                    last_polling_error = None
                except Exception as exc:
                    last_polling_error = exc
                    logger.warning(
                        "test_service.ci_polling_request_failed",
                        repo=repo,
                        sha=sha,
                        error=_format_exception(exc),
                    )
                    if time.monotonic() >= deadline:
                        timeout_result = _advance_ci_timeout_phase(
                            repo=repo,
                            sha=sha,
                            phase=phase,
                            primary_timeout=self._polling_timeout,
                            grace_timeout=self._polling_grace_timeout,
                            last_runs=last_runs,
                            last_polling_error=last_polling_error,
                        )
                        if timeout_result is not None:
                            return timeout_result

                        phase = "grace"
                        deadline = time.monotonic() + self._polling_grace_timeout
                        continue
                    await asyncio.sleep(self._polling_interval)
                    continue

                runs = data.get("workflow_runs", [])
                last_runs = runs

                result = await _completed_ci_result(client, runs, headers)
                if result is not None:
                    return result

                if time.monotonic() >= deadline:
                    timeout_result = _advance_ci_timeout_phase(
                        repo=repo,
                        sha=sha,
                        phase=phase,
                        primary_timeout=self._polling_timeout,
                        grace_timeout=self._polling_grace_timeout,
                        last_runs=last_runs,
                        last_polling_error=last_polling_error,
                    )
                    if timeout_result is not None:
                        return timeout_result

                    phase = "grace"
                    deadline = time.monotonic() + self._polling_grace_timeout
                    continue

                await asyncio.sleep(self._polling_interval)


async def _completed_ci_result(
    client: httpx.AsyncClient,
    runs: list[dict[str, Any]],
    headers: dict[str, str],
) -> CIResult | None:
    if not runs:
        return None

    all_completed = all(run.get("status") == "completed" for run in runs)
    if not all_completed:
        return None

    failed_logs = []
    for run in runs:
        if run.get("conclusion") != "success":
            failed_logs.append(
                await _build_workflow_run_failure_log(
                    client,
                    run,
                    headers,
                )
            )

    if not failed_logs:
        logger.info("test_service.ci_passed")
        return CIResult(passed=True, failed_logs=[])

    issues = classify_ci_failure_logs(failed_logs)
    logger.warning("test_service.ci_failed", failures=len(failed_logs), structured_issues=len(issues))
    return CIResult(passed=False, failed_logs=failed_logs, issues=issues)


def classify_ci_failure_logs(failed_logs: list[str]) -> list[dict[str, Any]]:
    text = "\n\n".join(log for log in failed_logs if log)
    if not text:
        return []

    issues = []
    auth_redirect_issue = _classify_auth_redirect_after_login(text)
    if auth_redirect_issue:
        issues.append(auth_redirect_issue)
    prisma_migration_issue = _classify_prisma_migration_sql_syntax_error(text)
    if prisma_migration_issue:
        issues.append(prisma_migration_issue)
    unknown_smoke_action_issue = _classify_unknown_smoke_action(text)
    if unknown_smoke_action_issue:
        issues.append(unknown_smoke_action_issue)
    frontend_empty_dom_issue = _classify_frontend_empty_terminal_dom(text)
    if frontend_empty_dom_issue:
        issues.append(frontend_empty_dom_issue)
    if issues:
        return issues

    generic_issue = _classify_generic_ci_failure(text)
    return [generic_issue] if generic_issue else []


def ci_failure_signature_from_logs(
    failed_logs: list[str],
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    text = "\n\n".join(log for log in failed_logs if log)
    issues = issues if issues is not None else classify_ci_failure_logs(failed_logs)
    issue_codes = [str(issue.get("code", "")).strip() for issue in issues if issue.get("code")]

    stage = _infer_ci_failure_stage(text, issues)
    module = _infer_ci_failure_module(text, issues)
    family = _infer_ci_failure_family(text, issues)
    signature = ":".join(
        _normalize_signature_part(part) for part in (stage, module, family) if _normalize_signature_part(part)
    )
    if not signature:
        signature = "ci:unknown:generic_failure"

    clean_lines = [_clean_ci_line(line) for line in text.splitlines()]
    clean_lines = [line for line in clean_lines if line]

    return {
        "signature": signature,
        "stage": stage,
        "module": module,
        "family": family,
        "issue_codes": issue_codes,
        "failed_path": _extract_failed_ci_path(clean_lines),
        "summary": _format_ci_failure_signature_summary(stage, module, family),
    }


def _classify_prisma_migration_sql_syntax_error(text: str) -> dict[str, Any] | None:
    if "Error: P3018" not in text:
        return None
    if "syntax error at or near" not in text:
        return None
    if "Migration name:" not in text and "prisma/migrations" not in text:
        return None

    migration_match = re.search(r"Migration name:\s*([^\s]+)", text)
    migration_name = migration_match.group(1) if migration_match else "unknown migration"
    token_match = re.search(r"syntax error at or near\s+\"([^\"]+)\"", text)
    token = token_match.group(1) if token_match else "invalid SQL token"

    evidence = [
        "Docker runtime failed before browser smoke because the backend container exited during Prisma migration deploy.",
        f"Prisma reported P3018 for migration `{migration_name}`.",
        f"PostgreSQL reported syntax error at or near `{token}`.",
    ]
    if token == "#":
        evidence.append(
            "A migration.sql file likely contains shell/Markdown-style `#` comments; PostgreSQL SQL comments must use `--` or `/* ... */`."
        )

    return {
        "severity": "critical",
        "code": PRISMA_MIGRATION_SQL_SYNTAX_ISSUE_CODE,
        "description": (
            "Docker runtime cannot start because Prisma migration SQL is invalid. "
            f"Fix `{migration_name}` and any related Prisma migration files; do not bypass `prisma migrate deploy`."
        ),
        "evidence": evidence,
        "related_checks": [
            f"Inspect backend/prisma/migrations/{migration_name}/migration.sql.",
            "Replace non-SQL comments such as `# ...` with valid PostgreSQL comments (`-- ...`) or remove them.",
            "Verify every committed Prisma migration.sql file is valid PostgreSQL SQL and can run on a clean database.",
            "Check backend Dockerfile/start command still initializes schema with `prisma migrate deploy` or an equivalent production-safe path.",
        ],
        "acceptance_checks": [
            "Run Docker runtime startup from a clean checkout/database: docker compose config, docker compose build, docker compose up -d.",
            "Run the configured seed command and browser smoke flow after the backend container is healthy.",
        ],
        "migration_name": migration_name,
    }


def _infer_ci_failure_stage(text: str, issues: list[dict[str, Any]]) -> str:
    lower = text.lower()
    codes = {str(issue.get("code", "")) for issue in issues}

    if AUTH_REDIRECT_ISSUE_CODE in codes:
        return "browser_smoke"
    if PRISMA_MIGRATION_SQL_SYNTAX_ISSUE_CODE in codes:
        return "docker_runtime"
    if UNKNOWN_SMOKE_ACTION_ISSUE_CODE in codes:
        return "browser_smoke"
    if FRONTEND_EMPTY_TERMINAL_DOM_ISSUE_CODE in codes:
        return "browser_smoke"
    if any(keyword in lower for keyword in ("browser smoke", "playwright", "smoke_flow")):
        return "browser_smoke"
    if any(keyword in lower for keyword in ("readiness", "api check", "health endpoint")):
        return "readiness_check"
    if any(
        keyword in lower
        for keyword in (
            "docker compose build",
            "docker/build-push-action",
            "failed to solve",
            "failed to build",
            "build image",
            "backend image",
            "frontend image",
        )
    ):
        return "docker_build"
    if any(
        keyword in lower
        for keyword in (
            "docker compose up",
            "dependency failed to start",
            "container",
            "healthcheck",
            "unhealthy",
        )
    ):
        return "docker_runtime"
    if any(keyword in lower for keyword in ("npm run build", "tsc", "typescript", "vite build", "next build")):
        return "app_build"
    if any(keyword in lower for keyword in ("pytest", "traceback", "assertionerror")):
        return "test"

    clean_lines = [_clean_ci_line(line) for line in text.splitlines()]
    failed_path = _extract_failed_ci_path([line for line in clean_lines if line])
    if failed_path:
        return failed_path.split(" -> ", 1)[0]
    return "ci"


def _infer_ci_failure_module(text: str, issues: list[dict[str, Any]]) -> str:
    lower = text.lower()
    codes = {str(issue.get("code", "")) for issue in issues}

    if AUTH_REDIRECT_ISSUE_CODE in codes:
        return "auth"
    if PRISMA_MIGRATION_SQL_SYNTAX_ISSUE_CODE in codes:
        return "backend_prisma"
    if UNKNOWN_SMOKE_ACTION_ISSUE_CODE in codes:
        return "runtime_config"
    if FRONTEND_EMPTY_TERMINAL_DOM_ISSUE_CODE in codes:
        return "frontend"
    if any(keyword in lower for keyword in ("prisma", "schema.prisma", "migration.sql", "migrate deploy")):
        return "backend_prisma"
    if any(keyword in lower for keyword in ("backend", "server", "api/", "requirements.txt", "pip install")):
        return "backend"
    if any(keyword in lower for keyword in ("frontend", "next", "vite", "src/app", "app/", "playwright")):
        return "frontend"
    if any(keyword in lower for keyword in ("postgres", "postgresql", "database", "db-")):
        return "database"
    if "redis" in lower:
        return "redis"
    if any(keyword in lower for keyword in ("mvp.config.json", "app_url", "api_url", "health")):
        return "runtime_config"
    return "unknown"


def _infer_ci_failure_family(text: str, issues: list[dict[str, Any]]) -> str:
    lower = text.lower()
    codes = {str(issue.get("code", "")) for issue in issues}

    if AUTH_REDIRECT_ISSUE_CODE in codes:
        return "auth_redirect_after_login"
    if PRISMA_MIGRATION_SQL_SYNTAX_ISSUE_CODE in codes:
        return "prisma_migration_sql_syntax"
    if FRONTEND_EMPTY_TERMINAL_DOM_ISSUE_CODE in codes:
        return "empty_terminal_dom"
    unknown_smoke_action = re.search(r"Unknown smoke action:\s*([A-Za-z0-9_-]+)", text)
    if unknown_smoke_action:
        return f"unknown_smoke_action_{unknown_smoke_action.group(1)}"
    if any(
        keyword in lower
        for keyword in (
            "no matching distribution found",
            "could not find a version that satisfies",
            "requirements.txt",
        )
    ):
        return "python_dependency_resolution"
    if any(keyword in lower for keyword in ("npm err! code etarget", "no matching version found")):
        return "node_dependency_resolution"
    if "cannot find module" in lower:
        return "missing_module"
    if any(keyword in lower for keyword in ("unhealthy", "healthcheck")):
        return "container_healthcheck"
    if any(keyword in lower for keyword in ("econnrefused", "connection refused")):
        return "connection_refused"
    if any(keyword in lower for keyword in ("404", "not found")) and any(
        keyword in lower for keyword in ("/health", "/api/health", "readiness")
    ):
        return "api_endpoint_mismatch"
    if any(keyword in lower for keyword in ("expected url", "expect_url", "assertionerror")):
        return "assertion_failed"
    if any(keyword in lower for keyword in ("syntaxerror", "syntax error")):
        return "syntax_error"
    if any(keyword in lower for keyword in ("typescript", "tsc", "type error")):
        return "typescript_compile"
    return "generic_failure"


def _normalize_signature_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:80]


def _format_ci_failure_signature_summary(stage: str, module: str, family: str) -> str:
    if stage == "browser_smoke" and module == "frontend" and family == "empty_terminal_dom":
        return (
            "Backend completed enough to serve preview, but the browser never rendered the terminal success DOM. "
            "Suspect frontend state/polling/render lifecycle before Docker/backend/PDF pipeline."
        )
    return f"stage={stage}; module={module}; family={family}"


def _classify_unknown_smoke_action(text: str) -> dict[str, Any] | None:
    action_match = re.search(r"Unknown smoke action:\s*([A-Za-z0-9_-]+)", text)
    if not action_match:
        return None

    action = action_match.group(1)
    failing_step = _extract_ci_field(text, "failing_step") or "unknown"
    configured_action = _extract_ci_field(text, "action") or action

    return {
        "severity": "critical",
        "code": UNKNOWN_SMOKE_ACTION_ISSUE_CODE,
        "description": (
            "Browser smoke failed because `mvp.config.json` uses a smoke action that the CI runner does not support. "
            "Fix the smoke-test contract instead of weakening or skipping the browser smoke flow."
        ),
        "evidence": [
            f"Failed smoke step: {failing_step}.",
            f"Configured action: `{configured_action}`.",
            f"CI runner reported unknown smoke action: `{action}`.",
        ],
        "related_checks": [
            "Inspect root mvp.config.json v2 flows and data-testid targets.",
            "Use supported v2 smoke actions such as goto, click, fill, select, upload_file, expect_download, wait_for_response, expect_styled, and wait_for_outcome.",
            "Use expect_request on backend-triggering clicks and wait_for_outcome for final success/failure rather than fragile intermediate text waits.",
        ],
        "acceptance_checks": [
            "Run the Docker browser smoke flow and confirm the failing v2 flow step executes without Unknown smoke action errors.",
            "Confirm the next GitHub Actions docker-runtime job passes Run browser smoke flow.",
        ],
        "failing_step": failing_step,
        "action": action,
    }


def _extract_ci_field(text: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _classify_frontend_empty_terminal_dom(text: str) -> dict[str, Any] | None:
    diagnostics = _parse_browser_smoke_diagnostics(text)
    if not diagnostics:
        return None
    if not _diagnostics_show_backend_preview_ready(diagnostics):
        return None
    if not _diagnostics_network_is_clean(diagnostics):
        return None
    if not _diagnostics_show_missing_terminal_dom(diagnostics):
        return None

    payload = _diagnostic_payload(diagnostics)
    success_target = _nested_dict_value(payload, "success", "target") or _success_target_from_error(
        diagnostics.get("error", "")
    )
    failure_target = _nested_dict_value(payload, "failure", "target")
    failing_step = diagnostics.get("failing_step") or "unknown"
    action = diagnostics.get("action") or "unknown"

    evidence = [
        f"Failed browser step: {failing_step} ({action}).",
        "Create request succeeded: POST /api/jobs returned a successful 2xx status.",
        "Preview request succeeded: a GET request ending in /preview returned 200.",
        "Browser diagnostics show no console errors, failed requests, or 4xx/5xx responses.",
    ]
    if success_target:
        evidence.append(f"Terminal success target `{success_target}` was not visible before timeout.")
    if failure_target:
        evidence.append(f"Failure target `{failure_target}` was also not reported as visible.")
    if _html_has_empty_app_container(diagnostics.get("html_tail", "")):
        evidence.append("HTML tail contains an empty React app/root container.")
    else:
        evidence.append(
            "HTML/body tail does not expose the expected terminal outcome despite the completed backend preview."
        )

    return {
        "severity": "critical",
        "code": FRONTEND_EMPTY_TERMINAL_DOM_ISSUE_CODE,
        "description": (
            "Browser smoke reached the backend create/preview path, but the frontend did not render the final success "
            "state. Start with frontend terminal-state rendering and polling lifecycle; do not start by rewriting "
            "Docker, backend generation, or the PDF/preview pipeline unless this evidence is disproven."
        ),
        "evidence": evidence,
        "related_checks": [
            "Inspect frontend/src/App.tsx or the equivalent primary workflow component that switches into the done/success state.",
            "Inspect frontend/src/hooks/useJobPolling.ts or equivalent polling/state hook for clearing status/data when polling is disabled after status=done.",
            "Verify the done/success render path preserves the final job payload needed by the configured success target, such as download_pdf.",
            "Verify mvp.config.json success/failure targets still point at real data-testid elements rendered by the final UI state.",
        ],
        "acceptance_checks": [
            "Run the Docker browser smoke flow and confirm wait_for_outcome reaches the success target.",
            "After POST /api/jobs and GET /preview succeed, verify the final UI shows the preview/download success controls without requiring another poll.",
            "Confirm the next GitHub Actions docker-runtime job passes Run browser smoke flow.",
        ],
        "failing_step": failing_step,
        "action": action,
        "success_target": success_target,
        "failure_target": failure_target,
    }


def _parse_browser_smoke_diagnostics(text: str) -> dict[str, Any]:
    marker_match = list(re.finditer(r"browser smoke failure diagnostics", text, re.IGNORECASE))
    if not marker_match:
        return {}

    segment = _trim_browser_diagnostic_segment(text[marker_match[-1].start() :])
    diagnostics: dict[str, Any] = {}
    for field in ("failing_step", "action", "payload", "error", "current_url", "page_title"):
        value = _extract_ci_field(segment, field)
        if value:
            diagnostics[field] = value

    sections = _extract_browser_diagnostic_sections(segment)
    diagnostics.update(sections)
    diagnostics["console_error_entries"] = _parse_diagnostic_json_entries(sections.get("console_errors", ""))
    diagnostics["failed_request_entries"] = _parse_diagnostic_json_entries(sections.get("failed_requests", ""))
    diagnostics["bad_response_entries"] = _parse_diagnostic_json_entries(sections.get("responses_4xx_5xx", ""))
    diagnostics["recent_network_entries"] = _parse_diagnostic_json_entries(sections.get("recent_network", ""))
    return diagnostics


def _trim_browser_diagnostic_segment(segment: str) -> str:
    cut_markers = (
        "\nTraceback (most recent call last):",
        "\n##[error]",
        "\n##[endgroup]",
    )
    cuts = [index for marker in cut_markers if (index := segment.find(marker)) > 0]
    return segment[: min(cuts)] if cuts else segment


def _extract_browser_diagnostic_sections(segment: str) -> dict[str, str]:
    section_pattern = "|".join(re.escape(name) for name in SMOKE_DIAGNOSTIC_SECTIONS)
    matches = list(re.finditer(rf"^({section_pattern}):\s*$", segment, re.MULTILINE))
    sections = {}
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(segment)
        sections[match.group(1)] = segment[match.end() : next_start].strip()
    return sections


def _parse_diagnostic_json_entries(section_text: str) -> list[dict[str, Any]]:
    entries = []
    for raw_line in section_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if not line or line == "<empty>":
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            entries.append({"raw": line})
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
        else:
            entries.append({"value": parsed})
    return entries


def _diagnostic_payload(diagnostics: dict[str, Any]) -> dict[str, Any]:
    payload = diagnostics.get("payload")
    if not isinstance(payload, str) or not payload.strip():
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _nested_dict_value(data: dict[str, Any], *keys: str) -> str:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return current if isinstance(current, str) else ""


def _success_target_from_error(error: str) -> str:
    match = re.search(r"success target:\s*([A-Za-z0-9_.-]+)", error)
    return match.group(1) if match else ""


def _diagnostics_show_backend_preview_ready(diagnostics: dict[str, Any]) -> bool:
    network = diagnostics.get("recent_network_entries") or []
    return _has_network_response(
        network, method="POST", path="/api/jobs", statuses={200, 201, 202}
    ) and _has_network_response(
        network,
        method="GET",
        path_suffix="/preview",
        statuses={200},
    )


def _has_network_response(
    entries: list[dict[str, Any]],
    *,
    method: str,
    statuses: set[int],
    path: str | None = None,
    path_suffix: str | None = None,
) -> bool:
    for entry in entries:
        if str(entry.get("event") or "").lower() != "response":
            continue
        if str(entry.get("method") or "").upper() != method:
            continue
        try:
            status = int(entry.get("status"))
        except (TypeError, ValueError):
            continue
        if status not in statuses:
            continue
        url_path = urlparse(str(entry.get("url") or "")).path.rstrip("/")
        if path and url_path != path.rstrip("/"):
            continue
        if path_suffix and not url_path.endswith(path_suffix.rstrip("/")):
            continue
        return True
    return False


def _diagnostics_network_is_clean(diagnostics: dict[str, Any]) -> bool:
    return not (
        diagnostics.get("console_error_entries")
        or diagnostics.get("failed_request_entries")
        or diagnostics.get("bad_response_entries")
    )


def _diagnostics_show_missing_terminal_dom(diagnostics: dict[str, Any]) -> bool:
    action = str(diagnostics.get("action") or "")
    error = str(diagnostics.get("error") or "")
    if action == "wait_for_outcome" or "Timed out waiting for outcome success target" in error:
        return True

    body_text = str(diagnostics.get("body_text_tail") or "").strip()
    html_tail = str(diagnostics.get("html_tail") or "")
    return body_text in {"", "<empty>"} or _html_has_empty_app_container(html_tail)


def _html_has_empty_app_container(html: str) -> bool:
    if not html:
        return False
    return bool(re.search(r'<div\s+[^>]*id=["\'](?:root|app|__next)["\'][^>]*>\s*</div>', html, re.IGNORECASE))


def _classify_generic_ci_failure(text: str) -> dict[str, Any] | None:
    clean_lines = [_clean_ci_line(line) for line in text.splitlines()]
    clean_lines = [line for line in clean_lines if line]
    if not clean_lines:
        return None

    failed_path = _extract_failed_ci_path(clean_lines)
    error_excerpt = _extract_error_excerpt(clean_lines)
    evidence = []
    if failed_path:
        evidence.append(f"Failed CI path: {failed_path}.")
    evidence.extend(error_excerpt)
    evidence = _dedupe_preserve_order(evidence)[:12]
    if not evidence:
        evidence = [_truncate(clean_lines[-1], 500)]

    related_checks = _infer_ci_related_checks(text)
    acceptance_checks = _infer_ci_acceptance_checks(text)

    return {
        "severity": "critical",
        "code": GENERIC_CI_FAILURE_ISSUE_CODE,
        "description": (
            "CI failed with an unclassified error. Use the evidence and raw CI context to locate the root cause "
            "in the application/runtime files, then fix the implementation instead of weakening CI or smoke checks."
        ),
        "evidence": evidence,
        "related_checks": related_checks,
        "acceptance_checks": acceptance_checks,
    }


def _extract_failed_ci_path(lines: list[str]) -> str:
    failed = []
    for line in lines:
        match = re.match(r"-\s*(.+?):\s*(failure|failed|cancelled|timed_out)$", line, re.IGNORECASE)
        if match:
            failed.append(match.group(1).strip())
    if failed:
        return " -> ".join(failed[:4])

    step_match = next(
        (
            re.search(r"step\s+\d+:\s*(.+?)\s*->\s*(failure|failed)", line, re.IGNORECASE)
            for line in lines
            if re.search(r"step\s+\d+:", line, re.IGNORECASE)
        ),
        None,
    )
    if step_match:
        return step_match.group(1).strip()
    return ""


def _extract_error_excerpt(lines: list[str]) -> list[str]:
    patterns = (
        r"##\[error\]",
        r"\bERROR\b",
        r"\bError:",
        r"\bfailed\b",
        r"\bfailure\b",
        r"\bexited\s*\(\d+\)",
        r"\bTraceback\b",
        r"\bAssertionError\b",
        r"\bTimeoutError\b",
        r"\bECONNREFUSED\b",
        r"\bmanifest unknown\b",
        r"\bdependency failed to start\b",
    )
    combined = re.compile("|".join(patterns), re.IGNORECASE)
    excerpt = []
    for line in lines:
        if not combined.search(line):
            continue
        if _is_low_signal_ci_line(line):
            continue
        excerpt.append(_truncate(line, 700))
        if len(excerpt) >= 10:
            break
    return excerpt


def _infer_ci_related_checks(text: str) -> list[str]:
    lower = text.lower()
    checks = [
        "Inspect the failed GitHub Actions job and step to identify the exact command, but fix the product/runtime root cause rather than weakening CI.",
        "Search the exact error text in the workspace and inspect the files referenced by the failed command or stack trace.",
    ]
    if any(keyword in lower for keyword in ("docker", "compose", "container", "health", "dependency failed to start")):
        checks.extend(
            [
                "Inspect docker-compose.yml service dependencies, healthchecks, published ports, env_file usage, and build contexts.",
                "Inspect service Dockerfiles, package scripts, and startup commands for the container that exited or became unhealthy.",
                "Inspect .env.example and mvp.config.json v2 readiness URLs so CI runtime ports and seed command match Docker reality.",
            ]
        )
    if any(keyword in lower for keyword in ("prisma", "migration", "schema.prisma", "seed")):
        checks.extend(
            [
                "Inspect backend/prisma/schema.prisma, backend/prisma/migrations/*/migration.sql, and backend/prisma/seed.js.",
                "Verify schema initialization is production-safe on a clean database: prisma migrate deploy with valid migrations or prisma db push before seed.",
            ]
        )
    if any(
        keyword in lower for keyword in ("browser smoke", "playwright", "smoke_flow", "current_url", "localstorage")
    ):
        checks.extend(
            [
                "Inspect mvp.config.json v2 flows, targets, expect_request, and wait_for_outcome contract.",
                "Inspect frontend routes/components plus backend API/auth flow involved in the failing browser step.",
            ]
        )
    if any(
        keyword in lower
        for keyword in ("npm", "tsc", "typescript", "module not found", "cannot find module", "build failed")
    ):
        checks.extend(
            [
                "Inspect package.json scripts/dependencies, lockfile, tsconfig, and imports referenced by the build/test error.",
            ]
        )
    if any(keyword in lower for keyword in ("pytest", "traceback", "assertionerror")):
        checks.append("Inspect the failing test traceback and the source module it exercises.")
    return _dedupe_preserve_order(checks)


def _infer_ci_acceptance_checks(text: str) -> list[str]:
    lower = text.lower()
    checks = ["Rerun the same local command or runtime path represented by the failed CI step before pushing again."]
    if any(keyword in lower for keyword in ("docker", "compose", "container", "health", "dependency failed to start")):
        checks.append(
            "Run Docker runtime verification from a clean state: docker compose config, docker compose build, docker compose up -d, then inspect service health/logs."
        )
    if any(keyword in lower for keyword in ("seed", "mvp_seed_command")):
        checks.append("Run the configured seed command successfully against the Docker runtime.")
    if any(keyword in lower for keyword in ("browser smoke", "playwright", "smoke_flow")):
        checks.append("Run the configured browser smoke flow and verify the failing step now passes.")
    checks.append("Confirm the next GitHub Actions run passes the previously failing job/step.")
    return _dedupe_preserve_order(checks)


def _clean_ci_line(line: str) -> str:
    line = GITHUB_LOG_TS_RE.sub("", line)
    line = ANSI_ESCAPE_RE.sub("", line)
    return line.strip()


def _is_low_signal_ci_line(line: str) -> bool:
    lowered = line.lower()
    return (
        "process completed with exit code" in lowered
        or "post job cleanup" in lowered
        or "##[endgroup]" in lowered
        or "##[group]" in lowered
        or lowered.startswith("shell:")
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _classify_auth_redirect_after_login(text: str) -> dict[str, Any] | None:
    redirect_target = _extract_login_redirect_target(text)
    if not redirect_target:
        return None

    login_success = _has_successful_login_response(text)
    storage_token = _has_auth_storage_evidence(text)
    cookie_token = _has_auth_cookie_evidence(text)
    protected_redirect = _has_protected_route_redirect(text, redirect_target)
    final_login_url = _has_final_login_redirect_url(text)

    if not final_login_url or not (login_success or storage_token or cookie_token):
        return None

    evidence = [
        f"Final browser URL is /login?redirect={redirect_target} instead of the protected route.",
    ]
    if login_success:
        evidence.append("Login API returned a successful 2xx response for POST /api/auth/login.")
    if storage_token:
        evidence.append("Browser storage contains auth token/user keys after login.")
    if cookie_token:
        evidence.append("Browser cookies contain an auth token cookie name after login.")
    if protected_redirect:
        evidence.append(f"Navigation/RSC request for {redirect_target} returned a redirect instead of rendering.")

    return {
        "severity": "critical",
        "code": AUTH_REDIRECT_ISSUE_CODE,
        "description": (
            "Browser smoke login succeeds, but the protected route redirects back to "
            f"/login?redirect={redirect_target}. Do not weaken the smoke expectation; fix the auth/session flow."
        ),
        "evidence": evidence,
        "related_checks": [
            "Inspect the frontend auth write path, especially auth.ts/setAuth and the login page response shape.",
            "Inspect middleware.ts protected-route guard and verify it reads the same cookie/key that login writes.",
            "Inspect server layout/page guards for protected routes and any redirects executed during RSC navigation.",
            "Verify role-based access for the demo user allows the target protected route.",
            "Verify the auth cookie is visible to Next App Router/RSC navigation to the protected route.",
        ],
        "acceptance_checks": [
            (
                "Run the Docker browser smoke flow from mvp.config.json, or an equivalent Playwright flow: "
                f"login as the demo user, navigate to {redirect_target}, and assert the URL stays on {redirect_target}."
            )
        ],
        "target_path": redirect_target,
    }


def _extract_login_redirect_target(text: str) -> str | None:
    for url in _candidate_urls(text):
        parsed = urlparse(url)
        if parsed.path.rstrip("/") != "/login":
            continue
        redirect_values = parse_qs(parsed.query).get("redirect")
        if redirect_values:
            target = unquote(redirect_values[0])
            return target if target.startswith("/") else f"/{target}"

    match = re.search(r"login\?redirect=([^\s\"'<>]+)", text)
    if not match:
        return None
    target = unquote(match.group(1).split("&", 1)[0])
    return target if target.startswith("/") else f"/{target}"


def _candidate_urls(text: str) -> list[str]:
    urls = []
    for match in re.finditer(r"https?://[^\s\"'<>]+", text):
        urls.append(match.group(0).rstrip(").,"))
    return urls


def _has_final_login_redirect_url(text: str) -> bool:
    return bool(re.search(r"(current_url:\s*|navigated to\s+\")https?://[^\s\"']+/login\?redirect=", text))


def _has_successful_login_response(text: str) -> bool:
    return bool(
        re.search(r"/api/auth/login.{0,240}\"status\":\s*20\d", text, re.DOTALL)
        or re.search(r"\"status\":\s*20\d.{0,240}/api/auth/login", text, re.DOTALL)
        or re.search(r"POST\s+[^\n]+/api/auth/login[^\n]+(?:20\d|success)", text, re.IGNORECASE)
    )


def _has_auth_storage_evidence(text: str) -> bool:
    storage_match = re.search(
        r"localStorage:\n(?P<body>.*?)(?:\nsessionStorage:|\ncookies:|\nconsole_errors:|\Z)", text, re.DOTALL
    )
    if not storage_match:
        return False
    body = storage_match.group("body")
    return bool(re.search(r"(token|accessToken|auth|user)", body, re.IGNORECASE))


def _has_auth_cookie_evidence(text: str) -> bool:
    cookie_match = re.search(
        r"cookies:\n(?P<body>.*?)(?:\nconsole_errors:|\nfailed_requests:|\nresponses_4xx_5xx:|\Z)", text, re.DOTALL
    )
    if not cookie_match:
        return False
    body = cookie_match.group("body")
    return bool(re.search(r'"name":\s*"[^"]*(token|auth|session)[^"]*"', body, re.IGNORECASE))


def _has_protected_route_redirect(text: str, target_path: str) -> bool:
    for line in text.splitlines():
        if target_path in line and re.search(r'"status":\s*30[1278]', line):
            return True
    return False


def _advance_ci_timeout_phase(
    *,
    repo: str,
    sha: str,
    phase: str,
    primary_timeout: int,
    grace_timeout: int,
    last_runs: list[dict[str, Any]],
    last_polling_error: Exception | None,
) -> CIResult | None:
    if phase == "primary" and grace_timeout > 0:
        logger.warning(
            "test_service.ci_primary_timeout_recheck",
            repo=repo,
            sha=sha,
            primary_timeout=primary_timeout,
            grace_timeout=grace_timeout,
            observed_runs=_format_observed_runs(last_runs),
        )
        return None

    logger.error(
        "test_service.ci_timeout",
        repo=repo,
        sha=sha,
        phase=phase,
        observed_runs=_format_observed_runs(last_runs),
    )
    details = (
        f"CI polling timeout: no completed workflow result within "
        f"{primary_timeout + max(grace_timeout, 0)} seconds for commit {sha}."
    )
    observed_runs = _format_observed_runs(last_runs)
    if observed_runs:
        details += f"\nLast observed workflow runs: {observed_runs}"
    if last_polling_error is not None:
        details += f"\nLast GitHub API error: {_format_exception(last_polling_error)}"
    return CIResult(passed=False, failed_logs=[details], timed_out=True)


def _format_observed_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "none"

    parts = []
    for run in runs[:5]:
        name = run.get("name") or run.get("display_title") or f"run {run.get('id', 'unknown')}"
        status = run.get("status") or "unknown"
        conclusion = run.get("conclusion") or "pending"
        parts.append(f"{name}: {status}/{conclusion}")
    if len(runs) > 5:
        parts.append(f"... and {len(runs) - 5} more")
    return "; ".join(parts)


async def _build_workflow_run_failure_log(
    client: httpx.AsyncClient,
    run: dict[str, Any],
    headers: dict[str, str],
) -> str:
    summary = _format_workflow_run_summary(run)
    jobs_url = run.get("jobs_url")
    if not jobs_url:
        return summary

    try:
        response = await client.get(jobs_url, headers=headers)
        response.raise_for_status()
        jobs = response.json().get("jobs", [])
    except Exception as exc:
        logger.warning(
            "test_service.ci_jobs_load_failed",
            run_id=run.get("id"),
            error=str(exc),
        )
        return summary

    job_details = _format_failed_jobs(jobs)
    job_logs = await _failed_job_log_snippets(client, jobs, headers, jobs_url)
    details = "\n\n".join(part for part in (job_details, job_logs) if part)
    if not details:
        return summary
    return f"{summary}\n{details}"


def _format_workflow_run_summary(run: dict[str, Any]) -> str:
    name = run.get("name") or run.get("display_title") or f"workflow run {run.get('id', 'unknown')}"
    conclusion = run.get("conclusion") or "unknown"
    html_url = run.get("html_url")
    if html_url:
        return f"{name}: {conclusion} ({html_url})"
    return f"{name}: {conclusion}"


def _format_failed_jobs(jobs: list[dict[str, Any]]) -> str:
    lines = []
    for job in jobs:
        conclusion = job.get("conclusion")
        status = job.get("status")
        if status == "completed" and conclusion in {"success", "skipped"}:
            continue

        job_name = job.get("name") or f"job {job.get('id', 'unknown')}"
        job_state = conclusion or status or "unknown"
        lines.append(f"- {job_name}: {job_state}")

        for step in job.get("steps", []):
            step_conclusion = step.get("conclusion")
            step_status = step.get("status")
            if step_status == "completed" and step_conclusion in {"success", "skipped"}:
                continue

            step_name = step.get("name") or f"step {step.get('number', '?')}"
            step_state = step_conclusion or step_status or "unknown"
            lines.append(f"  - {step_name}: {step_state}")

    return "\n".join(lines)


async def _failed_job_log_snippets(
    client: httpx.AsyncClient,
    jobs: list[dict[str, Any]],
    headers: dict[str, str],
    jobs_url: str | None = None,
) -> str:
    snippets = []
    for job in jobs:
        if not _job_failed(job):
            continue

        logs_url = _job_logs_url(job, jobs_url)
        if not logs_url:
            continue

        job_name = job.get("name") or f"job {job.get('id', 'unknown')}"
        try:
            response = await client.get(logs_url, headers=headers, follow_redirects=True)
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "test_service.ci_job_log_load_failed",
                job_id=job.get("id"),
                error=_format_exception(exc),
            )
            continue

        text = getattr(response, "text", "") or ""
        if not text.strip():
            continue

        failed_step_names = _failed_step_names(job)
        failed_step_log = _extract_failed_step_log(text)
        if failed_step_log:
            step_label = " / ".join(failed_step_names[:3]) if failed_step_names else "failed step"
            snippets.append(f"--- failed step log: {job_name} -> {step_label} ---\n{failed_step_log}")
        else:
            snippets.append(
                f"--- logs: {job_name} ---\n{_tail_text(_clean_github_log_text(text), MAX_CI_JOB_LOG_CHARS)}"
            )

    return "\n\n".join(snippets)


def _job_logs_url(job: dict[str, Any], jobs_url: str | None = None) -> str | None:
    if job.get("logs_url"):
        return job["logs_url"]
    if job.get("url"):
        return f"{job['url'].rstrip('/')}/logs"
    job_id = job.get("id")
    if job_id and jobs_url:
        match = re.match(r"(.+/actions)/runs/\d+/jobs", jobs_url.rstrip("/"))
        if match:
            return f"{match.group(1)}/jobs/{job_id}/logs"
    return None


def _job_failed(job: dict[str, Any]) -> bool:
    status = job.get("status")
    conclusion = job.get("conclusion")
    return not (status == "completed" and conclusion in {"success", "skipped"})


def _failed_step_names(job: dict[str, Any]) -> list[str]:
    names = []
    for step in job.get("steps", []):
        status = step.get("status")
        conclusion = step.get("conclusion")
        if status == "completed" and conclusion in {"success", "skipped"}:
            continue
        name = step.get("name")
        if name:
            names.append(str(name))
    return names


def _extract_failed_step_log(text: str) -> str:
    clean_text = _clean_github_log_text(text)
    if not clean_text:
        return ""

    marker = _failure_marker_position(clean_text)
    if marker is None:
        return ""

    start, end = _step_group_bounds(clean_text, marker)
    block = clean_text[start:end].strip()
    if not block:
        return ""
    return _truncate_failed_step_log(block)


def _failure_marker_position(text: str) -> int | None:
    last_marker_patterns = (
        r"browser smoke failure diagnostics",
        r"SmokeStepError:",
        r"Traceback \(most recent call last\)",
        r"Unknown smoke action:",
        r"\bAssertionError\b",
        r"\bError:",
        r"\bERROR\b",
    )
    for pattern in last_marker_patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if matches:
            return matches[-1].start()

    match = re.search(r"##\[error\]", text, re.IGNORECASE)
    return match.start() if match else None


def _step_group_bounds(text: str, marker: int) -> tuple[int, int]:
    groups = list(GITHUB_STEP_GROUP_RE.finditer(text))
    if not groups:
        return 0, len(text)

    start = 0
    end = len(text)
    for index, group in enumerate(groups):
        if group.start() <= marker:
            start = group.start()
            next_index = index + 1
            if next_index < len(groups):
                end = groups[next_index].start()
            continue
        break
    return start, end


def _clean_github_log_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = GITHUB_LOG_TS_RE.sub("", line)
        line = ANSI_ESCAPE_RE.sub("", line)
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _truncate_failed_step_log(text: str) -> str:
    if len(text) <= MAX_CI_FAILED_STEP_LOG_CHARS:
        return text

    head_chars = min(8000, MAX_CI_FAILED_STEP_LOG_CHARS // 4)
    tail_chars = MAX_CI_FAILED_STEP_LOG_CHARS - head_chars
    omitted = len(text) - head_chars - tail_chars
    return (
        f"{text[:head_chars].rstrip()}\n"
        f"... [truncated {omitted} chars from failed step log] ...\n"
        f"{text[-tail_chars:].lstrip()}"
    )


def _tail_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    return text[-limit:].strip()


def _format_exception(exc: BaseException) -> str:
    text = str(exc)
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


def _run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _command_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout or ''}\n{result.stderr or ''}".strip()


def _format_command_result(cwd: str | Path, result: subprocess.CompletedProcess[str]) -> str:
    command = " ".join(result.args) if isinstance(result.args, list) else str(result.args)
    output = _command_output(result)
    return f"$ {command}\n# cwd: {cwd}\n# exit: {result.returncode}\n{output}".strip()


def _display_cwd(result: subprocess.CompletedProcess[str], fallback: Path) -> str | Path:
    return getattr(result, "display_cwd", fallback)


def _shell_join(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def _find_package_dirs(workspace: Path) -> list[Path]:
    package_dirs = []
    for package_json in workspace.rglob("package.json"):
        relative_parts = package_json.relative_to(workspace).parts
        if any(part in {"node_modules", "dist", "build", ".git"} for part in relative_parts):
            continue
        if len(relative_parts) > 3:
            continue
        package_dirs.append(package_json.parent)

    return sorted(package_dirs, key=lambda path: (len(path.relative_to(workspace).parts), str(path)))


def _read_package_json(package_dir: Path) -> dict[str, Any]:
    try:
        return json.loads((package_dir / "package.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _needs_node_install(package_dir: Path, package_json: dict[str, Any]) -> bool:
    if (package_dir / "node_modules").exists():
        return False
    dependency_keys = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
    return any(package_json.get(key) for key in dependency_keys)


def _node_install_command(package_dir: Path) -> list[str]:
    if (package_dir / "package-lock.json").exists():
        return ["npm", "ci"]
    return ["npm", "install"]


def _node_check_commands(workspace: Path, package_dirs: list[Path]) -> list[tuple[Path, list[str]]]:
    commands = []
    root_package = workspace / "package.json"
    root_has_test = False

    if root_package.exists():
        root_scripts = _read_package_json(workspace).get("scripts", {})
        root_has_test = "test" in root_scripts
        if root_has_test:
            commands.append((workspace, ["npm", "test"]))
        elif "build" in root_scripts:
            commands.append((workspace, ["npm", "run", "build"]))

    for package_dir in package_dirs:
        if package_dir == workspace:
            continue

        scripts = _read_package_json(package_dir).get("scripts", {})
        if "test" in scripts and not root_has_test:
            commands.append((package_dir, ["npm", "test"]))
        elif "build" in scripts:
            commands.append((package_dir, ["npm", "run", "build"]))

    return commands
