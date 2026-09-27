import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Self

from redis.asyncio import Redis

from cortex_api.core.config import Settings
from cortex_api.schemas.memory import SessionMessage, SessionState

# Runs atomically inside Redis so concurrent appends to one conversation never
# lose messages. Must trim exactly like `trim_messages` below.
#
# KEYS[1] session key
# ARGV[1] mode: "append" (ARGV[2] = one message) or "replace" (ARGV[2] = message array)
# ARGV[3] max messages   ARGV[4] max tokens   ARGV[5] ttl seconds   ARGV[6] last activity
_SESSION_SCRIPT = """
local messages = {}
if ARGV[1] == "append" then
  local raw = redis.call("HGET", KEYS[1], "messages")
  if raw then messages = cjson.decode(raw) end
  local incoming = cjson.decode(ARGV[2])
  local duplicate = false
  for _, message in ipairs(messages) do
    if message.id == incoming.id then duplicate = true break end
  end
  if not duplicate then table.insert(messages, incoming) end
else
  messages = cjson.decode(ARGV[2])
end

local max_messages = tonumber(ARGV[3])
while #messages > max_messages do table.remove(messages, 1) end

local tokens = 0
for _, message in ipairs(messages) do tokens = tokens + (tonumber(message.token_count) or 0) end

local max_tokens = tonumber(ARGV[4])
while tokens > max_tokens and #messages > 1 do
  tokens = tokens - (tonumber(messages[1].token_count) or 0)
  table.remove(messages, 1)
end

local encoded = cjson.encode(messages)
redis.call("HSET", KEYS[1], "messages", encoded, "token_count", tokens, "last_activity", ARGV[6])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[5]))
return {encoded, tokens}
"""


def trim_messages(
    messages: Sequence[SessionMessage], *, max_messages: int, max_tokens: int
) -> list[SessionMessage]:
    """Newest-wins window: cap the count, then drop oldest until within the token budget.

    The newest message is always kept, even if it alone exceeds the budget.
    """
    window = list(messages[-max_messages:])
    tokens = sum(message.token_count for message in window)
    while tokens > max_tokens and len(window) > 1:
        tokens -= window.pop(0).token_count
    return window


def _text(value: str | bytes) -> str:
    return value.decode() if isinstance(value, bytes) else value


def _decode_messages(raw: str | bytes) -> list[SessionMessage]:
    decoded: Any = json.loads(raw)
    # Lua's cjson encodes an empty table as {} rather than [].
    if not isinstance(decoded, list):
        return []
    return [SessionMessage.model_validate(item) for item in decoded]


class SessionCache:
    """Hot conversation state in Redis under `session:{conversation_id}`.

    A hash holding `messages` (JSON array, oldest first), `token_count`, and
    `last_activity`. Every write refreshes the TTL, so a session expires after
    `ttl_seconds` of inactivity. Redis is a cache: Postgres stays the source of
    truth and callers rebuild on a miss.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        ttl_seconds: int,
        max_messages: int,
        max_tokens: int,
        key_prefix: str = "",
    ) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages
        self.max_tokens = max_tokens
        self.key_prefix = key_prefix
        self._script = redis.register_script(_SESSION_SCRIPT)

    @classmethod
    def from_settings(cls, redis: Redis, settings: Settings) -> Self:
        return cls(
            redis,
            ttl_seconds=settings.memory_session_ttl_seconds,
            max_messages=settings.memory_session_max_messages,
            max_tokens=settings.memory_session_max_tokens,
            key_prefix=settings.memory_session_key_prefix,
        )

    def key(self, conversation_id: uuid.UUID) -> str:
        return f"{self.key_prefix}session:{conversation_id}"

    def trim(self, messages: Sequence[SessionMessage]) -> list[SessionMessage]:
        return trim_messages(messages, max_messages=self.max_messages, max_tokens=self.max_tokens)

    async def get(self, conversation_id: uuid.UUID) -> SessionState | None:
        raw = await self.redis.hgetall(self.key(conversation_id))
        data = {_text(field): _text(value) for field, value in raw.items()}
        if "messages" not in data or "last_activity" not in data:
            return None
        return SessionState(
            conversation_id=conversation_id,
            messages=_decode_messages(data["messages"]),
            token_count=int(data.get("token_count", 0)),
            last_activity=datetime.fromisoformat(data["last_activity"]),
        )

    async def append(self, conversation_id: uuid.UUID, message: SessionMessage) -> SessionState:
        return await self._run(
            conversation_id, "append", message.model_dump_json(), message.created_at
        )

    async def replace(
        self,
        conversation_id: uuid.UUID,
        messages: Sequence[SessionMessage],
        last_activity: datetime,
    ) -> SessionState:
        payload = json.dumps([message.model_dump(mode="json") for message in messages])
        return await self._run(conversation_id, "replace", payload, last_activity)

    async def invalidate(self, conversation_id: uuid.UUID) -> None:
        await self.redis.delete(self.key(conversation_id))

    async def ttl(self, conversation_id: uuid.UUID) -> int:
        return int(await self.redis.ttl(self.key(conversation_id)))

    async def exists_many(self, conversation_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, bool]:
        if not conversation_ids:
            return {}
        async with self.redis.pipeline(transaction=False) as pipe:
            for conversation_id in conversation_ids:
                pipe.exists(self.key(conversation_id))
            results = await pipe.execute()
        return {cid: bool(found) for cid, found in zip(conversation_ids, results, strict=True)}

    async def _run(
        self, conversation_id: uuid.UUID, mode: str, payload: str, last_activity: datetime
    ) -> SessionState:
        encoded, tokens = await self._script(
            keys=[self.key(conversation_id)],
            args=[
                mode,
                payload,
                self.max_messages,
                self.max_tokens,
                self.ttl_seconds,
                last_activity.isoformat(),
            ],
        )
        return SessionState(
            conversation_id=conversation_id,
            messages=_decode_messages(encoded),
            token_count=int(tokens),
            last_activity=last_activity,
        )
