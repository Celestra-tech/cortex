import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cortex_api.models.api_key import ApiKey, ApiKeyRole
from cortex_api.models.organization import SLUG_PATTERN
from cortex_api.schemas.observatory import OrganizationRead

ApiKeyStatus = Literal["active", "expired", "revoked"]
MAX_GRACE_PERIOD_SECONDS = 7 * 24 * 3600


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255, description="What the key is for.")
    role: ApiKeyRole = ApiKeyRole.MEMBER
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Omit for a key that never expires."
    )


class ApiKeyRotate(BaseModel):
    grace_period_seconds: int = Field(
        default=3600,
        ge=0,
        le=MAX_GRACE_PERIOD_SECONDS,
        description="How long the old key keeps working; 0 revokes it immediately.",
    )
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    prefix: str | None
    role: ApiKeyRole
    status: ApiKeyStatus
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    rotated_from_id: uuid.UUID | None

    @classmethod
    def of(cls, api_key: ApiKey, now: datetime | None = None) -> "ApiKeyRead":
        now = now or datetime.now(UTC)
        status: ApiKeyStatus = (
            "revoked"
            if api_key.deleted_at is not None
            else "expired"
            if api_key.is_expired(now)
            else "active"
        )
        return cls(
            id=api_key.id,
            organization_id=api_key.organization_id,
            name=api_key.name,
            prefix=api_key.prefix,
            role=ApiKeyRole(api_key.role),
            status=status,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            expires_at=api_key.expires_at,
            revoked_at=api_key.deleted_at,
            rotated_from_id=api_key.rotated_from_id,
        )


class ApiKeyCreated(ApiKeyRead):
    secret: str = Field(description="The API key. Shown once; store it now.")


class ApiKeyListResponse(BaseModel):
    items: list[ApiKeyRead]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(max_length=63, pattern=SLUG_PATTERN)
    key_name: str = Field(default="Admin", min_length=1, max_length=255)


class OrganizationBootstrap(BaseModel):
    organization: OrganizationRead
    api_key: ApiKeyCreated
