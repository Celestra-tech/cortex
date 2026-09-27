import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cortex_api.models.organization import SLUG_PATTERN

OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
OrganizationSlug = Annotated[str, StringConstraints(max_length=63, pattern=SLUG_PATTERN)]


class OrganizationCreate(BaseModel):
    name: OrganizationName
    slug: OrganizationSlug = Field(
        description="Lowercase, URL-safe, unique among live organizations."
    )


class OrganizationUpdate(BaseModel):
    """Partial update: only fields present in the payload are changed."""

    name: OrganizationName | None = None
    slug: OrganizationSlug | None = None
    settings: dict[str, Any] | None = None

    def changes(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True, exclude_none=True)


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime
