from fastapi import APIRouter, Depends, Header, HTTPException

from dependencies import get_db_service
from services.db_service import DatabaseService
from app.config import settings
from pydantic import BaseModel, field_validator

router = APIRouter(prefix="/webhook", tags=["webhook"])


class WebhookPayload(BaseModel):
    user_request: str
    input_source: str = "api"
    chat_id: int | None = None
    telegram_user_id: int | None = None

    @field_validator("user_request")
    @classmethod
    def user_request_must_not_be_empty(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("user_request must not be empty")
        return text


@router.post("/trigger", status_code=202)
async def handle_trigger_webhook(
    payload: WebhookPayload,
    x_webhook_secret: str = Header(None),
    db: DatabaseService = Depends(get_db_service),
):
    expected = settings.WEBHOOK_SECRET.get_secret_value()
    if not x_webhook_secret or x_webhook_secret != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    job_id = await db.create_job(
        input_text=payload.user_request,
        input_source=payload.input_source,
        chat_id=payload.chat_id,
        telegram_user_id=payload.telegram_user_id,
    )
    return {"job_id": job_id}
