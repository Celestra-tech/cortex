import uuid
from collections.abc import Sequence

from cortex_api.models.user import User
from cortex_api.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, organization_id: uuid.UUID, email: str) -> User | None:
        statement = self.select().where(
            User.organization_id == organization_id,
            User.email == email.strip().lower(),
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_by_organization(self, organization_id: uuid.UUID) -> Sequence[User]:
        statement = self.select().where(User.organization_id == organization_id).order_by(User.id)
        result = await self.session.execute(statement)
        return result.scalars().all()
