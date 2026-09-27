import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, StringConstraints

from cortex_api.models.user import UserRole

# The database enforces `email = lower(email)`; normalize before it gets there.
Email = Annotated[EmailStr, AfterValidator(str.lower)]
FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class UserCreate(BaseModel):
    """The organization comes from the authenticated context, never the payload."""

    email: Email
    full_name: FullName | None = None
    role: UserRole = UserRole.MEMBER


class UserUpdate(BaseModel):
    """Partial update: only fields present in the payload are changed.

    `full_name` may be explicitly set to null to clear it.
    """

    email: Email | None = None
    full_name: FullName | None = None
    role: UserRole | None = None

    def changes(self) -> dict[str, Any]:
        values = self.model_dump(exclude_unset=True)
        return {
            key: value for key, value in values.items() if value is not None or key == "full_name"
        }


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    full_name: str | None
    role: UserRole
    created_at: datetime
    updated_at: datetime
