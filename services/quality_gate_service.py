import json
import re
import shlex
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import git
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from utils.logger import get_logger

logger = get_logger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)))
DOCKER_CI_SCRIPT_PATH = ".pipeline/docker-ci.sh"


REQUIRED_GITIGNORE_PATTERNS = [
    "node_modules/",
    "dist/",
    "build/",
    "coverage/",
    ".cache/",
    ".next/",
    ".nuxt/",
    ".turbo/",
    ".vite/",
    ".pytest_cache/",
    ".mypy_cache/",
    "__pycache__/",
    "*.py[cod]",
    ".env",
    ".env.*",
    "!.env.example",
    "!.env.sample",
    "!.env.template",
    "*.log",
    "logs/",
    ".DS_Store",
]

COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
ENV_TEMPLATE_FILENAMES = (
    ".env.example",
    ".env.sample",
    ".env.template",
)
KNOWN_INVALID_COMPOSE_IMAGES = {
    "postgres:16-slim": "Use a published official PostgreSQL image tag such as `postgres:16-alpine` or `postgres:16`.",
    "redis:7-slim": "Use a published official Redis image tag such as `redis:7-alpine` or `redis:7`.",
}
EXTERNAL_VENDOR_HINTS = {
    "anthropic": ("anthropic", "api.anthropic.com"),
    "elevenlabs": ("elevenlabs", "api.elevenlabs.io"),
    "fal": ("fal.ai", "fal-ai", "api.fal.ai"),
    "gemini": ("gemini", "generativelanguage.googleapis.com", "google.generativeai"),
    "huggingface": ("huggingface", "hugging_face", "api-inference.huggingface.co"),
    "openai": ("openai", "api.openai.com"),
    "replicate": ("replicate", "api.replicate.com"),
    "stability": ("stability", "stability.ai", "api.stability.ai"),
    "stripe": ("stripe", "api.stripe.com"),
    "together": ("together", "api.together.xyz"),
}
EXTERNAL_SECRET_ENV_SUFFIXES = (
    "API_KEY",
    "API_TOKEN",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "BEARER_TOKEN",
    "CLIENT_SECRET",
    "SECRET_KEY",
)
INTERNAL_SECRET_ENV_NAMES = {
    "APP_SECRET",
    "CSRF_SECRET",
    "DATABASE_URL",
    "DB_PASSWORD",
    "JWT_SECRET",
    "NEXTAUTH_SECRET",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "SECRET_KEY",
    "SESSION_SECRET",
    "WEBHOOK_SECRET",
}
INTERNAL_SECRET_NAME_PARTS = (
    "DATABASE",
    "DB_",
    "JWT",
    "SESSION",
    "WEBHOOK",
    "COOKIE",
    "CSRF",
    "POSTGRES",
    "REDIS",
)
PLACEHOLDER_SECRET_VALUES = {
    "",
    "changeme",
    "demo",
    "dummy",
    "example",
    "fake",
    "placeholder",
    "replace_me",
    "test",
    "todo",
    "your_api_key",
    "your_key_here",
    "your_token_here",
}
MOCK_PROVIDER_VALUES = {
    "demo",
    "disabled",
    "fake",
    "local",
    "mock",
    "none",
    "offline",
    "stub",
    "test",
}
REAL_PROVIDER_VALUES = {
    "anthropic",
    "elevenlabs",
    "fal",
    "gemini",
    "huggingface",
    "openai",
    "production",
    "real",
    "replicate",
    "stability",
    "stripe",
    "together",
}
PROVIDER_MODE_NAME_RE = re.compile(r"(PROVIDER|MODE|DRIVER|ADAPTER)$")

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
    "build",
    "coverage",
    ".cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".vite",
}

LOCK_AND_GENERATED_FILES = {
    ".gitignore",
    "MVP_REPORT.md",
    "mvp.config.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
}

SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".html",
}

FRONTEND_PACKAGE_HINTS = {
    "@vitejs/plugin-react",
    "vite",
    "react",
    "react-dom",
    "next",
    "vue",
    "svelte",
}

TAILWIND_UTILITY_RE = re.compile(
    r"\b(?:bg|text|p|px|py|m|mx|my|rounded|shadow|border|grid|flex|gap|"
    r"items|justify|w|h|min-h|max-w|space-y|space-x)-[A-Za-z0-9_:/.[\]%-]+"
)

STATS_SUFFIXES = SOURCE_SUFFIXES | {
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".sql",
    ".prisma",
    ".env.example",
}

STATS_FILENAMES = {
    "Dockerfile",
    "docker-compose.yml",
    "Makefile",
    "requirements.txt",
    ".env.example",
}

MUTATING_BUTTON_LABELS = (
    "new",
    "create",
    "add",
    "edit",
    "delete",
    "save",
    "submit",
    "send",
    "complete",
    "approve",
    "reject",
    "нов",
    "созда",
    "добав",
    "редакт",
    "удал",
    "сохран",
    "отправ",
    "выполн",
    "аппрув",
    "отклон",
)

PLACEHOLDER_RE = re.compile(
    r"\b(placeholder|todo|coming soon|not implemented|logic would go here|stub)\b",
    re.IGNORECASE,
)
README_DOCKER_START_RE = re.compile(r"\bdocker\s+compose\b[\s\S]{0,120}\bup\b", re.IGNORECASE)
EMPTY_HANDLER_RE = re.compile(
    r"on[A-Z][A-Za-z0-9]*\s*=\s*\{\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{\s*\}\s*\}",
    re.DOTALL,
)
STATE_ONLY_OK_RE = re.compile(
    r"onOk\s*=\s*\{\s*(?:async\s*)?\([^)]*\)\s*=>\s*(?:\{\s*)?"
    r"set[A-Za-z_$][\w$]*\s*\(\s*false\s*\)\s*;?\s*(?:\}\s*)?\}",
    re.DOTALL,
)
BUTTON_RE = re.compile(r"<Button\b(?P<attrs>[^>]*)>(?P<body>.*?)</Button>", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
SUCCESS_RE = re.compile(r"\b(?:message|antdMessage|toast)\.success\s*\(", re.IGNORECASE)
REAL_MUTATION_RE = re.compile(
    r"(mutateAsync|mutate\s*\(|fetch\s*\(|axios\.|api\.|client\.|http\.|"
    r"\.(?:post|patch|put|delete)\s*\(|await\s+[A-Za-z_$][\w$]*\s*\()"
)
KNOWN_SMOKE_ACTIONS = {
    "open_app",
    "login",
    "goto",
    "click",
    "fill",
    "select",
    "expect_text",
    "wait_for_text",
    "expect_url",
    "wait_for_selector",
    "upload_file",
    "expect_download",
    "wait_for_response",
    "screenshot",
    "expect_styled",
    "open_dashboard",
    "open_primary_entity_list",
    "create_primary_entity",
    "verify_entity_visible",
}
V2_SMOKE_ACTIONS = {
    "goto",
    "click",
    "fill",
    "select",
    "expect_text",
    "expect_url",
    "wait_for_selector",
    "upload_file",
    "expect_download",
    "wait_for_response",
    "screenshot",
    "expect_styled",
    "wait_for_outcome",
}
V2_TARGET_ACTIONS = {
    "click",
    "fill",
    "select",
    "wait_for_selector",
    "upload_file",
    "expect_download",
}
DATA_TESTID_RE = re.compile(r"\bdata-testid\b")


class QualityGateIssue(BaseModel):
    severity: str
    code: str
    description: str
    path: str | None = None
    line: int | None = None


class QualityGateResult(BaseModel):
    passed: bool
    output: str
    issues: list[QualityGateIssue] = []
    own_code_files: int = 0
    own_code_lines: int = 0


class QualityGateService:
    def run(self, workspace_path: str | Path) -> QualityGateResult:
        workspace = Path(workspace_path)
        ensure_mvp_gitignore(workspace)
        untracked_artifacts = untrack_forbidden_artifacts(workspace)

        issues = []
        issues.extend(_mvp_config_issues(workspace))
        issues.extend(_readme_issues(workspace))
        issues.extend(_docker_runtime_issues(workspace))
        issues.extend(_docker_ci_script_issues(workspace))
        issues.extend(_external_api_secret_issues(workspace))
        issues.extend(_tracked_artifact_issues(workspace))
        issues.extend(_managed_ci_issues(workspace))
        issues.extend(_source_quality_issues(workspace))
        issues.extend(_frontend_styling_issues(workspace))
        own_code_files, own_code_lines = _own_code_stats(workspace)

        passed = not any(issue.severity in {"critical", "major"} for issue in issues)
        result = QualityGateResult(
            passed=passed,
            output=_format_output(issues, own_code_files, own_code_lines),
            issues=issues,
            own_code_files=own_code_files,
            own_code_lines=own_code_lines,
        )
        write_mvp_report(workspace, result)

        logger.info(
            "quality_gate.finished",
            workspace=str(workspace),
            passed=passed,
            issues=len(issues),
            untracked_artifacts=len(untracked_artifacts),
            own_code_files=own_code_files,
            own_code_lines=own_code_lines,
        )
        return result


def ensure_mvp_gitignore(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    gitignore = workspace / ".gitignore"
    existing = gitignore.read_text().splitlines() if gitignore.exists() else []
    existing_normalized = {line.strip() for line in existing if line.strip()}
    missing = [pattern for pattern in REQUIRED_GITIGNORE_PATTERNS if pattern not in existing_normalized]
    if not missing:
        return

    lines = existing[:]
    if lines and lines[-1].strip():
        lines.append("")
    lines.append("# Generated MVP hygiene")
    lines.extend(missing)
    gitignore.write_text("\n".join(lines).rstrip() + "\n")
    logger.info("quality_gate.gitignore_updated", path=str(gitignore), patterns=len(missing))


def write_mvp_report(workspace: Path, result: QualityGateResult) -> None:
    lines = [
        "# MVP Quality Report",
        "",
        f"- Status: {'passed' if result.passed else 'failed'}",
        f"- Own code files: {result.own_code_files}",
        f"- Own code lines: {result.own_code_lines}",
        f"- Issues: {len(result.issues)}",
        "",
        "## Checks",
        "",
        "- Root .gitignore contains dependency, build, cache, log, and env exclusions.",
        "- Root README.md describes the MVP and includes real setup/run commands.",
        "- Root mvp.config.json uses contract v2 with runtime/readiness, data-testid targets, and a browser smoke flow.",
        "- Git-tracked dependency/build/secret artifacts are blocked.",
        "- Obvious placeholders, empty handlers, fake-success actions, and unwired mutating buttons are blocked.",
        "- Docker Compose runtime contract is checked when the MVP start command uses Docker.",
        "- Docker env files use ignored local `.env` plus a committed safe template such as `.env.example`.",
        "- External API integrations with placeholder secrets must default to a mock/demo/local provider.",
        "- Frontend styling setup is checked for obvious broken Tailwind/CSS wiring.",
        "- Smoke-test contract must assert backend side effects and wait for final success/failure outcome.",
    ]
    if result.issues:
        lines.extend(["", "## Issues", ""])
        for issue in result.issues:
            location = (
                f" ({issue.path}:{issue.line})"
                if issue.path and issue.line
                else f" ({issue.path})"
                if issue.path
                else ""
            )
            lines.append(f"- [{issue.severity}] {issue.code}{location}: {issue.description}")

    (workspace / "MVP_REPORT.md").write_text("\n".join(lines).rstrip() + "\n")


def _readme_issues(workspace: Path) -> list[QualityGateIssue]:
    readme_path = workspace / "README.md"
    if not readme_path.exists():
        return [
            QualityGateIssue(
                severity="major",
                code="missing_readme",
                path="README.md",
                description=(
                    "Generated MVP must include a root README.md with a short project overview and "
                    "exact setup/run commands."
                ),
            )
        ]

    try:
        text = readme_path.read_text(errors="ignore")
    except OSError as exc:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_readme",
                path="README.md",
                description=f"README.md could not be read: {exc}",
            )
        ]

    stripped = text.strip()
    issues: list[QualityGateIssue] = []
    word_count = len(re.findall(r"[A-Za-zА-Яа-я0-9]+", stripped))
    if word_count < 30 or not re.search(r"(?m)^#\s+\S", stripped):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_readme",
                path="README.md",
                description=("README.md must include a short project overview, not only command fragments."),
            )
        )

    if not README_DOCKER_START_RE.search(stripped):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_readme",
                path="README.md",
                description=(
                    "README.md must document the real Docker Compose startup command, for example "
                    "`docker compose up -d`."
                ),
            )
        )

    env_template_names = {path.name for path in workspace.iterdir() if path.name in ENV_TEMPLATE_FILENAMES}
    if env_template_names and not any(name in stripped for name in env_template_names):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_readme",
                path="README.md",
                description=("README.md must mention how to create local env config from the committed env template."),
            )
        )

    return issues


def _mvp_config_issues(workspace: Path) -> list[QualityGateIssue]:
    config_path = workspace / "mvp.config.json"
    if not config_path.exists():
        return [
            QualityGateIssue(
                severity="major",
                code="missing_mvp_config",
                path="mvp.config.json",
                description=(
                    "Generated MVP must declare a runnable mvp.config.json v2 smoke contract with "
                    "runtime, readiness, data-testid targets, flows, expect_request, and wait_for_outcome."
                ),
            )
        ]

    try:
        data = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description=f"mvp.config.json must be valid JSON: {exc}",
            )
        ]

    if data.get("version") != 2:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description=(
                    "mvp.config.json must use contract v2. Legacy smoke_flow/primary_entity contracts are too "
                    "fragile for generated MVP CI; declare version, runtime, readiness, data-testid targets, flows, "
                    "expect_request, and wait_for_outcome."
                ),
            )
        ]

    return _mvp_config_v2_issues(workspace, data)


def _mvp_config_v1_issues(data: dict) -> list[QualityGateIssue]:
    issues = []
    for field in ("app_url", "start_command"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_mvp_config",
                    path="mvp.config.json",
                    description=f"`{field}` is required and must be a non-empty string.",
                )
            )

    start_command = data.get("start_command")
    if isinstance(start_command, str) and _is_blocking_start_command(start_command):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="`start_command` must be non-blocking. Use `docker compose up -d` instead of foreground `docker compose up`.",
            )
        )

    smoke_flow = data.get("smoke_flow")
    if not isinstance(smoke_flow, list) or not smoke_flow:
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="`smoke_flow` is required and must be a non-empty list.",
            )
        )
    else:
        issues.extend(_smoke_flow_issues(smoke_flow))
        issues.extend(_smoke_action_payload_issues(smoke_flow, "smoke_flow"))
        issues.extend(_primary_flow_issues(data, smoke_flow))

    demo_user = data.get("demo_user")
    if _smoke_flow_contains(smoke_flow, "login") and not _valid_demo_user(demo_user):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="`demo_user.email` and `demo_user.password` are required when smoke_flow contains login.",
            )
        )

    return issues


def _mvp_config_v2_issues(workspace: Path, data: dict) -> list[QualityGateIssue]:
    issues = []
    runtime = data.get("runtime")
    if not isinstance(runtime, dict):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `runtime` is required and must describe the runnable environment.",
            )
        )
        runtime = {}

    runtime_type = runtime.get("type")
    if runtime_type != "docker_compose":
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `runtime.type` must be `docker_compose` for the current CI runtime verifier.",
            )
        )

    compose_file = runtime.get("compose_file")
    if not _non_empty_string(compose_file):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `runtime.compose_file` is required and must point to the Docker Compose file.",
            )
        )

    start_command = _config_start_command(data)
    if isinstance(start_command, str) and _is_blocking_start_command(start_command):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 runtime startup must be non-blocking. Use detached Docker Compose startup.",
            )
        )

    readiness = data.get("readiness")
    if not isinstance(readiness, list) or not readiness:
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `readiness` is required and must contain at least one URL/status check.",
            )
        )
    else:
        issues.extend(_v2_readiness_issues(readiness))

    fixtures = data.get("fixtures")
    fixture_files = {}
    if fixtures is not None:
        if not isinstance(fixtures, dict):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_mvp_config",
                    path="mvp.config.json",
                    description="v2 `fixtures` must be an object when provided.",
                )
            )
        else:
            files = fixtures.get("files", {})
            if not isinstance(files, dict):
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_mvp_config",
                        path="mvp.config.json",
                        description="v2 `fixtures.files` must map logical fixture names to workspace paths.",
                    )
                )
            else:
                fixture_files = files
                issues.extend(_v2_fixture_file_issues(workspace, fixture_files))

    targets = data.get("targets")
    if not isinstance(targets, dict) or not targets:
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `targets` is required and must map stable target ids to data-testid selectors.",
            )
        )
        targets = {}
    else:
        issues.extend(_v2_target_issues(targets))

    flows = data.get("flows")
    if not isinstance(flows, list) or not flows:
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_mvp_config",
                path="mvp.config.json",
                description="v2 `flows` is required and must contain at least one runnable happy path.",
            )
        )
    else:
        issues.extend(_v2_flow_issues(flows, targets, fixture_files))

    return issues


def _v2_readiness_issues(readiness: list) -> list[QualityGateIssue]:
    issues = []
    for index, item in enumerate(readiness):
        path = f"readiness[{index}]"
        if not isinstance(item, dict):
            issues.append(_invalid_v2_config(f"`{path}` must be an object."))
            continue
        if not _non_empty_string(item.get("name")):
            issues.append(_invalid_v2_config(f"`{path}.name` is required."))
        if not _non_empty_string(item.get("url")):
            issues.append(_invalid_v2_config(f"`{path}.url` is required."))
        if not _valid_status_field(item.get("expect_status")):
            issues.append(_invalid_v2_config(f"`{path}.expect_status` must be an HTTP status integer or list."))
    return issues


def _v2_fixture_file_issues(workspace: Path, files: dict) -> list[QualityGateIssue]:
    issues = []
    for name, path in files.items():
        if not _non_empty_string(name) or not _non_empty_string(path):
            issues.append(_invalid_v2_config("v2 `fixtures.files` keys and values must be non-empty strings."))
            continue
        fixture_path = Path(path)
        if not fixture_path.is_absolute():
            fixture_path = workspace / fixture_path
        if not fixture_path.exists():
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_fixture_file",
                    path="mvp.config.json",
                    description=f"v2 fixture `{name}` points to missing file `{path}`.",
                )
            )
    return issues


def _v2_target_issues(targets: dict) -> list[QualityGateIssue]:
    issues = []
    for name, target in targets.items():
        if not _non_empty_string(name):
            issues.append(_invalid_v2_config("v2 target ids must be non-empty strings."))
            continue
        if not isinstance(target, dict):
            issues.append(_invalid_v2_config(f"v2 target `{name}` must be an object."))
            continue
        selector = target.get("selector")
        if not _non_empty_string(selector):
            issues.append(_invalid_v2_config(f"v2 target `{name}` must include a non-empty selector."))
            continue
        if DATA_TESTID_RE.search(selector) is None:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="unstable_smoke_target",
                    path="mvp.config.json",
                    description=(
                        f"v2 target `{name}` must use a stable data-testid selector, not text or layout selectors."
                    ),
                )
            )
    return issues


def _v2_flow_issues(flows: list, targets: dict, fixture_files: dict) -> list[QualityGateIssue]:
    issues = []
    for flow_index, flow in enumerate(flows):
        flow_path = f"flows[{flow_index}]"
        if not isinstance(flow, dict):
            issues.append(_invalid_v2_config(f"`{flow_path}` must be an object."))
            continue
        flow_id = flow.get("id")
        if not _non_empty_string(flow_id):
            issues.append(_invalid_v2_config(f"`{flow_path}.id` is required."))
        steps = flow.get("steps")
        if not isinstance(steps, list) or not steps:
            issues.append(_invalid_v2_config(f"`{flow_path}.steps` must be a non-empty list."))
            continue

        has_expect_request = False
        has_outcome = False
        for step_index, step in enumerate(steps):
            step_path = f"{flow_path}.steps[{step_index}]"
            step_issues, step_has_request, step_has_outcome = _v2_step_issues(
                step,
                step_path,
                targets,
                fixture_files,
            )
            issues.extend(step_issues)
            has_expect_request = has_expect_request or step_has_request
            has_outcome = has_outcome or step_has_outcome

        if not has_expect_request:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="weak_smoke_flow",
                    path="mvp.config.json",
                    description=(
                        f"`{flow_path}` must assert the network side effect with `expect_request`, "
                        "for example POST /api/jobs after the submit click."
                    ),
                )
            )
        if not has_outcome:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="weak_smoke_flow",
                    path="mvp.config.json",
                    description=(
                        f"`{flow_path}` must end by waiting for a final `wait_for_outcome` success/failure target "
                        "instead of fragile intermediate UI text."
                    ),
                )
            )
    return issues


def _v2_step_issues(
    step,
    step_path: str,
    targets: dict,
    fixture_files: dict,
) -> tuple[list[QualityGateIssue], bool, bool]:
    issues = []
    if not isinstance(step, dict):
        return [_invalid_v2_config(f"`{step_path}` must be an object with an action.")], False, False

    action = step.get("action")
    if action not in V2_SMOKE_ACTIONS:
        return (
            [
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{step_path}` uses unsupported v2 smoke action `{action}`.",
                )
            ],
            False,
            False,
        )

    if action in V2_TARGET_ACTIONS:
        issues.extend(_v2_target_ref_issues(step, "target", targets, step_path))

    if action == "goto" and not _non_empty_string(step.get("path")):
        issues.append(_invalid_v2_step(f"`{step_path}` is a goto step and must include `path`."))
    elif action in {"fill", "select"} and step.get("value") is None:
        issues.append(_invalid_v2_step(f"`{step_path}` is a {action} step and must include `value`."))
    elif action == "expect_text" and not _non_empty_string(step.get("text")):
        issues.append(_invalid_v2_step(f"`{step_path}` is an expect_text step and must include `text`."))
    elif action == "expect_url" and not _non_empty_string(step.get("url")):
        issues.append(_invalid_v2_step(f"`{step_path}` is an expect_url step and must include `url`."))
    elif action == "wait_for_response" and not _non_empty_string(step.get("url")):
        issues.append(_invalid_v2_step(f"`{step_path}` is a wait_for_response step and must include `url`."))
    elif action == "screenshot" and not _non_empty_string(step.get("path")):
        issues.append(_invalid_v2_step(f"`{step_path}` is a screenshot step and must include `path`."))
    elif action == "upload_file":
        file_name = step.get("file")
        if not _non_empty_string(file_name):
            issues.append(_invalid_v2_step(f"`{step_path}` is an upload_file step and must include logical `file`."))
        elif file_name not in fixture_files:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{step_path}` references unknown fixture file `{file_name}`.",
                )
            )
    elif action == "wait_for_outcome":
        issues.extend(_v2_outcome_issues(step, targets, step_path))

    expect_request = step.get("expect_request")
    has_expect_request = expect_request is not None
    if has_expect_request:
        if action != "click":
            issues.append(_invalid_v2_step(f"`{step_path}.expect_request` is only supported on click steps."))
        issues.extend(_v2_expect_request_issues(expect_request, step_path))

    return issues, has_expect_request and action == "click", action == "wait_for_outcome"


def _v2_target_ref_issues(step: dict, field: str, targets: dict, step_path: str) -> list[QualityGateIssue]:
    target_name = step.get(field)
    if not _non_empty_string(target_name):
        return [_invalid_v2_step(f"`{step_path}` must reference a stable target with `{field}`.")]
    if target_name not in targets:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_smoke_step",
                path="mvp.config.json",
                description=f"`{step_path}` references unknown target `{target_name}`.",
            )
        ]
    return []


def _v2_outcome_issues(step: dict, targets: dict, step_path: str) -> list[QualityGateIssue]:
    issues = []
    for field in ("success", "failure"):
        value = step.get(field)
        if not isinstance(value, dict):
            issues.append(_invalid_v2_step(f"`{step_path}.{field}` must be an object with a target."))
            continue
        issues.extend(_v2_target_ref_issues(value, "target", targets, f"{step_path}.{field}"))
    return issues


def _v2_expect_request_issues(value, step_path: str) -> list[QualityGateIssue]:
    if not isinstance(value, dict):
        return [_invalid_v2_step(f"`{step_path}.expect_request` must be an object.")]

    issues = []
    if not _non_empty_string(value.get("method")):
        issues.append(_invalid_v2_step(f"`{step_path}.expect_request.method` is required."))
    if not _non_empty_string(value.get("url")):
        issues.append(_invalid_v2_step(f"`{step_path}.expect_request.url` is required."))
    if not _valid_status_field(value.get("status")):
        issues.append(_invalid_v2_step(f"`{step_path}.expect_request.status` must be an HTTP status integer or list."))
    return issues


def _invalid_v2_config(description: str) -> QualityGateIssue:
    return QualityGateIssue(
        severity="major",
        code="invalid_mvp_config",
        path="mvp.config.json",
        description=description,
    )


def _invalid_v2_step(description: str) -> QualityGateIssue:
    return QualityGateIssue(
        severity="major",
        code="invalid_smoke_step",
        path="mvp.config.json",
        description=description,
    )


def _valid_status_field(value) -> bool:
    if isinstance(value, int):
        return 100 <= value <= 599
    if isinstance(value, list) and value:
        return all(isinstance(item, int) and 100 <= item <= 599 for item in value)
    return False


def _smoke_flow_issues(smoke_flow: list) -> list[QualityGateIssue]:
    issues = []
    for index, step in enumerate(smoke_flow):
        if isinstance(step, str):
            action = step
        elif isinstance(step, dict):
            action = step.get("action")
        else:
            action = None

        if action not in KNOWN_SMOKE_ACTIONS:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_mvp_config",
                    path="mvp.config.json",
                    description=f"smoke_flow[{index}] must use a known action.",
                )
            )
    return issues


def _smoke_action_payload_issues(steps: list, path_prefix: str) -> list[QualityGateIssue]:
    issues = []
    for index, step in enumerate(steps):
        path = f"{path_prefix}[{index}]"
        if isinstance(step, str):
            action = step
            payload = {}
        elif isinstance(step, dict):
            action = step.get("action")
            payload = step
        else:
            continue

        if action not in KNOWN_SMOKE_ACTIONS:
            if path_prefix != "smoke_flow":
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_smoke_step",
                        path="mvp.config.json",
                        description=f"`{path}` must use a known smoke action. Unsupported action: `{action}`.",
                    )
                )
            continue

        if action == "click" and not any(
            _non_empty_string(payload.get(field)) for field in ("selector", "text", "fallback_text")
        ):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a click step and must include `selector`, `text`, or `fallback_text`.",
                )
            )
        elif action == "fill":
            missing = []
            if not _non_empty_string(payload.get("selector")):
                missing.append("selector")
            if payload.get("value") is None:
                missing.append("value")
            if missing:
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_smoke_step",
                        path="mvp.config.json",
                        description=f"`{path}` is a fill step and must include {', '.join(f'`{field}`' for field in missing)}.",
                    )
                )
        elif action == "select":
            missing = []
            if not _non_empty_string(payload.get("selector")):
                missing.append("selector")
            if payload.get("value") is None:
                missing.append("value")
            if missing:
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_smoke_step",
                        path="mvp.config.json",
                        description=f"`{path}` is a select step and must include {', '.join(f'`{field}`' for field in missing)}.",
                    )
                )
        elif action == "goto" and not _non_empty_string(payload.get("path")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a goto step and must include `path`.",
                )
            )
        elif action == "expect_text" and not _non_empty_string(payload.get("text")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is an expect_text step and must include `text`.",
                )
            )
        elif action == "wait_for_text" and not _non_empty_string(payload.get("text")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a wait_for_text step and must include `text`.",
                )
            )
        elif action == "expect_url" and not _non_empty_string(payload.get("url")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is an expect_url step and must include `url`.",
                )
            )
        elif action == "wait_for_selector" and not _non_empty_string(payload.get("selector")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a wait_for_selector step and must include `selector`.",
                )
            )
        elif action == "upload_file":
            missing = []
            if not _non_empty_string(payload.get("selector")):
                missing.append("selector")
            if not _non_empty_string(payload.get("path")):
                missing.append("path")
            if missing:
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_smoke_step",
                        path="mvp.config.json",
                        description=f"`{path}` is an upload_file step and must include {', '.join(f'`{field}`' for field in missing)}.",
                    )
                )
        elif action == "expect_download" and not any(
            _non_empty_string(payload.get(field)) for field in ("selector", "text", "fallback_text")
        ):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is an expect_download step and must include `selector`, `text`, or `fallback_text` for the download trigger.",
                )
            )
        elif action == "wait_for_response" and not _non_empty_string(payload.get("url")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a wait_for_response step and must include `url`.",
                )
            )
        elif action == "screenshot" and not _non_empty_string(payload.get("path")):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="invalid_smoke_step",
                    path="mvp.config.json",
                    description=f"`{path}` is a screenshot step and must include `path`.",
                )
            )

    return issues


def _non_empty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _primary_flow_issues(config: dict, smoke_flow: list) -> list[QualityGateIssue]:
    primary = config.get("primary_entity")
    if not isinstance(primary, dict):
        return [
            QualityGateIssue(
                severity="major",
                code="weak_smoke_flow",
                path="mvp.config.json",
                description=(
                    "`primary_entity` is required so smoke tests can create the main MVP record "
                    "and verify it appears in the UI."
                ),
            )
        ]

    issues = []
    for field in ("list_path", "create_flow", "expect_text"):
        value = primary.get(field)
        if field == "create_flow":
            valid = isinstance(value, list) and bool(value)
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="weak_smoke_flow",
                    path="mvp.config.json",
                    description=f"`primary_entity.{field}` is required for the primary MVP browser flow.",
                )
            )

    create_flow = primary.get("create_flow")
    if isinstance(create_flow, list):
        issues.extend(_smoke_action_payload_issues(create_flow, "primary_entity.create_flow"))

    required_actions = {"expect_styled", "open_primary_entity_list", "create_primary_entity", "verify_entity_visible"}
    missing_actions = sorted(action for action in required_actions if not _smoke_flow_contains(smoke_flow, action))
    if missing_actions:
        issues.append(
            QualityGateIssue(
                severity="major",
                code="weak_smoke_flow",
                path="mvp.config.json",
                description=(
                    "`smoke_flow` must include the primary create/read workflow actions: "
                    + ", ".join(missing_actions)
                    + "."
                ),
            )
        )

    return issues


def _smoke_flow_contains(smoke_flow, action: str) -> bool:
    if not isinstance(smoke_flow, list):
        return False
    for step in smoke_flow:
        if step == action:
            return True
        if isinstance(step, dict) and step.get("action") == action:
            return True
    return False


def _valid_demo_user(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("email"), str)
        and bool(value["email"].strip())
        and isinstance(value.get("password"), str)
        and bool(value["password"].strip())
    )


def _is_blocking_start_command(command: str) -> bool:
    parts = command.split()
    return parts[:3] == ["docker", "compose", "up"] and "-d" not in parts and "--detach" not in parts


def _docker_ci_script_issues(workspace: Path) -> list[QualityGateIssue]:
    config = _read_json(workspace / "mvp.config.json")
    start_command = _config_start_command(config)
    if not isinstance(start_command, str) or not _uses_docker_compose(start_command):
        return []

    rel_path = DOCKER_CI_SCRIPT_PATH
    script_path = workspace / rel_path
    if not script_path.exists():
        return [
            QualityGateIssue(
                severity="major",
                code="missing_docker_ci_script",
                path=rel_path,
                description=(
                    "Generated MVPs must include project-owned Docker CI at `.pipeline/docker-ci.sh`. "
                    "The managed GitHub Actions workflow runs this script for project-specific Docker Compose validation."
                ),
            )
        ]

    text = _safe_read(script_path)
    lowered = text.lower()
    missing_checks = [check for check in ("docker compose", "config", "build", "up") if check not in lowered]
    if missing_checks:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_docker_ci_script",
                path=rel_path,
                description=(
                    "`.pipeline/docker-ci.sh` must perform real Docker Compose validation for this project: "
                    "`docker compose config`, image build, runtime startup, and readiness/seed checks. "
                    f"Missing expected check(s): {', '.join(missing_checks)}."
                ),
            )
        ]

    if "set -e" not in lowered:
        return [
            QualityGateIssue(
                severity="major",
                code="invalid_docker_ci_script",
                path=rel_path,
                description="`.pipeline/docker-ci.sh` must fail fast, for example with `set -euo pipefail`.",
            )
        ]

    return []


def _docker_runtime_issues(workspace: Path) -> list[QualityGateIssue]:
    config = _read_json(workspace / "mvp.config.json")
    start_command = _config_start_command(config)
    if not isinstance(start_command, str) or not _uses_docker_compose(start_command):
        return []

    compose_path = _compose_path_from_command(workspace, start_command)
    if compose_path is None:
        return [
            QualityGateIssue(
                severity="major",
                code="missing_docker_compose",
                path="docker-compose.yml",
                description=(
                    "`mvp.config.json` uses Docker Compose, but no compose file exists at the configured path. "
                    "Generated MVPs must include a runnable Docker Compose contract."
                ),
            )
        ]

    if not compose_path.exists():
        return [
            QualityGateIssue(
                severity="major",
                code="missing_docker_compose",
                path=_relative_path(workspace, compose_path),
                description="Docker Compose file referenced by `mvp.config.json` does not exist.",
            )
        ]

    compose_text = _safe_read(compose_path)
    issues = []
    issues.extend(_compose_image_issues(workspace, compose_path, compose_text))
    issues.extend(_compose_env_file_issues(workspace, compose_path, compose_text))
    build_specs = _compose_build_specs(compose_text, compose_path.parent)
    service_names = _compose_service_names(compose_text)

    if build_specs:
        issues.extend(_docker_build_issues(workspace, build_specs))
        issues.extend(_docker_prisma_runtime_issues(workspace, build_specs))
        issues.extend(_prisma_migration_sql_issues(workspace, build_specs))
        if not _has_dockerignore(workspace, build_specs):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_dockerignore",
                    path=".dockerignore",
                    description=(
                        "Docker build contexts require a .dockerignore so node_modules, build outputs, "
                        "env files, logs, and caches are not sent into images."
                    ),
                )
            )

    for field, value in _config_runtime_urls(config):
        issues.extend(_compose_port_issues(workspace, compose_path, compose_text, field, value))

    seed_command = _config_seed_command(config)
    if isinstance(seed_command, str):
        issues.extend(_seed_service_issues(workspace, compose_path, seed_command, service_names))
        issues.extend(_seed_runtime_command_issues(workspace, compose_path, compose_text, seed_command))

    return issues


def _config_start_command(config: dict) -> str | None:
    if config.get("version") == 2:
        runtime = config.get("runtime")
        if not isinstance(runtime, dict):
            return None
        command = runtime.get("start_command")
        if isinstance(command, str) and command.strip():
            return command
        compose_file = runtime.get("compose_file")
        if isinstance(compose_file, str) and compose_file.strip():
            return f"docker compose -f {shlex.quote(compose_file)} up -d"
        return None
    return config.get("start_command")


def _config_seed_command(config: dict) -> str | None:
    if config.get("version") == 2:
        runtime = config.get("runtime")
        if isinstance(runtime, dict) and isinstance(runtime.get("seed_command"), str):
            return runtime["seed_command"]
    return config.get("seed_command")


def _config_runtime_urls(config: dict) -> list[tuple[str, str]]:
    if config.get("version") == 2:
        readiness = config.get("readiness")
        if not isinstance(readiness, list):
            return []
        urls = []
        for index, item in enumerate(readiness):
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                name = item.get("name") if isinstance(item.get("name"), str) else str(index)
                urls.append((f"readiness[{name}].url", item["url"]))
        return urls

    urls = []
    for field in ("app_url", "api_url"):
        value = config.get(field)
        if isinstance(value, str):
            urls.append((field, value))
    return urls


def _compose_image_issues(workspace: Path, compose_path: Path, compose_text: str) -> list[QualityGateIssue]:
    issues = []
    for line_number, line in enumerate(compose_text.splitlines(), start=1):
        stripped = _strip_yaml_comment(line).strip()
        if not stripped.startswith("image:"):
            continue
        image = _clean_yaml_scalar(stripped.split(":", 1)[1])
        if image not in KNOWN_INVALID_COMPOSE_IMAGES:
            continue
        issues.append(
            QualityGateIssue(
                severity="major",
                code="invalid_compose_image_tag",
                path=_relative_path(workspace, compose_path),
                line=line_number,
                description=(
                    f"Docker Compose references `{image}`, which is not a valid published image tag. "
                    f"{KNOWN_INVALID_COMPOSE_IMAGES[image]} Do not invent Docker image tags."
                ),
            )
        )
    return issues


def _uses_docker_compose(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return len(tokens) >= 2 and tokens[:2] == ["docker", "compose"]


def _compose_path_from_command(workspace: Path, command: str) -> Path | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []

    for index, token in enumerate(tokens):
        if token in {"-f", "--file"} and index + 1 < len(tokens):
            return workspace / tokens[index + 1]
        if token.startswith("--file="):
            return workspace / token.split("=", 1)[1]

    for filename in COMPOSE_FILENAMES:
        path = workspace / filename
        if path.exists():
            return path
    return None


def _compose_env_file_issues(workspace: Path, compose_path: Path, compose_text: str) -> list[QualityGateIssue]:
    issues = []
    compose_dir = compose_path.parent
    for reference in _compose_env_file_references(compose_text):
        env_path = Path(reference)
        if not env_path.is_absolute():
            env_path = compose_dir / env_path

        name = env_path.name
        rel_path = _relative_path(workspace, env_path)
        if env_path.exists():
            if _is_local_secret_env_name(name) and not _matching_env_template_exists(env_path, workspace, compose_dir):
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="missing_env_template",
                        path=rel_path,
                        description=(
                            "Docker Compose uses an ignored local env file. Commit `.env.example`, `.env.sample`, "
                            "or `.env.template` with safe demo values so CI and clean checkouts can bootstrap `.env`."
                        ),
                    )
                )
            continue

        if name in ENV_TEMPLATE_FILENAMES:
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_env_file",
                    path=rel_path,
                    description="Docker Compose references an env template file that does not exist.",
                )
            )
            continue

        if name == ".env":
            if not _matching_env_template_exists(env_path, workspace, compose_dir):
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="missing_env_template",
                        path=rel_path,
                        description=(
                            "Docker Compose references `.env`, but no safe committed env template exists. "
                            "Create `.env.example` with non-secret demo/runtime values; do not commit real `.env`."
                        ),
                    )
                )
            continue

        if name.startswith(".env"):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="unsupported_env_file",
                    path=rel_path,
                    description=(
                        "Docker Compose references a non-standard local env file. Use `env_file: .env` plus "
                        "a committed `.env.example`, or reference `.env.example` directly for demo-only values."
                    ),
                )
            )
            continue

        issues.append(
            QualityGateIssue(
                severity="major",
                code="missing_env_file",
                path=rel_path,
                description="Docker Compose references an env file that is missing from the workspace.",
            )
        )
    return issues


def _compose_env_file_references(compose_text: str) -> list[str]:
    lines = compose_text.splitlines()
    references = []
    for index, line in enumerate(lines):
        stripped = _strip_yaml_comment(line).strip()
        if not stripped.startswith("env_file:"):
            continue

        indent = len(line) - len(line.lstrip())
        raw_value = stripped.split(":", 1)[1].strip()
        if raw_value:
            references.extend(_env_file_values_from_raw(raw_value))
            continue

        for child in lines[index + 1 :]:
            child_stripped = _strip_yaml_comment(child).strip()
            if not child_stripped:
                continue
            child_indent = len(child) - len(child.lstrip())
            if child_indent <= indent:
                break
            if child_stripped.startswith("-"):
                references.extend(_env_file_values_from_raw(child_stripped[1:].strip()))
            elif child_stripped.startswith("path:"):
                references.extend(_env_file_values_from_raw(child_stripped))

    return [reference for reference in references if reference]


def _env_file_values_from_raw(raw_value: str) -> list[str]:
    value = _clean_yaml_scalar(raw_value)
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        return [_clean_yaml_scalar(part) for part in value[1:-1].split(",")]
    if value.startswith("{") and value.endswith("}"):
        match = re.search(r"path\s*:\s*([^,}]+)", value)
        return [_clean_yaml_scalar(match.group(1))] if match else []
    if value.startswith("path:"):
        return [_clean_yaml_scalar(value.split(":", 1)[1])]
    return [value]


def _clean_yaml_scalar(value: str) -> str:
    return value.strip().strip("'\"")


def _is_local_secret_env_name(name: str) -> bool:
    return name.startswith(".env") and name not in ENV_TEMPLATE_FILENAMES


def _matching_env_template_exists(env_path: Path, workspace: Path, compose_dir: Path) -> bool:
    candidates = [env_path.with_name(template_name) for template_name in ENV_TEMPLATE_FILENAMES]
    candidates.extend(workspace / template_name for template_name in ENV_TEMPLATE_FILENAMES)
    candidates.extend(compose_dir / template_name for template_name in ENV_TEMPLATE_FILENAMES)
    return any(path.exists() for path in candidates)


def _external_api_secret_issues(workspace: Path) -> list[QualityGateIssue]:
    env_entries = list(_env_template_entries(workspace))
    if not env_entries:
        return []

    placeholder_secrets = [
        entry
        for entry in env_entries
        if _is_external_secret_env_name(entry[1]) and _is_placeholder_secret_value(entry[2])
    ]
    if not placeholder_secrets:
        return []

    source_hints = _external_api_source_hints(workspace)
    env_vendor_hints = {vendor for _, name, _ in placeholder_secrets for vendor in _external_vendors_in_text(name)}
    generic_external_secret = any(_is_generic_external_secret_name(name) for _, name, _ in placeholder_secrets)
    if not source_hints and not env_vendor_hints and not generic_external_secret:
        return []

    if _has_mock_provider_default(env_entries):
        return []

    first_path, _, _ = placeholder_secrets[0]
    secret_names = ", ".join(sorted({name for _, name, _ in placeholder_secrets})[:4])
    hint_names = ", ".join(sorted(source_hints | env_vendor_hints)) or "external API"
    return [
        QualityGateIssue(
            severity="major",
            code="external_api_requires_mock",
            path=_relative_path(workspace, first_path),
            description=(
                f"External integration detected ({hint_names}) with placeholder secret env values ({secret_names}), "
                "but `.env.example` does not default to a mock/demo/local provider. Docker CI runs without real "
                "secrets, so the generated MVP must provide a deterministic mock/demo provider for the primary "
                "smoke path and only call the real vendor when explicitly configured with a real key."
            ),
        )
    ]


def _env_template_entries(workspace: Path) -> Iterable[tuple[Path, str, str]]:
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.name not in ENV_TEMPLATE_FILENAMES:
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(workspace).parts):
            continue
        for name, value in _parse_env_template(_safe_read(path)):
            yield path, name, value


def _parse_env_template(text: str) -> list[tuple[str, str]]:
    entries = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        name, value = stripped.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        entries.append((name, _clean_env_value(value)))
    return entries


def _clean_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return value


def _is_external_secret_env_name(name: str) -> bool:
    upper = name.upper()
    if upper in INTERNAL_SECRET_ENV_NAMES:
        return False
    if any(part in upper for part in INTERNAL_SECRET_NAME_PARTS):
        return False
    if not upper.endswith(EXTERNAL_SECRET_ENV_SUFFIXES):
        return False
    if _external_vendors_in_text(name):
        return True
    return _is_generic_external_secret_name(name)


def _is_generic_external_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in ("EXTERNAL", "THIRD_PARTY", "VENDOR", "INTEGRATION"))


def _is_placeholder_secret_value(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if normalized in PLACEHOLDER_SECRET_VALUES:
        return True
    return any(
        marker in normalized
        for marker in ("changeme", "placeholder", "replace", "your_", "dummy", "fake", "example", "sk-test")
    )


def _has_mock_provider_default(env_entries: list[tuple[Path, str, str]]) -> bool:
    for _, name, value in env_entries:
        upper = name.upper()
        normalized = value.strip().strip("'\"").lower()
        if _is_provider_mode_name(name) and _is_mock_provider_value(normalized):
            return True
        if upper.startswith(("USE_MOCK", "ENABLE_MOCK", "MOCK_")) and normalized in {"1", "true", "yes", "enabled"}:
            return True
        if upper.startswith(("USE_REAL", "ENABLE_REAL")) and normalized in {"0", "false", "no", "disabled"}:
            return True
    return False


def _is_provider_mode_name(name: str) -> bool:
    upper = name.upper()
    if bool(PROVIDER_MODE_NAME_RE.search(upper)) or any(
        marker in upper for marker in ("PROVIDER_", "_PROVIDER_", "MODE_", "_MODE_", "MOCK_PROVIDER")
    ):
        return True
    return upper.endswith("BACKEND") and any(
        marker in upper for marker in ("AI", "IMAGE", "MEDIA", "PAYMENT", "EXTERNAL", "GENERATION")
    )


def _is_mock_provider_value(value: str) -> bool:
    return value in MOCK_PROVIDER_VALUES or value.startswith("mock") or value.endswith("_mock")


def _external_api_source_hints(workspace: Path) -> set[str]:
    hints = set()
    for path in _iter_own_code_files(workspace):
        if path.name in ENV_TEMPLATE_FILENAMES:
            continue
        text = _safe_read(path).lower()
        hints.update(_external_vendors_in_text(text))
        if "http" in text and any(marker in text for marker in ("api key", "api_key", "authorization", "bearer")):
            hints.add("external HTTP API")
    return hints


def _external_vendors_in_text(text: str) -> set[str]:
    lower = text.lower()
    return {vendor for vendor, markers in EXTERNAL_VENDOR_HINTS.items() if any(marker in lower for marker in markers)}


def _compose_build_specs(compose_text: str, compose_dir: Path) -> list[tuple[Path, Path]]:
    lines = compose_text.splitlines()
    specs = []
    for index, line in enumerate(lines):
        stripped = _strip_yaml_comment(line).strip()
        if not stripped.startswith("build:"):
            continue

        indent = len(line) - len(line.lstrip())
        raw_value = stripped.split(":", 1)[1].strip().strip("'\"")
        context = raw_value if raw_value and not raw_value.startswith("{") else "."
        dockerfile = "Dockerfile"

        if not raw_value or raw_value.startswith("{"):
            for child in lines[index + 1 :]:
                child_stripped = _strip_yaml_comment(child).strip()
                if not child_stripped:
                    continue
                child_indent = len(child) - len(child.lstrip())
                if child_indent <= indent:
                    break
                if child_stripped.startswith("context:"):
                    context = child_stripped.split(":", 1)[1].strip().strip("'\"") or "."
                elif child_stripped.startswith("dockerfile:"):
                    dockerfile = child_stripped.split(":", 1)[1].strip().strip("'\"") or "Dockerfile"

        context_path = Path(context)
        if not context_path.is_absolute():
            context_path = compose_dir / context_path
        dockerfile_path = Path(dockerfile)
        if not dockerfile_path.is_absolute():
            dockerfile_path = context_path / dockerfile_path
        specs.append((context_path, dockerfile_path))

    return specs


def _docker_build_issues(workspace: Path, build_specs: list[tuple[Path, Path]]) -> list[QualityGateIssue]:
    issues = []
    for context_path, dockerfile_path in build_specs:
        if not context_path.exists():
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_docker_build_context",
                    path=_relative_path(workspace, context_path),
                    description="Docker Compose build context does not exist.",
                )
            )
            continue
        if not dockerfile_path.exists():
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_dockerfile",
                    path=_relative_path(workspace, dockerfile_path),
                    description="Docker Compose build context has no referenced Dockerfile.",
                )
            )
    return issues


def _docker_prisma_runtime_issues(workspace: Path, build_specs: list[tuple[Path, Path]]) -> list[QualityGateIssue]:
    issues = []
    for context_path, dockerfile_path in build_specs:
        if not context_path.exists() or not dockerfile_path.exists():
            continue
        package_data = _read_json(context_path / "package.json")
        if not _package_uses_prisma(package_data):
            continue
        dockerfile_text = _safe_read(dockerfile_path)
        if not _dockerfile_uses_alpine(dockerfile_text):
            continue
        issues.append(
            QualityGateIssue(
                severity="major",
                code="prisma_alpine_runtime_risk",
                path=_relative_path(workspace, dockerfile_path),
                description=(
                    "Prisma-backed Node services must not use Alpine base images in the generated MVP Docker runtime. "
                    "Use a Debian slim Node image with OpenSSL installed so `prisma generate`, `prisma db push`, "
                    "and runtime schema engines work reliably in CI and clean Docker environments."
                ),
            )
        )
    return issues


def _prisma_migration_sql_issues(workspace: Path, build_specs: list[tuple[Path, Path]]) -> list[QualityGateIssue]:
    issues = []
    seen_paths = set()
    for context_path, _ in build_specs:
        migrations_dir = context_path / "prisma" / "migrations"
        if not migrations_dir.exists():
            continue
        for migration_path in sorted(migrations_dir.glob("*/migration.sql")):
            if migration_path in seen_paths:
                continue
            seen_paths.add(migration_path)
            for line_number, line in enumerate(_safe_read(migration_path).splitlines(), start=1):
                if not re.match(r"^\s*#", line):
                    continue
                issues.append(
                    QualityGateIssue(
                        severity="major",
                        code="invalid_prisma_migration_sql",
                        path=_relative_path(workspace, migration_path),
                        line=line_number,
                        description=(
                            "Prisma migration.sql must contain valid PostgreSQL SQL. Lines starting with `#` "
                            "are not SQL comments and make `prisma migrate deploy` fail with P3018; use `--` "
                            "comments or remove the line."
                        ),
                    )
                )
                break
    return issues


def _package_uses_prisma(package_data: dict) -> bool:
    dependencies = {
        **package_data.get("dependencies", {}),
        **package_data.get("devDependencies", {}),
        **package_data.get("optionalDependencies", {}),
    }
    return "prisma" in dependencies or "@prisma/client" in dependencies


def _dockerfile_uses_alpine(dockerfile_text: str) -> bool:
    return re.search(r"(?im)^\s*FROM\s+\S*alpine(?:\s|$)", dockerfile_text) is not None


def _has_dockerignore(workspace: Path, build_specs: list[tuple[Path, Path]]) -> bool:
    if (workspace / ".dockerignore").exists():
        return True
    return any((context_path / ".dockerignore").exists() for context_path, _ in build_specs)


def _compose_port_issues(
    workspace: Path,
    compose_path: Path,
    compose_text: str,
    field: str,
    url: str,
) -> list[QualityGateIssue]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.port is None:
        return []

    port = str(parsed.port)
    exposes_port = re.search(rf"['\"]?\s*{re.escape(port)}\s*:", compose_text) is not None
    if exposes_port:
        return []

    return [
        QualityGateIssue(
            severity="major",
            code="compose_port_mismatch",
            path=_relative_path(workspace, compose_path),
            description=f"`mvp.config.json.{field}` uses localhost:{port}, but Docker Compose does not publish that host port.",
        )
    ]


def _seed_service_issues(
    workspace: Path,
    compose_path: Path,
    seed_command: str,
    service_names: set[str],
) -> list[QualityGateIssue]:
    service = _docker_compose_exec_service(seed_command)
    if not service or service in service_names:
        return []
    return [
        QualityGateIssue(
            severity="major",
            code="invalid_seed_service",
            path=_relative_path(workspace, compose_path),
            description=f"`seed_command` references Docker Compose service `{service}`, but that service is not declared.",
        )
    ]


def _seed_runtime_command_issues(
    workspace: Path,
    compose_path: Path,
    compose_text: str,
    seed_command: str,
) -> list[QualityGateIssue]:
    if not _uses_docker_compose(seed_command):
        return []

    issues = []
    if _uses_typescript_seed_runtime(seed_command):
        issues.append(
            _typescript_seed_issue(
                workspace,
                compose_path,
                "Docker seed commands run inside the generated production runtime. "
                "`seed_command` must not depend on TypeScript runtime tooling such as `ts-node`, `tsx`, "
                "or `ts-node/register`, which is commonly absent from production images. Compile the seed "
                "to JavaScript or provide a runtime-safe JS seed and run it with `node`.",
            )
        )

    issues.extend(_prisma_seed_schema_issues(workspace, compose_path, compose_text, seed_command))

    service = _docker_compose_exec_service(seed_command)
    npm_script = _npm_run_script_name(seed_command)
    if not service or not npm_script:
        return issues

    context_path = _compose_build_context_for_service(compose_text, compose_path.parent, service)
    if context_path is None:
        return issues

    package_data = _read_json(context_path / "package.json")
    script = package_data.get("scripts", {}).get(npm_script)
    if not isinstance(script, str) or not _uses_typescript_seed_runtime(script):
        return issues

    issues.append(
        _typescript_seed_issue(
            workspace,
            compose_path,
            f"`seed_command` runs `npm run {npm_script}` in Docker Compose service `{service}`, "
            f"but that package script uses TypeScript runtime tooling: `{script}`. Production Docker "
            "seed paths must be executable without devDependencies; use a compiled/runtime JS seed command.",
        )
    )
    return issues


def _prisma_seed_schema_issues(
    workspace: Path,
    compose_path: Path,
    compose_text: str,
    seed_command: str,
) -> list[QualityGateIssue]:
    service = _docker_compose_exec_service(seed_command)
    if not service:
        return []

    context_path = _compose_build_context_for_service(compose_text, compose_path.parent, service)
    if context_path is None:
        return []

    package_data = _read_json(context_path / "package.json")
    schema_path = context_path / "prisma" / "schema.prisma"
    if not _package_uses_prisma(package_data) and not schema_path.exists():
        return []
    if schema_path.exists() and not _prisma_schema_declares_model(schema_path):
        return []

    command_text = _prisma_runtime_command_texts(context_path, compose_text, service, seed_command, package_data)
    has_db_push = _uses_prisma_db_push(command_text)
    has_migrate_deploy = _uses_prisma_migrate_deploy(command_text)
    has_migrations = _has_prisma_migrations(context_path)

    if has_db_push or (has_migrate_deploy and has_migrations):
        return []

    if has_migrate_deploy and not has_migrations:
        description = (
            "Prisma runtime invokes `prisma migrate deploy`, but no committed `prisma/migrations/*/migration.sql` "
            "files exist in the Docker build context. On a clean CI Postgres database this applies nothing, so "
            "seed scripts can fail with missing tables such as `public.User`. Commit an initial Prisma migration "
            "or run `prisma db push` before `node prisma/seed.js` in the Docker seed/init path."
        )
        code = "missing_prisma_migrations"
    else:
        description = (
            "Prisma-backed seed command has no guaranteed schema initialization in the Docker runtime. Clean CI "
            "databases start empty, so run `prisma migrate deploy` with committed migrations or `prisma db push` "
            "before executing the seed script."
        )
        code = "missing_prisma_schema_init"

    return [
        QualityGateIssue(
            severity="major",
            code=code,
            path=_relative_path(workspace, schema_path if schema_path.exists() else context_path),
            description=description,
        )
    ]


def _typescript_seed_issue(workspace: Path, compose_path: Path, description: str) -> QualityGateIssue:
    return QualityGateIssue(
        severity="major",
        code="typescript_seed_runtime_dependency",
        path=_relative_path(workspace, compose_path),
        description=description,
    )


def _uses_typescript_seed_runtime(command: str) -> bool:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    joined = " ".join(tokens).lower()
    if re.search(r"(^|\s)(?:npx\s+)?(?:ts-node|ts-node-esm|tsx)(\s|$)", joined):
        return True
    if "ts-node/register" in joined or "ts-node/esm" in joined:
        return True
    return bool(re.search(r"(^|\s)node\s+--loader\s+ts-node(?:/esm)?(\s|$)", joined))


def _prisma_schema_declares_model(schema_path: Path) -> bool:
    return bool(re.search(r"^\s*model\s+\w+\s*{", _safe_read(schema_path), flags=re.MULTILINE))


def _has_prisma_migrations(context_path: Path) -> bool:
    migrations_dir = context_path / "prisma" / "migrations"
    return migrations_dir.exists() and any(migrations_dir.glob("*/migration.sql"))


def _uses_prisma_db_push(text: str) -> bool:
    return bool(re.search(r"\bprisma\s+db\s+push\b", text, flags=re.IGNORECASE))


def _uses_prisma_migrate_deploy(text: str) -> bool:
    return bool(re.search(r"\bprisma\s+migrate\s+deploy\b", text, flags=re.IGNORECASE))


def _prisma_runtime_command_texts(
    context_path: Path,
    compose_text: str,
    service: str,
    seed_command: str,
    package_data: dict,
) -> str:
    texts = [seed_command, _compose_service_block_text(compose_text, service)]

    for dockerfile in _candidate_dockerfiles(context_path):
        texts.append(_safe_read(dockerfile))

    scripts = package_data.get("scripts", {})
    if isinstance(scripts, dict):
        for script_name in ("start", "start:prod", "start:docker", "docker:start"):
            script = scripts.get(script_name)
            if isinstance(script, str):
                texts.append(script)

    npm_script = _npm_run_script_name(seed_command)
    if npm_script and isinstance(scripts, dict):
        script = scripts.get(npm_script)
        if isinstance(script, str):
            texts.append(script)

    return "\n".join(texts)


def _candidate_dockerfiles(context_path: Path) -> list[Path]:
    if not context_path.exists():
        return []
    candidates = [context_path / "Dockerfile"]
    candidates.extend(path for path in context_path.glob("Dockerfile.*") if path.is_file())
    return [path for path in candidates if path.exists()]


def _compose_service_block_text(compose_text: str, service: str) -> str:
    lines = compose_text.splitlines()
    block = []
    in_services = False
    in_target_service = False
    service_indent: int | None = None

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            if in_target_service:
                block.append(line)
            continue

        indent = len(line) - len(line.lstrip())
        stripped = _strip_yaml_comment(line).strip()
        if indent == 0:
            in_services = stripped == "services:"
            in_target_service = False
            service_indent = None
            continue

        if in_services and indent == 2 and stripped.endswith(":"):
            service_name = stripped[:-1].strip("'\"")
            in_target_service = service_name == service
            service_indent = indent if in_target_service else None
            if in_target_service:
                block.append(line)
            continue

        if not in_target_service or service_indent is None:
            continue
        if indent <= service_indent:
            break
        block.append(line)

    return "\n".join(block)


def _npm_run_script_name(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None

    if "exec" not in tokens:
        return None
    index = tokens.index("exec") + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 1
    if index < len(tokens):
        index += 1

    while index < len(tokens) and "=" in tokens[index] and not tokens[index].startswith("-"):
        index += 1

    if index + 2 < len(tokens) and tokens[index : index + 2] == ["npm", "run"]:
        return tokens[index + 2]
    if index + 1 < len(tokens) and tokens[index] == "npm" and tokens[index + 1].startswith("run-script"):
        return tokens[index + 2] if index + 2 < len(tokens) else None
    return None


def _compose_build_context_for_service(compose_text: str, compose_dir: Path, service: str) -> Path | None:
    lines = compose_text.splitlines()
    in_services = False
    in_target_service = False
    service_indent: int | None = None

    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        stripped = _strip_yaml_comment(line).strip()
        if indent == 0:
            in_services = stripped == "services:"
            in_target_service = False
            service_indent = None
            continue

        if in_services and indent == 2 and stripped.endswith(":"):
            service_name = stripped[:-1].strip("'\"")
            in_target_service = service_name == service
            service_indent = indent if in_target_service else None
            continue

        if not in_target_service or service_indent is None:
            continue
        if indent <= service_indent:
            in_target_service = False
            service_indent = None
            continue
        if not stripped.startswith("build:"):
            continue

        raw_value = stripped.split(":", 1)[1].strip().strip("'\"")
        context = raw_value if raw_value and not raw_value.startswith("{") else "."
        if not raw_value or raw_value.startswith("{"):
            build_indent = indent
            for child in lines[index + 1 :]:
                child_stripped = _strip_yaml_comment(child).strip()
                if not child_stripped:
                    continue
                child_indent = len(child) - len(child.lstrip())
                if child_indent <= build_indent:
                    break
                if child_stripped.startswith("context:"):
                    context = child_stripped.split(":", 1)[1].strip().strip("'\"") or "."
                    break

        context_path = Path(context)
        if not context_path.is_absolute():
            context_path = compose_dir / context_path
        return context_path

    return None


def _docker_compose_exec_service(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if len(tokens) < 4 or tokens[:2] != ["docker", "compose"] or "exec" not in tokens:
        return None

    index = tokens.index("exec") + 1
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 1
    return tokens[index] if index < len(tokens) else None


def _compose_service_names(compose_text: str) -> set[str]:
    service_names = set()
    in_services = False
    for line in compose_text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        stripped = _strip_yaml_comment(line).strip()
        if indent == 0:
            in_services = stripped == "services:"
            continue
        if in_services and indent == 2 and stripped.endswith(":"):
            service_names.add(stripped[:-1].strip("'\""))
    return service_names


def _strip_yaml_comment(line: str) -> str:
    return line.split("#", 1)[0]


def untrack_forbidden_artifacts(workspace: Path) -> list[str]:
    tracked = _tracked_paths(workspace)
    roots = sorted({root for path in tracked if (root := _forbidden_git_root(path)) is not None})
    if not roots:
        return []

    try:
        repo = git.Repo(workspace)
        repo.git.rm("--cached", "-r", "--ignore-unmatch", *roots)
    except git.exc.GitCommandError as exc:
        logger.warning("quality_gate.untrack_failed", workspace=str(workspace), error=str(exc))
        return []

    logger.info("quality_gate.untracked_artifacts", workspace=str(workspace), count=len(roots))
    return roots


def _tracked_artifact_issues(workspace: Path) -> list[QualityGateIssue]:
    tracked = _tracked_paths(workspace)
    issues = []
    for path in tracked:
        if _is_forbidden_git_path(path):
            issues.append(
                QualityGateIssue(
                    severity="critical",
                    code="forbidden_tracked_artifact",
                    path=path,
                    description="Dependency, build, environment, cache, or generated artifact is tracked by git.",
                )
            )
    return issues


def _managed_ci_issues(workspace: Path) -> list[QualityGateIssue]:
    ci_path = workspace / ".github" / "workflows" / "ci.yml"
    if not ci_path.exists():
        return []

    try:
        actual = ci_path.read_text()
    except OSError:
        return []

    expected = _render_managed_ci_yaml()
    if actual == expected:
        return []

    try:
        ci_path.write_text(expected)
    except OSError as exc:
        return [
            QualityGateIssue(
                severity="critical",
                code="managed_ci_restore_failed",
                path=".github/workflows/ci.yml",
                description=f"Generated MVP CI workflow is managed by the orchestrator, but could not be restored: {exc}",
            )
        ]

    logger.info("quality_gate.managed_ci_restored", path=str(ci_path))
    return []


def _render_managed_ci_yaml(python_version: str = "3.11", node_version: str = "20") -> str:
    template = _jinja_env.get_template("ci.yml.j2")
    return template.render(python_version=python_version, node_version=node_version)


def _source_quality_issues(workspace: Path) -> list[QualityGateIssue]:
    issues = []
    for path in _iter_source_files(workspace):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue

        rel = _relative_path(workspace, path)
        issues.extend(_placeholder_issues(rel, text))
        issues.extend(_empty_handler_issues(rel, text))
        issues.extend(_state_only_ok_issues(rel, text))
        issues.extend(_unwired_button_issues(rel, text))
        issues.extend(_fake_success_issues(rel, text))
    return issues


def _frontend_styling_issues(workspace: Path) -> list[QualityGateIssue]:
    issues = []
    for package_json in workspace.rglob("package.json"):
        if any(part in IGNORED_DIRS for part in package_json.relative_to(workspace).parts):
            continue

        package_dir = package_json.parent
        package_data = _read_json(package_json)
        if not _looks_like_frontend_package(package_data):
            continue

        rel_package = _relative_path(workspace, package_dir)
        css_files = _package_css_files(package_dir)
        source_files = list(_package_source_files(package_dir))
        source_text = "\n".join(_safe_read(path) for path in source_files)

        if TAILWIND_UTILITY_RE.search(source_text) and not _entry_imports_css(package_dir):
            issues.append(
                QualityGateIssue(
                    severity="major",
                    code="missing_css_import",
                    path=rel_package,
                    description=(
                        "Frontend uses utility CSS classes but the app entrypoint does not import a CSS file. "
                        "The browser will render mostly default HTML."
                    ),
                )
            )

        tailwind_version = _dependency_version(package_data, "tailwindcss")
        if _is_tailwind_v4(tailwind_version):
            for css_file in css_files:
                css_text = _safe_read(css_file)
                if (
                    "@tailwind base" in css_text
                    or "@tailwind components" in css_text
                    or "@tailwind utilities" in css_text
                ):
                    issues.append(
                        QualityGateIssue(
                            severity="major",
                            code="broken_tailwind_v4_setup",
                            path=_relative_path(workspace, css_file),
                            description=(
                                'Tailwind v4 project uses legacy @tailwind directives. Use `@import "tailwindcss";` '
                                "or pin/configure Tailwind v3 so spacing, color, and component classes compile."
                            ),
                        )
                    )

        issues.extend(_compiled_css_issues(workspace, package_dir, source_text))

    return issues


def _compiled_css_issues(workspace: Path, package_dir: Path, source_text: str) -> list[QualityGateIssue]:
    dist_dir = package_dir / "dist"
    if not dist_dir.exists() or not TAILWIND_UTILITY_RE.search(source_text):
        return []

    compiled_css = "\n".join(_safe_read(path) for path in dist_dir.rglob("*.css"))
    if not compiled_css:
        return []

    required_utilities = ["bg-", "text-", "p-", "px-", "py-", "rounded", "shadow"]
    missing = [utility for utility in required_utilities if utility in source_text and utility not in compiled_css]
    if len(missing) < 3:
        return []

    return [
        QualityGateIssue(
            severity="major",
            code="compiled_css_missing_utilities",
            path=_relative_path(workspace, dist_dir),
            description=(
                "Built CSS is missing multiple utility classes used by the frontend. "
                "This usually means Tailwind/PostCSS is misconfigured and the UI will look unstyled."
            ),
        )
    ]


def _placeholder_issues(path: str, text: str) -> list[QualityGateIssue]:
    issues = []
    for match in PLACEHOLDER_RE.finditer(text):
        issues.append(
            QualityGateIssue(
                severity="major",
                code="placeholder_code",
                path=path,
                line=_line_number(text, match.start()),
                description="Visible or source-level placeholder/TODO text is not acceptable in a runnable MVP.",
            )
        )
    return issues


def _empty_handler_issues(path: str, text: str) -> list[QualityGateIssue]:
    return [
        QualityGateIssue(
            severity="major",
            code="empty_ui_handler",
            path=path,
            line=_line_number(text, match.start()),
            description="A visible UI event handler is empty. Implement the action end-to-end or remove the control.",
        )
        for match in EMPTY_HANDLER_RE.finditer(text)
    ]


def _state_only_ok_issues(path: str, text: str) -> list[QualityGateIssue]:
    return [
        QualityGateIssue(
            severity="major",
            code="state_only_modal_ok",
            path=path,
            line=_line_number(text, match.start()),
            description="Modal OK handler only closes local UI state. Persist the form data or remove the modal action.",
        )
        for match in STATE_ONLY_OK_RE.finditer(text)
    ]


def _unwired_button_issues(path: str, text: str) -> list[QualityGateIssue]:
    issues = []
    for match in BUTTON_RE.finditer(text):
        attrs = match.group("attrs") or ""
        body = _visible_text(match.group("body"))
        if not _looks_mutating_button(body):
            continue
        if "onClick" in attrs or "htmlType" in attrs:
            continue
        issues.append(
            QualityGateIssue(
                severity="major",
                code="unwired_mutating_button",
                path=path,
                line=_line_number(text, match.start()),
                description=f"Mutating button '{body.strip()}' has no click or submit wiring.",
            )
        )
    return issues


def _fake_success_issues(path: str, text: str) -> list[QualityGateIssue]:
    lines = text.splitlines()
    issues = []
    for index, line in enumerate(lines):
        if not SUCCESS_RE.search(line):
            continue
        start = max(0, index - 10)
        end = min(len(lines), index + 6)
        context = "\n".join(lines[start:end])
        if REAL_MUTATION_RE.search(context):
            continue
        issues.append(
            QualityGateIssue(
                severity="major",
                code="fake_success_action",
                path=path,
                line=index + 1,
                description="Success notification appears without a nearby API call or mutation.",
            )
        )
    return issues


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _looks_like_frontend_package(package_data: dict) -> bool:
    dependencies = {
        **package_data.get("dependencies", {}),
        **package_data.get("devDependencies", {}),
    }
    return any(name in dependencies for name in FRONTEND_PACKAGE_HINTS)


def _dependency_version(package_data: dict, name: str) -> str | None:
    dependencies = {
        **package_data.get("dependencies", {}),
        **package_data.get("devDependencies", {}),
    }
    value = dependencies.get(name)
    return value if isinstance(value, str) else None


def _is_tailwind_v4(version: str | None) -> bool:
    if not version:
        return False
    normalized = version.strip().lstrip("^~>=< ")
    return normalized.startswith("4.")


def _package_css_files(package_dir: Path) -> list[Path]:
    return [
        path
        for path in package_dir.rglob("*.css")
        if path.is_file() and not any(part in IGNORED_DIRS for part in path.relative_to(package_dir).parts)
    ]


def _package_source_files(package_dir: Path) -> Iterable[Path]:
    for path in package_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.relative_to(package_dir).parts):
            continue
        if path.suffix.lower() in SOURCE_SUFFIXES:
            yield path


def _entry_imports_css(package_dir: Path) -> bool:
    entry_names = {
        "main.tsx",
        "main.jsx",
        "main.ts",
        "main.js",
        "index.tsx",
        "index.jsx",
        "index.ts",
        "index.js",
        "App.tsx",
        "App.jsx",
        "app.tsx",
        "app.jsx",
    }
    import_re = re.compile(r"import\s+['\"][^'\"]+\.css['\"]")
    for source in _package_source_files(package_dir):
        if source.name not in entry_names:
            continue
        if import_re.search(_safe_read(source)):
            return True
    return False


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def _own_code_stats(workspace: Path) -> tuple[int, int]:
    files = 0
    lines = 0
    for path in _iter_own_code_files(workspace):
        files += 1
        try:
            lines += len(path.read_text(errors="ignore").splitlines())
        except OSError:
            continue
    return files, lines


def _iter_source_files(workspace: Path) -> Iterable[Path]:
    for path in _iter_own_code_files(workspace):
        if path.suffix.lower() in SOURCE_SUFFIXES:
            yield path


def _iter_own_code_files(workspace: Path) -> Iterable[Path]:
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(workspace).parts
        if any(part in IGNORED_DIRS for part in relative_parts):
            continue
        if path.name in LOCK_AND_GENERATED_FILES:
            continue
        if path.suffix.lower() not in STATS_SUFFIXES and path.name not in STATS_FILENAMES:
            continue
        yield path


def _tracked_paths(workspace: Path) -> list[str]:
    try:
        repo = git.Repo(workspace)
    except git.exc.InvalidGitRepositoryError:
        return []

    try:
        raw = repo.git.ls_files("-z")
    except git.exc.GitCommandError:
        return []
    if not isinstance(raw, str):
        return []
    return [item for item in raw.split("\0") if item]


def _is_forbidden_git_path(path: str) -> bool:
    if _forbidden_git_root(path) is not None:
        return True
    return False


def _forbidden_git_root(path: str) -> str | None:
    parts = Path(path).parts
    for artifact_dir in ("node_modules", "dist", "build", "coverage", ".cache", ".next", ".nuxt", ".turbo", ".vite"):
        if artifact_dir in parts:
            index = parts.index(artifact_dir)
            return "/".join(parts[: index + 1])

    name = Path(path).name
    if name.startswith(".env") and name not in {".env.example", ".env.sample", ".env.template"}:
        return path
    if name in {".DS_Store"} or name.endswith(".log"):
        return path
    return None


def _looks_mutating_button(text: str) -> bool:
    normalized = text.strip().lower()
    return bool(normalized) and any(label in normalized for label in MUTATING_BUTTON_LABELS)


def _visible_text(markup: str) -> str:
    without_tags = TAG_RE.sub(" ", markup)
    return " ".join(without_tags.split())


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _relative_path(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return str(path)


def _format_output(issues: list[QualityGateIssue], own_code_files: int, own_code_lines: int) -> str:
    lines = [
        "MVP quality gate",
        f"own_code_files={own_code_files}",
        f"own_code_lines={own_code_lines}",
    ]
    if not issues:
        lines.append("passed=true")
        return "\n".join(lines)

    lines.append("passed=false")
    for issue in issues:
        location = (
            f" {issue.path}:{issue.line}" if issue.path and issue.line else f" {issue.path}" if issue.path else ""
        )
        lines.append(f"- [{issue.severity}] {issue.code}{location}: {issue.description}")
    return "\n".join(lines)
