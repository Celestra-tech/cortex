from fastapi import APIRouter

from cortex_api.api.routes import (
    admin,
    api_keys,
    completions,
    conversations,
    documents,
    events,
    executions,
    knowledge,
    memories,
    messages,
    models,
    observatory,
)

router = APIRouter(prefix="/v1")
router.include_router(conversations.router)
router.include_router(messages.router)
router.include_router(memories.router)
router.include_router(completions.router)
router.include_router(models.router)
router.include_router(executions.router)
router.include_router(documents.router)
router.include_router(knowledge.router)
router.include_router(observatory.router)
router.include_router(events.router)
router.include_router(api_keys.router)
router.include_router(admin.router)
