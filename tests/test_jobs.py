import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


def _create_app():
    from api.v1.router import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def client(mock_db):
    app = _create_app()
    from dependencies import get_db_service

    app.dependency_overrides[get_db_service] = lambda: mock_db
    with TestClient(app) as c:
        yield c


class TestGetJob:
    def test_get_job_found(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "queued",
                "input_text": "t1",
            }
        )

        resp = client.get("/api/v1/jobs/abc")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "abc"
        assert body["status"] == "queued"

    def test_get_job_not_found(self, client, mock_db):
        mock_db.get_job = AsyncMock(return_value=None)

        resp = client.get("/api/v1/jobs/nonexistent")

        assert resp.status_code == 404


class TestListJobs:
    def test_list_jobs(self, client, mock_db):
        mock_db.list_jobs = AsyncMock(
            return_value=[
                {"id": "1", "status": "queued"},
                {"id": "2", "status": "done"},
            ]
        )

        resp = client.get("/api/v1/jobs")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2

    def test_list_jobs_with_limit(self, client, mock_db):
        mock_db.list_jobs = AsyncMock(return_value=[])

        resp = client.get("/api/v1/jobs?limit=5")

        assert resp.status_code == 200
        mock_db.list_jobs.assert_called_once_with(5)


class TestApproveJob:
    def test_approve(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/approve")

        assert resp.status_code == 200
        mock_db.update_job.assert_called_once_with("abc", human_approved=True)
        mock_db.update_status.assert_called_once_with("abc", "preparing_workspace")

    def test_approve_requires_customer_when_job_has_customer(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
                "telegram_user_id": 67890,
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/approve", json={"telegram_user_id": 111})

        assert resp.status_code == 403
        mock_db.update_job.assert_not_called()
        mock_db.update_status.assert_not_called()

    def test_approve_accepts_customer(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
                "telegram_user_id": 67890,
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/approve", json={"telegram_user_id": 67890})

        assert resp.status_code == 200
        mock_db.update_job.assert_called_once_with("abc", human_approved=True)
        mock_db.update_status.assert_called_once_with("abc", "preparing_workspace")


class TestRejectJob:
    def test_reject(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/reject")

        assert resp.status_code == 200
        mock_db.update_status.assert_called_once_with("abc", "awaiting_human_feedback")
        mock_db.update_job.assert_not_called()

    def test_reject_requires_customer_when_job_has_customer(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
                "telegram_user_id": 67890,
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/reject", json={"telegram_user_id": 111})

        assert resp.status_code == 403
        mock_db.update_job.assert_not_called()
        mock_db.update_status.assert_not_called()


class TestApprovalFeedback:
    def test_feedback_requeues_planning(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_feedback",
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/feedback", json={"feedback": "Нужно изменить архитектуру"})

        assert resp.status_code == 200
        assert resp.json()["status"] == "replan_queued"
        mock_db.update_job.assert_called_once_with("abc", approval_feedback="Нужно изменить архитектуру")
        mock_db.update_status.assert_called_once_with("abc", "planning")

    def test_feedback_rejects_wrong_status(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
            }
        )
        mock_db.update_job = AsyncMock()
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/feedback", json={"feedback": "fix"})

        assert resp.status_code == 409
        mock_db.update_job.assert_not_called()
        mock_db.update_status.assert_not_called()


class TestKillJob:
    def test_kill(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_approval",
            }
        )
        mock_db.update_status = AsyncMock()
        mock_db.update_job = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/kill")

        assert resp.status_code == 200
        assert resp.json()["status"] == "killed"
        mock_db.update_status.assert_called_once_with("abc", "failed")
        mock_db.update_job.assert_called_once_with("abc", error_message="Killed by human")


class TestEscalationResolve:
    def test_escalation_resolve(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_escalation",
            }
        )
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/escalation-resolve")

        assert resp.status_code == 200
        mock_db.update_status.assert_called_once_with("abc", "done")

    def test_escalation_resolve_requires_customer(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_escalation",
                "telegram_user_id": 67890,
            }
        )
        mock_db.update_status = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/escalation-resolve", json={"telegram_user_id": 111})

        assert resp.status_code == 403
        mock_db.update_status.assert_not_called()


class TestEscalationReject:
    def test_escalation_reject(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_escalation",
            }
        )
        mock_db.update_status = AsyncMock()
        mock_db.update_job = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/escalation-reject")

        assert resp.status_code == 200
        mock_db.update_status.assert_called_once_with("abc", "failed")
        mock_db.update_job.assert_called_once_with("abc", error_message="Rejected by human (escalation)")

    def test_escalation_reject_accepts_customer(self, client, mock_db):
        mock_db.get_job = AsyncMock(
            return_value={
                "id": "abc",
                "status": "awaiting_human_escalation",
                "telegram_user_id": 67890,
            }
        )
        mock_db.update_status = AsyncMock()
        mock_db.update_job = AsyncMock()

        resp = client.post("/api/v1/jobs/abc/escalation-reject", json={"telegram_user_id": 67890})

        assert resp.status_code == 200
        mock_db.update_status.assert_called_once_with("abc", "failed")
        mock_db.update_job.assert_called_once_with("abc", error_message="Rejected by human (escalation)")
