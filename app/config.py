from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "development"

    DATABASE_URL: str = "postgresql://pipeline:pipeline@localhost:5432/pipeline"

    OPENCLAW_API_URL: str = "http://localhost:11434"
    OPENCLAW_API_KEY: str = ""
    OPENCLAW_MODEL: str = "qwen2.5-coder:32b"
    OPENCLAW_TIMEOUT: int = 300

    OPENCODE_API_URL: str = "http://localhost:4096"
    OPENCODE_SERVER_PASSWORD: SecretStr = SecretStr("")
    OPENCODE_TIMEOUT: int = 600
    OPENCODE_BUSY_STABLE_SECONDS: int = 120
    OPENCODE_STUCK_TIMEOUT_SECONDS: int = 600
    OPENCODE_EMPTY_DIFF_FOLLOWUP_MIN_STABLE_SECONDS: int = 500
    OPENCODE_MAX_EMPTY_DIFF_FOLLOWUPS: int = 1
    MAX_OPENCODE_STUCK_RETRIES: int = 2
    OPENCODE_WORKSPACE_ROOT: str | None = None

    GITHUB_TOKEN: SecretStr = SecretStr("")
    GITHUB_REPO_OWNER: str = ""
    GITHUB_REPO_NAME: str = ""
    GITHUB_REPO_PRIVATE: bool = True

    TELEGRAM_BOT_TOKEN: SecretStr = SecretStr("")
    TELEGRAM_APPROVAL_CHAT_ID: str = ""
    TELEGRAM_ESCALATION_CHAT_ID: str = ""
    TELEGRAM_NOTIFICATION_CHAT_ID: str = ""

    WEBHOOK_SECRET: SecretStr = SecretStr("")

    MAX_REVIEW_RETRIES: int = 3
    MAX_TEST_RETRIES: int = 3
    MAX_LOCAL_SAME_FAILURE_RETRIES: int = 3
    MAX_CI_SAME_FAILURE_RETRIES: int = 3
    JOB_STALE_TIMEOUT: int = 1800
    CI_POLLING_TIMEOUT: int = 600
    CI_POLLING_GRACE_TIMEOUT: int = 300
    CI_POLLING_INTERVAL: int = 15
    WORKSPACE_ROOT: str = "workspaces"
    NODE_TEST_CONTAINER_SERVICE: str = ""
    NODE_TEST_DOCKER_COMPOSE_DIR: str = "."
    SMOKE_TEST_ENABLED: bool = False
    SMOKE_TEST_BROWSER_TIMEOUT_MS: int = 30000
    SMOKE_TEST_COMMAND_TIMEOUT: int = 180
    SMOKE_TEST_HEADLESS: bool = True


settings = Settings()
