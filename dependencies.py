from functools import lru_cache

from services.db_service import DatabaseService
from app.config import settings
from pipeline.runner import PipelineRunner
from services.coding_service import CodingService
from services.git_service import GitService
from services.openclaw_client import OpenClawClient
from services.opencode_client import OpenCodeClient
from services.plan_service import PlanService
from services.quality_gate_service import QualityGateService
from services.review_service import ReviewService
from services.spec_service import SpecService
from services.smoke_test_service import SmokeTestService
from services.telegram_service import TelegramService
from services.test_service import TestService


@lru_cache(maxsize=1)
def get_db_service() -> DatabaseService:
    return DatabaseService(settings.DATABASE_URL)


@lru_cache(maxsize=1)
def get_openclaw_client() -> OpenClawClient:
    return OpenClawClient(
        base_url=settings.OPENCLAW_API_URL,
        api_key=settings.OPENCLAW_API_KEY,
        model=settings.OPENCLAW_MODEL,
        timeout=settings.OPENCLAW_TIMEOUT,
    )


@lru_cache(maxsize=1)
def get_opencode_client() -> OpenCodeClient:
    return OpenCodeClient(
        base_url=settings.OPENCODE_API_URL,
        password=settings.OPENCODE_SERVER_PASSWORD.get_secret_value(),
        timeout=settings.OPENCODE_TIMEOUT,
    )


@lru_cache(maxsize=1)
def get_spec_service() -> SpecService:
    return SpecService(get_openclaw_client())


@lru_cache(maxsize=1)
def get_plan_service() -> PlanService:
    return PlanService(get_openclaw_client())


@lru_cache(maxsize=1)
def get_review_service() -> ReviewService:
    return ReviewService(get_openclaw_client())


@lru_cache(maxsize=1)
def get_coding_service() -> CodingService:
    return CodingService(
        get_opencode_client(),
        timeout=settings.OPENCODE_TIMEOUT,
        busy_stable_seconds=settings.OPENCODE_BUSY_STABLE_SECONDS,
        stuck_seconds=settings.OPENCODE_STUCK_TIMEOUT_SECONDS,
        empty_diff_followup_min_stable_seconds=settings.OPENCODE_EMPTY_DIFF_FOLLOWUP_MIN_STABLE_SECONDS,
        max_empty_diff_followups=settings.OPENCODE_MAX_EMPTY_DIFF_FOLLOWUPS,
    )


@lru_cache(maxsize=1)
def get_test_service() -> TestService:
    return TestService(
        github_token=settings.GITHUB_TOKEN.get_secret_value(),
        polling_timeout=settings.CI_POLLING_TIMEOUT,
        polling_grace_timeout=settings.CI_POLLING_GRACE_TIMEOUT,
        polling_interval=settings.CI_POLLING_INTERVAL,
        workspace_root=settings.WORKSPACE_ROOT,
        container_workspace_root=settings.OPENCODE_WORKSPACE_ROOT,
        node_test_container_service=settings.NODE_TEST_CONTAINER_SERVICE,
        docker_compose_project_dir=settings.NODE_TEST_DOCKER_COMPOSE_DIR,
    )


@lru_cache(maxsize=1)
def get_git_service() -> GitService:
    return GitService(
        github_token=settings.GITHUB_TOKEN.get_secret_value(),
        repo_owner=settings.GITHUB_REPO_OWNER,
        repo_name=settings.GITHUB_REPO_NAME,
        repo_private=settings.GITHUB_REPO_PRIVATE,
    )


@lru_cache(maxsize=1)
def get_quality_gate_service() -> QualityGateService:
    return QualityGateService()


@lru_cache(maxsize=1)
def get_smoke_test_service() -> SmokeTestService | None:
    if not settings.SMOKE_TEST_ENABLED:
        return None
    return SmokeTestService(
        browser_timeout_ms=settings.SMOKE_TEST_BROWSER_TIMEOUT_MS,
        command_timeout=settings.SMOKE_TEST_COMMAND_TIMEOUT,
        headless=settings.SMOKE_TEST_HEADLESS,
    )


@lru_cache(maxsize=1)
def get_telegram_service() -> TelegramService:
    return TelegramService(
        bot_token=settings.TELEGRAM_BOT_TOKEN.get_secret_value(),
        approval_chat_id=settings.TELEGRAM_APPROVAL_CHAT_ID,
        escalation_chat_id=settings.TELEGRAM_ESCALATION_CHAT_ID,
        notification_chat_id=settings.TELEGRAM_NOTIFICATION_CHAT_ID,
    )


@lru_cache(maxsize=1)
def get_pipeline_runner() -> PipelineRunner:
    return PipelineRunner(
        db=get_db_service(),
        spec_service=get_spec_service(),
        plan_service=get_plan_service(),
        coding_service=get_coding_service(),
        test_service=get_test_service(),
        git_service=get_git_service(),
        review_service=get_review_service(),
        telegram_service=get_telegram_service(),
        quality_gate_service=get_quality_gate_service(),
        smoke_test_service=get_smoke_test_service(),
        max_review_retries=settings.MAX_REVIEW_RETRIES,
        max_test_retries=settings.MAX_TEST_RETRIES,
        max_local_same_failure_retries=settings.MAX_LOCAL_SAME_FAILURE_RETRIES,
        max_ci_same_failure_retries=settings.MAX_CI_SAME_FAILURE_RETRIES,
        max_opencode_stuck_retries=settings.MAX_OPENCODE_STUCK_RETRIES,
        workspace_root=settings.WORKSPACE_ROOT,
        opencode_workspace_root=settings.OPENCODE_WORKSPACE_ROOT,
    )
