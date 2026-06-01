from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from dependencies import get_db_service
from services.db_service import DatabaseService

router = APIRouter(prefix="/jobs", tags=["jobs"])


class TelegramActionPayload(BaseModel):
    telegram_user_id: int | None = None


class FeedbackPayload(BaseModel):
    telegram_user_id: int | None = None
    feedback: str


@router.get("")
async def list_jobs(
    limit: int = 20,
    db: DatabaseService = Depends(get_db_service),
):
    return await db.list_jobs(limit)


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/{job_id}/approve")
async def approve_job(
    job_id: str,
    payload: TelegramActionPayload | None = None,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    await db.update_job(job_id, human_approved=True)
    await db.update_status(job_id, "preparing_workspace")
    return {"status": "approved"}


@router.post("/{job_id}/reject")
async def reject_job(
    job_id: str,
    payload: TelegramActionPayload | None = None,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    await db.update_status(job_id, "awaiting_human_feedback")
    return {"status": "feedback_requested"}


@router.post("/{job_id}/feedback")
async def submit_approval_feedback(
    job_id: str,
    payload: FeedbackPayload,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    feedback = payload.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="Feedback must not be empty")
    if job.get("status") != "awaiting_human_feedback":
        raise HTTPException(status_code=409, detail="Job is not waiting for human feedback")
    await db.update_job(job_id, approval_feedback=feedback)
    await db.update_status(job_id, "planning")
    return {"status": "replan_queued"}


@router.post("/{job_id}/kill")
async def kill_job(
    job_id: str,
    payload: TelegramActionPayload | None = None,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    await db.update_status(job_id, "failed")
    await db.update_job(job_id, error_message="Killed by human")
    return {"status": "killed"}


@router.post("/{job_id}/escalation-resolve")
async def escalation_resolve(
    job_id: str,
    payload: TelegramActionPayload | None = None,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    await db.update_status(job_id, "done")
    return {"status": "resolved"}


@router.post("/{job_id}/escalation-reject")
async def escalation_reject(
    job_id: str,
    payload: TelegramActionPayload | None = None,
    db: DatabaseService = Depends(get_db_service),
):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ensure_customer(job, payload)
    await db.update_status(job_id, "failed")
    await db.update_job(job_id, error_message="Rejected by human (escalation)")
    return {"status": "rejected"}


def _ensure_customer(job: dict, payload: TelegramActionPayload | None) -> None:
    expected_user_id = job.get("telegram_user_id")
    if expected_user_id is None:
        return

    actual_user_id = payload.telegram_user_id if payload else None
    if actual_user_id != expected_user_id:
        raise HTTPException(status_code=403, detail="Only the MVP customer can perform this action")
