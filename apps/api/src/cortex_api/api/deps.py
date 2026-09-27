import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.requests import HTTPConnection
from redis.asyncio import Redis

from cortex_api.cache.redis import get_redis
from cortex_api.core.config import Settings
from cortex_api.core.rate_limit import RateLimiter, RateLimitExceededError
from cortex_api.core.security import (
    AuthenticationError,
    OrganizationMismatchError,
    PermissionDeniedError,
    hash_api_key,
)
from cortex_api.database.session import DbSession
from cortex_api.models.api_key import ApiKey, ApiKeyRole
from cortex_api.models.organization import Organization
from cortex_api.repositories.api_key import ApiKeyRepository
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.organization import OrganizationRepository
from cortex_api.services.knowledge.embeddings import Embedder
from cortex_api.services.knowledge.service import KnowledgeService
from cortex_api.services.memory.service import MemoryService
from cortex_api.services.observatory.events import EventPublisher
from cortex_api.services.router.router import CortexRouter
from cortex_api.services.router.service import CompletionService

logger = logging.getLogger(__name__)

ORGANIZATION_HEADER = "X-Organization-ID"
RATE_LIMIT_SETTING = "rate_limit_per_minute"


def get_app_settings(request: HTTPConnection) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling: a tenant and, unless in header-only development mode, its API key."""

    organization: Organization
    api_key: ApiKey | None

    @property
    def role(self) -> ApiKeyRole:
        # Header-only mode is a trusted development setup, so it acts as an admin.
        return ApiKeyRole.ADMIN if self.api_key is None else ApiKeyRole(self.api_key.role)

    @property
    def api_key_id(self) -> uuid.UUID | None:
        return None if self.api_key is None else self.api_key.id


async def get_principal(
    request: Request,
    session: DbSession,
    settings: SettingsDep,
    redis: RedisDep,
    authorization: Annotated[str | None, Header()] = None,
    organization_id: Annotated[uuid.UUID | None, Header(alias=ORGANIZATION_HEADER)] = None,
) -> Principal:
    """Tenant context. Every tenant-scoped query must go through this dependency.

    `Authorization: Bearer <api key>` resolves the key's organization; an
    `X-Organization-ID` sent alongside must match it. Without a key, the header
    alone is trusted, unless `auth_require_api_key` is set. Authenticated
    requests are then counted against the caller's rate limit.
    """
    principal = await authenticate(session, settings, authorization, organization_id)
    if settings.rate_limit_enabled:
        await _enforce_rate_limit(request, redis, settings, principal)
    return principal


async def authenticate(
    session: DbSession,
    settings: Settings,
    authorization: str | None,
    organization_id: uuid.UUID | None,
) -> Principal:
    organizations = OrganizationRepository(session)
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("Authorization must be `Bearer <api key>`")
        keys = ApiKeyRepository(session)
        api_key = await keys.get_by_hash(hash_api_key(token))
        if api_key is None:
            raise AuthenticationError("Invalid or revoked API key")
        now = datetime.now(UTC)
        if api_key.is_expired(now):
            raise AuthenticationError("API key has expired")
        if organization_id is not None and organization_id != api_key.organization_id:
            raise OrganizationMismatchError
        organization = await organizations.get(api_key.organization_id)
        if organization is None:
            raise AuthenticationError("Invalid or revoked API key")
        interval = timedelta(seconds=settings.auth_last_used_interval_seconds)
        if api_key.last_used_at is None or now - api_key.last_used_at >= interval:
            # Committed on its own so a failing request still records the use.
            await keys.touch(api_key, now)
            await session.commit()
        return Principal(organization, api_key)

    if settings.auth_require_api_key:
        raise AuthenticationError("An API key is required: send `Authorization: Bearer <api key>`")
    if organization_id is None:
        raise AuthenticationError(
            f"Send `Authorization: Bearer <api key>` or an `{ORGANIZATION_HEADER}` header"
        )
    organization = await organizations.get(organization_id)
    if organization is None:
        raise NotFoundError(Organization, organization_id)
    return Principal(organization, None)


def _organization_limit(organization: Organization, default: int) -> int:
    override = organization.settings.get(RATE_LIMIT_SETTING)
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return default


async def _enforce_rate_limit(
    request: Request, redis: Redis, settings: Settings, principal: Principal
) -> None:
    limit = _organization_limit(principal.organization, settings.rate_limit_requests_per_minute)
    bucket = (
        f"org:{principal.organization.id}"
        if principal.api_key is None
        else f"key:{principal.api_key.id}"
    )
    limiter = RateLimiter(redis, key_prefix=settings.rate_limit_key_prefix)
    status = await limiter.hit(bucket, limit)
    if status is None:
        return
    request.state.rate_limit = status
    if status.exceeded:
        raise RateLimitExceededError(status)


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def get_organization(principal: PrincipalDep) -> Organization:
    return principal.organization


def require_admin(principal: PrincipalDep) -> Principal:
    if principal.role is not ApiKeyRole.ADMIN:
        raise PermissionDeniedError("This action requires an admin API key")
    return principal


AdminDep = Annotated[Principal, Depends(require_admin)]
OrganizationDep = Annotated[Organization, Depends(get_organization)]


def get_memory_service(session: DbSession, redis: RedisDep, settings: SettingsDep) -> MemoryService:
    return MemoryService.from_settings(session, redis, settings)


MemoryServiceDep = Annotated[MemoryService, Depends(get_memory_service)]


def get_router(request: Request) -> CortexRouter:
    """Process-wide: the registry's circuit breakers and latency stats live here."""
    router: CortexRouter = request.app.state.router
    return router


RouterDep = Annotated[CortexRouter, Depends(get_router)]


def get_embedder(request: Request) -> Embedder:
    """Process-wide: shares the application's HTTP connection pool."""
    embedder: Embedder = request.app.state.embedder
    return embedder


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]


def get_knowledge_service(
    session: DbSession, redis: RedisDep, settings: SettingsDep, embedder: EmbedderDep
) -> KnowledgeService:
    return KnowledgeService.from_settings(session, redis, settings, embedder)


KnowledgeServiceDep = Annotated[KnowledgeService, Depends(get_knowledge_service)]


def get_event_publisher(redis: RedisDep, settings: SettingsDep) -> EventPublisher:
    return EventPublisher.from_settings(redis, settings)


EventPublisherDep = Annotated[EventPublisher, Depends(get_event_publisher)]


def get_completion_service(
    session: DbSession,
    router: RouterDep,
    memory: MemoryServiceDep,
    knowledge: KnowledgeServiceDep,
    events: EventPublisherDep,
) -> CompletionService:
    return CompletionService(session, router, memory, knowledge, events)


CompletionServiceDep = Annotated[CompletionService, Depends(get_completion_service)]
