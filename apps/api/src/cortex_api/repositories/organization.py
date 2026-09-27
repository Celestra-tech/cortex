from cortex_api.models.organization import Organization
from cortex_api.repositories.base import BaseRepository


class OrganizationRepository(BaseRepository[Organization]):
    model = Organization

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self.session.execute(self.select().where(Organization.slug == slug))
        return result.scalar_one_or_none()
