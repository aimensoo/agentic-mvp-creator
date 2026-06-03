from fastapi import APIRouter

from api.v1.webhook import router as webhook_router
from api.v1.jobs import router as jobs_router

router = APIRouter(prefix="/api/v1")
router.include_router(webhook_router)
router.include_router(jobs_router)
