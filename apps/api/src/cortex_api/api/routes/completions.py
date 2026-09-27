import json
import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from cortex_api.api.deps import CompletionServiceDep, OrganizationDep
from cortex_api.schemas.completion import (
    CompletionErrorResponse,
    CompletionRequest,
    CompletionResponse,
)

router = APIRouter(prefix="/chat", tags=["completions"])

# A word plus its trailing whitespace, or leading whitespace on its own.
_TOKEN = re.compile(r"\S+\s*|\s+")


@router.post(
    "/completions",
    response_model=CompletionResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "JSON, or Server-Sent Events when `stream` is true.",
        },
        403: {"description": "Blocked by the organization's routing policy."},
        422: {"description": "Invalid request or unknown model."},
        502: {"model": CompletionErrorResponse, "description": "Every provider failed."},
        503: {"description": "No provider can serve this request right now."},
    },
)
async def create_completion(
    body: CompletionRequest, organization: OrganizationDep, service: CompletionServiceDep
) -> CompletionResponse | StreamingResponse:
    completion = await service.complete(organization, body)
    if not body.stream:
        return completion
    return StreamingResponse(
        completion_events(completion),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def completion_events(completion: CompletionResponse) -> AsyncIterator[str]:
    """The SSE protocol: `start`, one `token` per text delta, then `complete`.

    Providers are called without upstream streaming, so deltas are cut from
    the finished output and failures surface as ordinary HTTP errors before
    the stream opens. The event protocol, including `error`, is what clients
    code against, so native upstream streaming can replace this generator
    without client changes.
    """
    yield _sse(
        "start",
        {
            "id": str(completion.id),
            "created_at": completion.created_at.isoformat(),
            "provider": completion.provider,
            "model": completion.model,
            "routing_reason": completion.routing_reason,
        },
    )
    for index, match in enumerate(_TOKEN.finditer(completion.output)):
        yield _sse("token", {"index": index, "delta": match.group()})
    yield _sse("complete", completion.model_dump(mode="json"))


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
