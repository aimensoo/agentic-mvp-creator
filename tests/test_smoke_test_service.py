import subprocess
from unittest.mock import patch

from services.smoke_test_service import (
    SmokeTestResult,
    SmokeTestService,
    _BrowserDiagnostics,
    _SmokeStepError,
    _format_browser_failure,
    _matches_expected_response,
)


def _write_config(path, extra: str = ""):
    seed_line = '"seed_command": "docker compose exec backend node dist/seed.js",' if extra == "seed" else ""
    (path / "mvp.config.json").write_text(
        f"""
        {{
          "version": 2,
          "runtime": {{
            "type": "docker_compose",
            "compose_file": "docker-compose.yml",
            {seed_line}
            "start_command": "docker compose up -d"
          }},
          "readiness": [
            {{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}}
          ],
          "targets": {{
            "save": {{"selector": "[data-testid='save']"}},
            "done": {{"selector": "[data-testid='done']"}},
            "error": {{"selector": "[data-testid='error']"}}
          }},
          "flows": [
            {{
              "id": "primary_happy_path",
              "steps": [
                {{"action": "goto", "path": "/dashboard", "expect_text": "Dashboard"}}
              ]
            }}
          ]
        }}
        """
    )


class _FakeBodyLocator:
    def inner_text(self):
        return "Login page\nEmail\nPassword\nSign in"


class _FakeContext:
    def cookies(self):
        return [
            {
                "name": "session",
                "value": "cookie-value",
                "domain": "localhost",
                "path": "/",
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ]


class _FakePage:
    url = "http://localhost:5174/login?redirect=%2Fclients"
    context = _FakeContext()

    def title(self):
        return "Login"

    def locator(self, selector):
        assert selector == "body"
        return _FakeBodyLocator()

    def evaluate(self, _script):
        return {
            "localStorage": {
                "authToken": "secret-token",
                "theme": "dark",
            },
            "sessionStorage": {
                "redirect": "/clients",
            },
        }

    def content(self):
        return "<html><body>Login page</body></html>"


def test_smoke_test_fails_when_config_missing(tmp_path):
    result = SmokeTestService().run(tmp_path)

    assert isinstance(result, SmokeTestResult)
    assert result.passed is False
    assert "mvp.config.json is missing" in result.output


def test_smoke_test_rejects_unsafe_start_command(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {
            "type": "docker_compose",
            "compose_file": "docker-compose.yml",
            "start_command": "rm -rf ."
          },
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {"done": {"selector": "[data-testid='done']"}},
          "flows": [{"id": "primary_happy_path", "steps": [{"action": "goto", "path": "/"}]}]
        }
        """
    )

    result = SmokeTestService().run(tmp_path)

    assert result.passed is False
    assert "not allowed" in result.output


def test_smoke_test_reports_start_command_timeout(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {
            "type": "docker_compose",
            "compose_file": "docker-compose.yml",
            "start_command": "docker compose up"
          },
          "readiness": [{"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}],
          "targets": {"done": {"selector": "[data-testid='done']"}},
          "flows": [{"id": "primary_happy_path", "steps": [{"action": "goto", "path": "/"}]}]
        }
        """
    )

    with patch("services.smoke_test_service.subprocess.run", side_effect=subprocess.TimeoutExpired(["docker"], 1)):
        result = SmokeTestService(command_timeout=1).run(tmp_path)

    assert result.passed is False
    assert "timed out" in result.output
    assert "docker compose up -d" in result.output


def test_smoke_test_runs_start_seed_and_browser_flow(tmp_path):
    _write_config(tmp_path, extra="seed")
    start = subprocess.CompletedProcess(
        args=["docker", "compose", "up", "-d"],
        returncode=0,
        stdout="started",
        stderr="",
    )
    seed = subprocess.CompletedProcess(
        args=["docker", "compose", "exec", "backend", "node", "dist/seed.js"],
        returncode=0,
        stdout="seeded",
        stderr="",
    )
    service = SmokeTestService(command_timeout=3)

    with (
        patch("services.smoke_test_service.subprocess.run", side_effect=[start, seed]) as mock_run,
        patch(
            "services.smoke_test_service._wait_for_readiness", return_value=SmokeTestResult(passed=True, output="ready")
        ),
        patch.object(service, "_run_browser_flow", return_value="browser_flow passed"),
    ):
        result = service.run(tmp_path)

    assert result.passed is True
    assert "started" in result.output
    assert "seeded" in result.output
    assert "ready" in result.output
    assert "browser_flow passed" in result.output
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0] == ["docker", "compose", "up", "-d"]


def test_smoke_test_runs_v2_runtime_readiness_and_flow(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        """
        {
          "version": 2,
          "runtime": {
            "type": "docker_compose",
            "compose_file": "docker-compose.yml",
            "seed_command": "docker compose exec backend node dist/seed.js"
          },
          "readiness": [
            {"name": "frontend", "url": "http://localhost:5174", "expect_status": 200}
          ],
          "targets": {
            "save": {"selector": "[data-testid='save']"},
            "done": {"selector": "[data-testid='done']"},
            "error": {"selector": "[data-testid='error']"}
          },
          "flows": [
            {
              "id": "primary_happy_path",
              "steps": [
                {
                  "action": "click",
                  "target": "save",
                  "expect_request": {"method": "POST", "url": "/api/tasks", "status": [201]}
                },
                {
                  "action": "wait_for_outcome",
                  "success": {"target": "done"},
                  "failure": {"target": "error"}
                }
              ]
            }
          ]
        }
        """
    )
    start = subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="started", stderr="")
    seed = subprocess.CompletedProcess(args=["docker"], returncode=0, stdout="seeded", stderr="")
    service = SmokeTestService(command_timeout=3)

    with (
        patch("services.smoke_test_service.subprocess.run", side_effect=[start, seed]) as mock_run,
        patch(
            "services.smoke_test_service._wait_for_readiness", return_value=SmokeTestResult(passed=True, output="ready")
        ),
        patch.object(service, "_run_browser_flow", return_value="browser_flow passed"),
    ):
        result = service.run(tmp_path)

    assert result.passed is True
    assert "ready" in result.output
    assert mock_run.call_args_list[0].args[0] == ["docker", "compose", "-f", "docker-compose.yml", "up", "-d"]
    assert mock_run.call_count == 2


class _FakeRequest:
    method = "POST"


class _FakeResponse:
    request = _FakeRequest()
    status = 201
    url = "http://localhost:5174/api/tasks"


def test_smoke_expect_request_matches_method_url_and_status():
    assert _matches_expected_response(
        _FakeResponse(),
        "http://localhost:5174",
        {"method": "POST", "url": "/api/tasks", "status": [200, 201, 202]},
    )
    assert not _matches_expected_response(
        _FakeResponse(),
        "http://localhost:5174",
        {"method": "GET", "url": "/api/tasks", "status": [201]},
    )


def test_smoke_test_rejects_legacy_mvp_config(tmp_path):
    (tmp_path / "mvp.config.json").write_text(
        '{"app_url":"http://localhost:5174","start_command":"docker compose up -d","smoke_flow":["open_app","login"]}'
    )

    result = SmokeTestService().run(tmp_path)

    assert result.passed is False
    assert "contract v2" in result.output


def test_browser_failure_diagnostics_include_auth_state_and_redact_sensitive_values():
    diagnostics = _BrowserDiagnostics()
    diagnostics.console_errors.append({"type": "error", "text": "Login failed", "location": {"url": "app.js"}})
    diagnostics.failed_requests.append(
        {"event": "requestfailed", "method": "POST", "url": "http://localhost:5174/api/login"}
    )
    diagnostics.bad_responses.append(
        {"event": "response", "method": "POST", "url": "http://localhost:5174/api/login", "status": 401}
    )
    diagnostics.recent_network.append({"event": "request", "method": "GET", "url": "http://localhost:5174/clients"})
    exc = _SmokeStepError("smoke_flow[2]", "open_primary_entity_list", {}, AssertionError("redirected to login"))

    output = _format_browser_failure(_FakePage(), diagnostics, exc)

    assert "failing_step: smoke_flow[2]" in output
    assert "current_url: http://localhost:5174/login?redirect=%2Fclients" in output
    assert "page_title: Login" in output
    assert "body_text_tail:" in output
    assert "localStorage:" in output
    assert "- authToken: ***" in output
    assert "- theme: dark" in output
    assert "sessionStorage:" in output
    assert "cookies:" in output
    assert '"name": "session"' in output
    assert "console_errors:" in output
    assert "failed_requests:" in output
    assert "responses_4xx_5xx:" in output
    assert "recent_network:" in output
    assert "html_tail:" in output
    assert "secret-token" not in output
    assert "cookie-value" not in output
