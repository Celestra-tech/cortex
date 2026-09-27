from fastapi import APIRouter

from cortex_api.api.routes import evidence, scenarios

router = APIRouter(prefix="/v2")
router.include_router(evidence.router)
router.include_router(scenarios.router)
router.include_router(scenarios.decisions_router)
