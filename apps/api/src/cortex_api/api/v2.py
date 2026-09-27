from fastapi import APIRouter

from cortex_api.api.routes import evidence

router = APIRouter(prefix="/v2")
router.include_router(evidence.router)
