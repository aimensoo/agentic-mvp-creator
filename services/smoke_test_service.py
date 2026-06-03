import json
import fnmatch
import re
import shlex
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel

from utils.logger import get_logger
from utils.redaction import redact_secrets

logger = get_logger(__name__)


ALLOWED_COMMAND_PREFIXES = (
    ("docker", "compose"),
    ("npm",),
    ("pnpm",),
    ("yarn",),
    ("node",),
    ("npx",),
    ("python",),
    ("python3",),
    ("make",),
)
FORBIDDEN_COMMAND_PARTS = {
    "rm",
    "sudo",
    "dd",
    "mkfs",
    "shutdown",
    "reboot",
}
MAX_DIAGNOSTIC_TEXT_CHARS = 4000
MAX_STORAGE_VALUE_CHARS = 500
SENSITIVE_STORAGE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|auth|jwt|session|cookie|credential|api[_-]?key)",
    re.IGNORECASE,
)
SENSITIVE_STORAGE_VALUE_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~+/=-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class SmokeTestResult(BaseModel):
    passed: bool
    output: str


class _SmokeStepError(RuntimeError):
    def __init__(self, step_path: str, action: str, payload: dict[str, Any], cause: BaseException):
        self.step_path = step_path
        self.action = action
        self.payload = payload
        self.cause = cause
        payload_json = _json_dumps(payload)
        super().__init__(
            f"{step_path}: {action} step failed. payload={payload_json}. error={cause.__class__.__name__}: {cause}"
        )


class _BrowserDiagnostics:
    def __init__(self):
        self.console_errors = deque(maxlen=20)
        self.failed_requests = deque(maxlen=20)
        self.bad_responses = deque(maxlen=20)
        self.recent_network = deque(maxlen=40)

    def attach(self, page) -> None:
        page.on("console", _guard_handler(self._on_console))
        page.on("request", _guard_handler(self._on_request))
        page.on("requestfailed", _guard_handler(self._on_request_failed))
        page.on("response", _guard_handler(self._on_response))

    def _on_console(self, message) -> None:
        if getattr(message, "type", "") != "error":
            return
        self.console_errors.append(
            {
                "type": getattr(message, "type", "error"),
                "text": redact_secrets(getattr(message, "text", "")),
                "location": getattr(message, "location", None),
            }
        )

    def _on_request(self, request) -> None:
        self.recent_network.append(_request_snapshot("request", request))

    def _on_request_failed(self, request) -> None:
        entry = _request_snapshot("requestfailed", request)
        failure = getattr(request, "failure", None)
        if callable(failure):
            failure = failure()
        if failure:
            entry["failure"] = redact_secrets(failure)
        self.failed_requests.append(entry)
        self.recent_network.append(entry)

    def _on_response(self, response) -> None:
        request = getattr(response, "request", None)
        entry = {
            "event": "response",
            "method": getattr(request, "method", None) if request is not None else None,
            "url": redact_secrets(getattr(response, "url", "")),
            "status": getattr(response, "status", None),
        }
        if entry["status"] is not None and entry["status"] >= 400:
            self.bad_responses.append(entry)
        self.recent_network.append(entry)


class SmokeTestService:
    def __init__(
        self,
        browser_timeout_ms: int = 30_000,
        command_timeout: int = 180,
        headless: bool = True,
    ):
        self._browser_timeout_ms = browser_timeout_ms
        self._command_timeout = command_timeout
        self._headless = headless

    def run(self, workspace_path: str | Path) -> SmokeTestResult:
        workspace = Path(workspace_path)
        config_result = _load_config(workspace)
        if isinstance(config_result, SmokeTestResult):
            return config_result
        config = config_result

        output_parts = []
        start = _run_safe_command(config["_start_command"], workspace, self._command_timeout)
        output_parts.append(_format_command_result("start_command", start))
        if start.returncode != 0:
            return SmokeTestResult(passed=False, output="\n\n".join(output_parts))

        seed_command = config.get("_seed_command")
        if seed_command:
            seed = _run_safe_command(seed_command, workspace, self._command_timeout)
            output_parts.append(_format_command_result("seed_command", seed))
            if seed.returncode != 0:
                return SmokeTestResult(passed=False, output="\n\n".join(output_parts))

        readiness = _wait_for_readiness(config)
        if readiness.output:
            output_parts.append(readiness.output)
        if not readiness.passed:
            return SmokeTestResult(passed=False, output="\n\n".join(output_parts))

        try:
            browser_output = self._run_browser_flow(config)
        except Exception as exc:
            output_parts.append(f"browser_flow failed: {exc}")
            logger.warning("smoke_test.failed", workspace=str(workspace), error=str(exc))
            return SmokeTestResult(passed=False, output="\n\n".join(output_parts))

        output_parts.append(browser_output)
        logger.info("smoke_test.passed", workspace=str(workspace))
        return SmokeTestResult(passed=True, output="\n\n".join(output_parts))

    def _run_browser_flow(self, config: dict[str, Any]) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Install backend dependency `playwright` "
                "and run `python -m playwright install chromium`."
            ) from exc

        app_url = config["_app_url"].rstrip("/")
        steps, step_prefix = _flow_steps(config)
        completed = []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self._headless)
            page = None
            diagnostics = _BrowserDiagnostics()
            try:
                page = browser.new_page()
                diagnostics.attach(page)
                page.set_default_timeout(self._browser_timeout_ms)
                for index, step in enumerate(steps):
                    action, payload = _normalize_step(step)
                    _run_step(page, app_url, config, action, payload, f"{step_prefix}[{index}]")
                    completed.append(action)
            except Exception as exc:
                raise RuntimeError(_format_browser_failure(page, diagnostics, exc)) from exc
            finally:
                browser.close()

        return "browser_flow passed: " + ", ".join(completed)


def _load_config(workspace: Path) -> dict[str, Any] | SmokeTestResult:
    config_path = workspace / "mvp.config.json"
    if not config_path.exists():
        return SmokeTestResult(
            passed=False,
            output=(
                "mvp.config.json is missing. Generated MVP must declare contract v2 with runtime, readiness, "
                "data-testid targets, flows, expect_request, and wait_for_outcome."
            ),
        )

    try:
        config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return SmokeTestResult(passed=False, output=f"mvp.config.json is invalid: {exc}")

    if config.get("version") != 2:
        return SmokeTestResult(
            passed=False,
            output=(
                "mvp.config.json must use contract v2. Legacy app_url/start_command/smoke_flow contracts are "
                "too fragile for generated MVP smoke tests."
            ),
        )

    return _load_v2_config(config, workspace)


def _load_v2_config(config: dict[str, Any], workspace: Path) -> dict[str, Any] | SmokeTestResult:
    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        return SmokeTestResult(passed=False, output="mvp.config.json v2 field `runtime` is required")
    if runtime.get("type") != "docker_compose":
        return SmokeTestResult(passed=False, output="mvp.config.json v2 runtime.type must be docker_compose")
    compose_file = runtime.get("compose_file")
    if not isinstance(compose_file, str) or not compose_file.strip():
        return SmokeTestResult(passed=False, output="mvp.config.json v2 runtime.compose_file is required")

    readiness = config.get("readiness")
    if not isinstance(readiness, list) or not readiness:
        return SmokeTestResult(passed=False, output="mvp.config.json v2 field `readiness` must be a non-empty list")
    app_url = _v2_app_url(readiness)
    if not app_url:
        return SmokeTestResult(passed=False, output="mvp.config.json v2 readiness must include at least one URL")

    if not isinstance(config.get("targets"), dict) or not config["targets"]:
        return SmokeTestResult(passed=False, output="mvp.config.json v2 field `targets` must be a non-empty object")
    if not isinstance(config.get("flows"), list) or not config["flows"]:
        return SmokeTestResult(passed=False, output="mvp.config.json v2 field `flows` must be a non-empty list")
    first_flow = config["flows"][0]
    if not isinstance(first_flow, dict) or not isinstance(first_flow.get("steps"), list) or not first_flow["steps"]:
        return SmokeTestResult(passed=False, output="mvp.config.json v2 first flow must include non-empty steps")

    config["_contract_version"] = 2
    config["_workspace"] = str(workspace)
    config["_start_command"] = runtime.get("start_command") or f"docker compose -f {shlex.quote(compose_file)} up -d"
    config["_seed_command"] = runtime.get("seed_command")
    config["_app_url"] = app_url
    return config


def _v2_app_url(readiness: list) -> str | None:
    first_url = None
    for item in readiness:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"].strip():
            continue
        first_url = first_url or item["url"]
        if item.get("name") == "frontend":
            return item["url"]
    return first_url


def _flow_steps(config: dict[str, Any]) -> tuple[list, str]:
    flow = config["flows"][0]
    flow_id = flow.get("id") or "0"
    return flow["steps"], f"flows.{flow_id}.steps"


def _wait_for_readiness(config: dict[str, Any]) -> SmokeTestResult:
    if config.get("_contract_version") != 2:
        return SmokeTestResult(passed=True, output="")

    lines = ["readiness checks"]
    for item in config.get("readiness", []):
        if not isinstance(item, dict):
            return SmokeTestResult(passed=False, output="readiness entry is not an object")
        name = item.get("name", "unnamed")
        url = item.get("url")
        expected = item.get("expect_status", 200)
        timeout_ms = int(item.get("timeout", 120_000))
        ok, observed = _wait_for_readiness_url(url, expected, timeout_ms)
        lines.append(f"- {name}: {url} expected={expected} observed={observed}")
        if not ok:
            return SmokeTestResult(passed=False, output="\n".join(lines))
    return SmokeTestResult(passed=True, output="\n".join(lines))


def _wait_for_readiness_url(url: str, expected_status, timeout_ms: int) -> tuple[bool, str]:
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_observed = "<not requested>"
    expected = expected_status if isinstance(expected_status, list) else [expected_status]
    while time.monotonic() <= deadline:
        try:
            request = Request(url, method="GET")
            with urlopen(request, timeout=5) as response:
                status = response.getcode()
            last_observed = str(status)
            if status in expected:
                return True, last_observed
        except HTTPError as exc:
            last_observed = str(exc.code)
            if exc.code in expected:
                return True, last_observed
        except Exception as exc:
            last_observed = f"{exc.__class__.__name__}: {exc}"
        time.sleep(1)
    return False, last_observed


def _run_safe_command(command: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    tokens = shlex.split(command)
    if not tokens:
        return subprocess.CompletedProcess(args=command, returncode=1, stdout="", stderr="Empty command")
    if not _is_allowed_command(tokens):
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=f"Command is not allowed in smoke-test contract: {command}",
        )

    try:
        return subprocess.run(
            tokens,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            args=tokens,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=f"Command timed out after {timeout}s. Use a non-blocking start command such as `docker compose up -d`.",
        )


def _is_allowed_command(tokens: list[str]) -> bool:
    if any(token in FORBIDDEN_COMMAND_PARTS for token in tokens):
        return False
    return any(tuple(tokens[: len(prefix)]) == prefix for prefix in ALLOWED_COMMAND_PREFIXES)


def _format_command_result(name: str, result: subprocess.CompletedProcess[str]) -> str:
    output = f"{result.stdout or ''}\n{result.stderr or ''}".strip()
    return f"$ {name}: {result.args}\n# exit: {result.returncode}\n{output}".strip()


def _normalize_step(step: str | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(step, str):
        return step, {}
    if isinstance(step, dict) and isinstance(step.get("action"), str):
        return step["action"], step
    raise ValueError(f"Invalid smoke_flow step: {step!r}")


def _run_step(
    page,
    app_url: str,
    config: dict[str, Any],
    action: str,
    payload: dict[str, Any],
    step_path: str,
) -> None:
    try:
        _run_step_action(page, app_url, config, action, payload, step_path)
    except _SmokeStepError:
        raise
    except Exception as exc:
        raise _SmokeStepError(step_path, action, payload, exc) from exc


def _run_step_action(
    page,
    app_url: str,
    config: dict[str, Any],
    action: str,
    payload: dict[str, Any],
    step_path: str,
) -> None:
    if action == "goto":
        page.goto(_url_for(app_url, payload["path"]), wait_until="networkidle")
        if payload.get("expect_text"):
            page.get_by_text(payload["expect_text"]).first.wait_for()
    elif action == "click":
        _click_with_expect_request(page, app_url, config, payload, step_path)
    elif action == "fill":
        page.locator(_selector_from_payload(config, payload, step_path)).fill(str(payload["value"]))
    elif action == "select":
        page.locator(_selector_from_payload(config, payload, step_path)).select_option(str(payload["value"]))
    elif action == "expect_text":
        page.get_by_text(payload["text"], exact=bool(payload.get("exact", False))).first.wait_for(
            timeout=_optional_timeout(payload)
        )
    elif action == "expect_url":
        page.wait_for_url(payload["url"], timeout=_optional_timeout(payload))
    elif action == "wait_for_selector":
        page.locator(_selector_from_payload(config, payload, step_path)).wait_for(timeout=_optional_timeout(payload))
    elif action == "upload_file":
        page.locator(_selector_from_payload(config, payload, step_path)).set_input_files(
            _file_path_from_payload(config, payload)
        )
    elif action == "expect_download":
        with page.expect_download(timeout=_optional_timeout(payload)):
            _click(page, payload, step_path, config)
    elif action == "wait_for_response":
        page.wait_for_response(payload["url"], timeout=_optional_timeout(payload))
    elif action == "screenshot":
        page.screenshot(path=payload["path"], full_page=bool(payload.get("full_page", True)))
    elif action == "expect_styled":
        _assert_styled(page)
    elif action == "wait_for_outcome":
        _wait_for_outcome(page, config, payload)
    else:
        raise ValueError(f"Unknown smoke action: {action}")


def _run_primary_entity_step(
    page,
    app_url: str,
    config: dict[str, Any],
    action: str,
    payload: dict[str, Any],
    step_path: str,
) -> None:
    primary = {**config.get("primary_entity", {}), **payload}
    if action == "open_primary_entity_list":
        list_path = primary.get("list_path")
        if not list_path:
            raise ValueError("primary_entity.list_path is required for open_primary_entity_list")
        page.goto(_url_for(app_url, list_path), wait_until="networkidle")
        _assert_nonblank(page)
    elif action == "create_primary_entity":
        create = primary.get("create_flow")
        if not isinstance(create, list) or not create:
            raise ValueError("primary_entity.create_flow is required for create_primary_entity")
        for index, step in enumerate(create):
            step_action, step_payload = _normalize_step(step)
            _run_step(page, app_url, config, step_action, step_payload, f"primary_entity.create_flow[{index}]")
    elif action == "verify_entity_visible":
        text = primary.get("expect_text")
        if not text:
            raise ValueError("primary_entity.expect_text is required for verify_entity_visible")
        page.get_by_text(text).first.wait_for()


def _login(page, demo_user: dict[str, str]) -> None:
    _fill_first(page, ["input[type='email']", "input[name='email']", "input[placeholder='Email']"], demo_user["email"])
    _fill_first(
        page,
        [
            "input[type='password']",
            "input[name='password']",
            "input[placeholder='Пароль']",
            "input[placeholder='Password']",
        ],
        demo_user["password"],
    )
    _click(page, {"text": "Войти", "fallback_text": "Login"}, "login.submit")
    page.wait_for_load_state("networkidle")


def _optional_timeout(payload: dict[str, Any]) -> int | None:
    value = payload.get("timeout")
    if value is None:
        return None
    return int(value)


def _fill_first(page, selectors: list[str], value: str) -> None:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            locator.first.fill(value)
            return
    raise ValueError(f"Could not find input for selectors: {selectors}")


def _click_with_expect_request(
    page,
    app_url: str,
    config: dict[str, Any],
    payload: dict[str, Any],
    step_path: str,
) -> None:
    expect_request = payload.get("expect_request")
    if not expect_request:
        _click(page, payload, step_path, config)
        return

    timeout = _optional_timeout(expect_request) or _optional_timeout(payload)
    with page.expect_response(
        lambda response: _matches_expected_response(response, app_url, expect_request),
        timeout=timeout,
    ):
        _click(page, payload, step_path, config)


def _click(page, payload: dict[str, Any], step_path: str, config: dict[str, Any] | None = None) -> None:
    selector = payload.get("selector")
    if not selector and config is not None:
        selector = _selector_from_target(config, payload.get("target"))
    if selector:
        page.locator(selector).click()
        return

    text = payload.get("text")
    if text:
        locator = page.get_by_text(text, exact=bool(payload.get("exact", False)))
        if locator.count() > 0:
            locator.first.click()
            return

    fallback_text = payload.get("fallback_text")
    if fallback_text:
        page.get_by_text(fallback_text, exact=bool(payload.get("exact", False))).first.click()
        return

    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raise ValueError(f"{step_path}: click step requires `selector`, `text`, or `fallback_text`. payload={payload_json}")


def _selector_from_payload(config: dict[str, Any], payload: dict[str, Any], step_path: str) -> str:
    selector = payload.get("selector") or _selector_from_target(config, payload.get("target"))
    if not selector:
        raise ValueError(f"{step_path}: step requires `selector` or v2 `target`.")
    return selector


def _selector_from_target(config: dict[str, Any], target_name: Any) -> str | None:
    if not target_name:
        return None
    target = config.get("targets", {}).get(target_name)
    if not isinstance(target, dict) or not isinstance(target.get("selector"), str):
        raise ValueError(f"Unknown smoke target: {target_name}")
    return target["selector"]


def _file_path_from_payload(config: dict[str, Any], payload: dict[str, Any]) -> str:
    path = payload.get("path")
    if payload.get("file"):
        files = config.get("fixtures", {}).get("files", {})
        path = files.get(payload["file"])
        if not path:
            raise ValueError(f"Unknown fixture file: {payload['file']}")
    if not path:
        raise ValueError("upload_file step requires `path` or v2 logical `file`.")

    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = Path(config.get("_workspace") or ".") / file_path
    return str(file_path)


def _matches_expected_response(response, app_url: str, expectation: dict[str, Any]) -> bool:
    request = getattr(response, "request", None)
    method = getattr(request, "method", "")
    expected_method = str(expectation.get("method", "")).upper()
    if expected_method and method.upper() != expected_method:
        return False

    statuses = expectation.get("status")
    if isinstance(statuses, int):
        statuses = [statuses]
    if isinstance(statuses, list) and statuses and getattr(response, "status", None) not in statuses:
        return False

    expected_url = expectation.get("url")
    actual_url = getattr(response, "url", "")
    return _url_matches(app_url, actual_url, expected_url)


def _url_matches(app_url: str, actual_url: str, expected_url: str) -> bool:
    if not isinstance(expected_url, str) or not expected_url:
        return False
    actual_path = urlparse(actual_url).path
    if "*" in expected_url:
        return fnmatch.fnmatch(actual_url, expected_url) or fnmatch.fnmatch(actual_path, expected_url)
    if expected_url.startswith(("http://", "https://")):
        return actual_url.rstrip("/") == expected_url.rstrip("/")
    if expected_url.startswith("/"):
        return actual_path.rstrip("/") == expected_url.rstrip("/")
    return actual_url.endswith(expected_url)


def _wait_for_outcome(page, config: dict[str, Any], payload: dict[str, Any]) -> None:
    timeout = _optional_timeout(payload) or 30_000
    deadline = time.monotonic() + (timeout / 1000)
    success_target = payload.get("success", {}).get("target") if isinstance(payload.get("success"), dict) else None
    failure_target = payload.get("failure", {}).get("target") if isinstance(payload.get("failure"), dict) else None
    if not success_target or not failure_target:
        raise ValueError("wait_for_outcome requires success.target and failure.target")

    while time.monotonic() <= deadline:
        if _target_is_visible(page, config, failure_target):
            raise AssertionError(f"Failure outcome target became visible: {failure_target}")
        if _target_is_visible(page, config, success_target):
            return
        page.wait_for_timeout(250)
    raise TimeoutError(f"Timed out waiting for outcome success target: {success_target}")


def _target_is_visible(page, config: dict[str, Any], target_name: str) -> bool:
    try:
        selector = _selector_from_target(config, target_name)
        locator = page.locator(selector).first
        return locator.is_visible()
    except Exception:
        return False


def _format_browser_failure(page, diagnostics: _BrowserDiagnostics, exc: BaseException) -> str:
    lines = [
        "browser smoke failure diagnostics",
        f"failing_step: {getattr(exc, 'step_path', 'unknown')}",
        f"action: {getattr(exc, 'action', 'unknown')}",
        f"payload: {_json_dumps(getattr(exc, 'payload', {}))}",
        f"error: {exc.__class__.__name__}: {exc}",
        f"current_url: {_safe_page_url(page)}",
        f"page_title: {_safe_page_title(page)}",
    ]

    body_text = _safe_page_body_text(page)
    lines.append("body_text_tail:")
    lines.append(_tail_text(body_text, MAX_DIAGNOSTIC_TEXT_CHARS) or "<empty>")

    storage = _read_browser_storage(page)
    lines.append("localStorage:")
    lines.extend(_format_storage_entries(storage.get("localStorage", {})))
    lines.append("sessionStorage:")
    lines.extend(_format_storage_entries(storage.get("sessionStorage", {})))

    lines.append("cookies:")
    lines.extend(_format_cookie_entries(_read_cookie_metadata(page)))

    lines.append("console_errors:")
    lines.extend(_format_mapping_entries(list(diagnostics.console_errors)))
    lines.append("failed_requests:")
    lines.extend(_format_mapping_entries(list(diagnostics.failed_requests)))
    lines.append("responses_4xx_5xx:")
    lines.extend(_format_mapping_entries(list(diagnostics.bad_responses)))
    lines.append("recent_network:")
    lines.extend(_format_mapping_entries(list(diagnostics.recent_network)))

    lines.append("html_tail:")
    lines.append(_tail_text(_safe_page_html(page), MAX_DIAGNOSTIC_TEXT_CHARS) or "<empty>")
    return "\n".join(lines)


def _guard_handler(handler):
    def wrapped(*args, **kwargs):
        try:
            handler(*args, **kwargs)
        except Exception:
            return None

    return wrapped


def _request_snapshot(event: str, request) -> dict[str, Any]:
    return {
        "event": event,
        "method": getattr(request, "method", None),
        "url": redact_secrets(getattr(request, "url", "")),
        "resource_type": getattr(request, "resource_type", None),
    }


def _read_browser_storage(page) -> dict[str, Any]:
    if page is None:
        return {"localStorage": {"<error>": "page unavailable"}, "sessionStorage": {"<error>": "page unavailable"}}
    try:
        return page.evaluate(
            """
            () => {
              const collect = (storage) => {
                const entries = {};
                for (let index = 0; index < storage.length; index += 1) {
                  const key = storage.key(index);
                  entries[key] = storage.getItem(key);
                }
                return entries;
              };
              return {
                localStorage: collect(window.localStorage),
                sessionStorage: collect(window.sessionStorage),
              };
            }
            """
        )
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {exc}"
        return {"localStorage": {"<error>": error}, "sessionStorage": {"<error>": error}}


def _read_cookie_metadata(page) -> list[dict[str, Any]]:
    if page is None:
        return [{"error": "page unavailable"}]
    try:
        cookies = page.context.cookies()
    except Exception as exc:
        return [{"error": f"{exc.__class__.__name__}: {exc}"}]
    return [
        {
            "name": cookie.get("name"),
            "domain": cookie.get("domain"),
            "path": cookie.get("path"),
            "httpOnly": cookie.get("httpOnly"),
            "secure": cookie.get("secure"),
            "sameSite": cookie.get("sameSite"),
        }
        for cookie in cookies
    ]


def _format_storage_entries(entries: dict[str, Any]) -> list[str]:
    if not entries:
        return ["- <empty>"]
    lines = []
    for key in sorted(entries):
        value = entries[key]
        safe_key = redact_secrets(key)
        lines.append(f"- {safe_key}: {_redact_storage_value(key, value)}")
    return lines


def _format_cookie_entries(entries: list[dict[str, Any]]) -> list[str]:
    if not entries:
        return ["- <empty>"]
    return [f"- {_json_dumps(entry)}" for entry in entries]


def _format_mapping_entries(entries: list[dict[str, Any]]) -> list[str]:
    if not entries:
        return ["- <empty>"]
    return [f"- {_json_dumps(entry)}" for entry in entries]


def _redact_storage_value(key: str, value: Any) -> str:
    text = "" if value is None else str(value)
    if SENSITIVE_STORAGE_KEY_RE.search(key):
        return "***"
    text = redact_secrets(text)
    text = SENSITIVE_STORAGE_VALUE_RE.sub("***", text)
    return _tail_text(text, MAX_STORAGE_VALUE_CHARS)


def _safe_page_url(page) -> str:
    if page is None:
        return "<page unavailable>"
    return redact_secrets(getattr(page, "url", ""))


def _safe_page_title(page) -> str:
    if page is None:
        return "<page unavailable>"
    try:
        return redact_secrets(page.title())
    except Exception as exc:
        return f"<title unavailable: {exc.__class__.__name__}: {exc}>"


def _safe_page_body_text(page) -> str:
    if page is None:
        return ""
    try:
        return redact_secrets(page.locator("body").inner_text())
    except Exception as exc:
        return f"<body unavailable: {exc.__class__.__name__}: {exc}>"


def _safe_page_html(page) -> str:
    if page is None:
        return ""
    try:
        return redact_secrets(page.content())
    except Exception as exc:
        return f"<html unavailable: {exc.__class__.__name__}: {exc}>"


def _tail_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _assert_nonblank(page) -> None:
    text = page.locator("body").inner_text().strip()
    if not text:
        raise ValueError("Page body is blank")


def _assert_styled(page) -> None:
    result = page.evaluate(
        """
        () => {
          const visible = (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };

          const defaultLinkColors = new Set([
            "rgb(0, 0, 238)",
            "rgb(85, 26, 139)",
            "rgb(0, 0, 0)"
          ]);
          const defaultButtonBackgrounds = new Set([
            "rgb(239, 239, 239)",
            "rgb(240, 240, 240)",
            "rgba(0, 0, 0, 0)",
            "transparent"
          ]);

          const controls = Array.from(document.querySelectorAll("button, a, input, select, textarea"));
          const styledControls = controls.filter((el) => {
            if (!visible(el)) return false;
            const style = window.getComputedStyle(el);
            const tag = el.tagName.toLowerCase();
            const padding = ["Top", "Right", "Bottom", "Left"]
              .map((side) => Number.parseFloat(style[`padding${side}`]) || 0)
              .reduce((sum, value) => sum + value, 0);
            const radius = Number.parseFloat(style.borderRadius) || 0;
            const hasBoxStyling = padding >= 18 || radius >= 4 || style.boxShadow !== "none";
            const hasColorStyling =
              (tag === "a" && !defaultLinkColors.has(style.color)) ||
              (tag === "button" && !defaultButtonBackgrounds.has(style.backgroundColor));
            return hasBoxStyling || hasColorStyling;
          });

          const utilityElements = Array.from(document.querySelectorAll("[class]"))
            .filter((el) => /(^|\\s)(bg-|text-|p-|px-|py-|rounded|shadow|border|grid|flex|gap-)/.test(el.className || ""));
          const paddedUtilityElements = utilityElements.filter((el) => {
            if (!visible(el)) return false;
            const style = window.getComputedStyle(el);
            const padding = ["Top", "Right", "Bottom", "Left"]
              .map((side) => Number.parseFloat(style[`padding${side}`]) || 0)
              .reduce((sum, value) => sum + value, 0);
            return padding >= 16 || style.backgroundColor !== "rgba(0, 0, 0, 0)" || style.boxShadow !== "none";
          });

          return {
            controlCount: controls.filter(visible).length,
            styledControlCount: styledControls.length,
            utilityElementCount: utilityElements.length,
            paddedUtilityElementCount: paddedUtilityElements.length,
          };
        }
        """
    )

    if result["controlCount"] > 0 and result["styledControlCount"] == 0:
        raise ValueError(f"Visible controls look browser-default, not styled: {result}")
    if result["utilityElementCount"] >= 5 and result["paddedUtilityElementCount"] == 0:
        raise ValueError(f"Utility CSS classes are present but do not affect layout/colors: {result}")


def _url_for(app_url: str, path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{app_url}/{path.lstrip('/')}"


def _flow_has_action(flow: list, action: str) -> bool:
    for step in flow:
        if step == action:
            return True
        if isinstance(step, dict) and step.get("action") == action:
            return True
    return False
