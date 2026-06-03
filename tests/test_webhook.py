import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from pydantic import SecretStr


def _create_app():
    from api.v1.router import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def client(mock_db):
    app = _create_app()
    from dependencies import get_db_service

    app.dependency_overrides[get_db_service] = lambda: mock_db
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _set_webhook_secret():
    with patch.dict(
        "os.environ", {"WEBHOOK_SECRET": "test-secret", "DATABASE_URL": "postgresql://t:t@localhost/t"}, clear=False
    ):
        yield


class TestWebhook:
    def test_webhook_creates_job(self, client, mock_db):
        mock_db.create_job = AsyncMock(return_value="job-123")

        with patch("api.v1.webhook.settings.WEBHOOK_SECRET", SecretStr("test-secret")):
            resp = client.post(
                "/api/v1/webhook/trigger",
                json={
                    "user_request": "Build a CRM dashboard",
                    "input_source": "telegram",
                    "chat_id": 12345,
                    "telegram_user_id": 67890,
                },
                headers={"X-Webhook-Secret": "test-secret"},
            )

        assert resp.status_code == 202
        body = resp.json()
        assert body["job_id"] == "job-123"
        mock_db.create_job.assert_called_once_with(
            input_text="Build a CRM dashboard",
            input_source="telegram",
            chat_id=12345,
            telegram_user_id=67890,
        )

    def test_webhook_rejects_bad_secret(self, client, mock_db):
        resp = client.post(
            "/api/v1/webhook/trigger",
            json={
                "user_request": "Build a CRM dashboard",
                "chat_id": 12345,
                "telegram_user_id": 67890,
            },
            headers={"X-Webhook-Secret": "wrong-secret"},
        )

        assert resp.status_code == 401
        mock_db.create_job.assert_not_called()

    def test_webhook_rejects_missing_secret(self, client, mock_db):
        resp = client.post(
            "/api/v1/webhook/trigger",
            json={
                "user_request": "Build a CRM dashboard",
                "chat_id": 12345,
                "telegram_user_id": 67890,
            },
        )

        assert resp.status_code == 401
        mock_db.create_job.assert_not_called()

    def test_webhook_rejects_invalid_body(self, client, mock_db):
        resp = client.post(
            "/api/v1/webhook/trigger",
            json={},
            headers={"X-Webhook-Secret": "test-secret"},
        )

        assert resp.status_code == 422
