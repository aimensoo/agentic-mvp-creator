import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def clean_env():
    env_vars = {
        "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
        "OPENCLAW_API_URL": "http://localhost:11434",
        "OPENCLAW_MODEL": "test-model",
        "OPENCODE_API_URL": "http://localhost:4096",
        "WEBHOOK_SECRET": "test-secret",
        "GITHUB_TOKEN": "ghp_test",
        "GITHUB_REPO_OWNER": "testowner",
        "GITHUB_REPO_NAME": "testrepo",
        "GITHUB_REPO_PRIVATE": "false",
        "TELEGRAM_BOT_TOKEN": "123456:ABC",
        "TELEGRAM_APPROVAL_CHAT_ID": "-100",
        "TELEGRAM_ESCALATION_CHAT_ID": "-200",
        "TELEGRAM_NOTIFICATION_CHAT_ID": "-300",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield


def test_settings_loads_from_env():
    from app.config import Settings

    s = Settings()
    assert str(s.DATABASE_URL) == "postgresql://test:test@localhost:5432/test"
    assert s.OPENCLAW_MODEL == "test-model"
    assert s.WEBHOOK_SECRET.get_secret_value() == "test-secret"
    assert s.GITHUB_REPO_PRIVATE is False


def test_settings_has_defaults(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("GITHUB_REPO_PRIVATE", raising=False)
    s = Settings(_env_file=None)
    assert s.MAX_REVIEW_RETRIES == 3
    assert s.MAX_TEST_RETRIES == 3
    assert s.MAX_LOCAL_SAME_FAILURE_RETRIES == 3
    assert s.MAX_CI_SAME_FAILURE_RETRIES == 3
    assert s.JOB_STALE_TIMEOUT == 1800
    assert s.CI_POLLING_TIMEOUT == 600
    assert s.CI_POLLING_GRACE_TIMEOUT == 300
    assert s.CI_POLLING_INTERVAL == 15
    assert s.ENV == "development"
    assert s.OPENCLAW_TIMEOUT == 300
    assert s.OPENCODE_TIMEOUT == 600
    assert s.OPENCODE_BUSY_STABLE_SECONDS == 120
    assert s.OPENCODE_STUCK_TIMEOUT_SECONDS == 600
    assert s.OPENCODE_EMPTY_DIFF_FOLLOWUP_MIN_STABLE_SECONDS == 500
    assert s.OPENCODE_MAX_EMPTY_DIFF_FOLLOWUPS == 1
    assert s.MAX_OPENCODE_STUCK_RETRIES == 2
    assert s.WORKSPACE_ROOT == "workspaces"
    assert s.OPENCODE_WORKSPACE_ROOT is None
    assert s.NODE_TEST_CONTAINER_SERVICE == ""
    assert s.NODE_TEST_DOCKER_COMPOSE_DIR == "."
    assert s.GITHUB_REPO_PRIVATE is True


def test_secret_fields_are_secretstr():
    from app.config import Settings
    from pydantic import SecretStr

    s = Settings()
    assert isinstance(s.WEBHOOK_SECRET, SecretStr)
    assert isinstance(s.GITHUB_TOKEN, SecretStr)
    assert isinstance(s.TELEGRAM_BOT_TOKEN, SecretStr)
    assert isinstance(s.OPENCODE_SERVER_PASSWORD, SecretStr)


def test_settings_singleton():
    from app.config import settings

    assert settings.OPENCLAW_MODEL is not None
