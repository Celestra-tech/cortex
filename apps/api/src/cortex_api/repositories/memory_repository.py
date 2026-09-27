import uuid
from collections.abc import Sequence

from sqlalchemy import func, select

from cortex_api.models.conversation import Conversation
from cortex_api.models.memory import Memory
from cortex_api.models.message import Message
from cortex_api.repositories.base import BaseRepository
from cortex_api.services.memory.retrieval import MemoryQuery, RankedMemory, RetrievalPolicy


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def get_for_organization(
        self, organization_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation | None:
        statement = self.select().where(
            Conversation.id == conversation_id,
            Conversation.organization_id == organization_id,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_with_counts(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[tuple[Conversation, int]]:
        """Live conversations, most recently active first, with their message counts."""
        counts = (
            select(Message.conversation_id, func.count().label("messages"))
            .group_by(Message.conversation_id)
            .subquery()
        )
        statement = (
            self.select()
            .add_columns(func.coalesce(counts.c.messages, 0))
            .outerjoin(counts, counts.c.conversation_id == Conversation.id)
            .where(Conversation.organization_id == organization_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(statement)).all()
        return [(row[0], int(row[1])) for row in rows]

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.organization_id == organization_id, Conversation.deleted_at.is_(None)
            )
        )
        return (await self.session.execute(statement)).scalar_one()


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def list_recent(self, conversation_id: uuid.UUID, *, limit: int) -> list[Message]:
        """The newest `limit` messages, returned oldest first."""
        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return list(reversed(result.scalars().all()))

    async def count_for_conversation(self, conversation_id: uuid.UUID) -> int:
        statement = select(func.count()).where(Message.conversation_id == conversation_id)
        return (await self.session.execute(statement)).scalar_one()

    async def get_for_organization(
        self, organization_id: uuid.UUID, message_id: uuid.UUID
    ) -> Message | None:
        statement = (
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.id == message_id, Conversation.organization_id == organization_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()


class MemoryRepository(BaseRepository[Memory]):
    model = Memory

    async def search(
        self,
        organization_id: uuid.UUID,
        query: MemoryQuery,
        policy: RetrievalPolicy,
    ) -> Sequence[RankedMemory]:
        result = await self.session.execute(policy.statement(organization_id, query))
        return [RankedMemory(memory=memory, score=score) for memory, score in result.all()]
