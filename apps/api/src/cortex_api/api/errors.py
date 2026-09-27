from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from cortex_api.core.rate_limit import RateLimitExceededError
from cortex_api.core.security import (
    AuthenticationError,
    OrganizationMismatchError,
    PermissionDeniedError,
)
from cortex_api.repositories.base import NotFoundError
from cortex_api.schemas.completion import CompletionErrorResponse
from cortex_api.services.api_keys import LastAdminKeyError
from cortex_api.services.evidence.graph import EvidenceNotFoundError
from cortex_api.services.evidence.linker import DecisionExistsError
from cortex_api.services.evidence.provenance import ProvenanceError
from cortex_api.services.knowledge.extraction import ExtractionError, UnsupportedFormatError
from cortex_api.services.knowledge.hybrid_search import VectorSearchUnavailableError
from cortex_api.services.knowledge.ingestion import DuplicateDocumentError, IngestionError
from cortex_api.services.router.base import ProviderError
from cortex_api.services.router.policies import NoRouteError, RoutingError
from cortex_api.services.router.registry import RegistryError
from cortex_api.services.router.router import CompletionFailedError
from cortex_api.services.router.service import attempt_summaries
from cortex_api.services.scenario.scoring import WeightError


async def not_found_handler(_request: Request, exc: Exception) -> JSONResponse:
    resource = exc.model.__name__ if isinstance(exc, NotFoundError) else "Resource"
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"{resource} not found"},
    )


async def evidence_not_found_handler(_request: Request, exc: Exception) -> JSONResponse:
    kind = exc.kind if isinstance(exc, EvidenceNotFoundError) else "Resource"
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND, content={"detail": f"{kind} not found"}
    )


async def unprocessable_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(exc)}
    )


async def registry_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(exc)}
    )


async def routing_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RoutingError)  # noqa: S101 - registered for RoutingError only
    content: dict[str, object] = {"detail": exc.message}
    if isinstance(exc, NoRouteError) and exc.rejections:
        content["rejections"] = exc.rejections
    return JSONResponse(status_code=exc.status_code, content=content)


async def completion_failed_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CompletionFailedError)  # noqa: S101 - registered for this type only
    body = CompletionErrorResponse(
        id=exc.completion_id, detail=str(exc), attempts=attempt_summaries(exc.attempts)
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


async def extraction_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    code = (
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        if isinstance(exc, UnsupportedFormatError)
        else status.HTTP_422_UNPROCESSABLE_CONTENT
    )
    return JSONResponse(status_code=code, content={"detail": str(exc)})


async def ingestion_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IngestionError)  # noqa: S101 - registered for IngestionError only
    content: dict[str, object] = {"detail": str(exc)}
    if isinstance(exc, DuplicateDocumentError):
        content["document_id"] = str(exc.document_id)
    return JSONResponse(status_code=exc.status_code, content=content)


async def upstream_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Embedding provider failures. Chat failures surface as CompletionFailedError instead."""
    if isinstance(exc, VectorSearchUnavailableError):
        detail = str(exc)
    else:
        assert isinstance(exc, ProviderError)  # noqa: S101 - registered for these types only
        detail = f"embedding provider {exc.provider} failed: {exc.describe()}"
    return JSONResponse(status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": detail})


async def authentication_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    message = exc.message if isinstance(exc, AuthenticationError) else "Not authenticated"
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def organization_mismatch_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "The API key does not belong to the requested organization"},
    )


async def permission_denied_handler(_request: Request, exc: Exception) -> JSONResponse:
    message = exc.message if isinstance(exc, PermissionDeniedError) else "Forbidden"
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": message})


async def conflict_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


async def rate_limit_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceededError)  # noqa: S101 - registered for this type only
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": str(exc)},
        headers={**exc.status.headers(), "Retry-After": str(exc.status.reset_seconds)},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AuthenticationError, authentication_error_handler)
    app.add_exception_handler(OrganizationMismatchError, organization_mismatch_handler)
    app.add_exception_handler(PermissionDeniedError, permission_denied_handler)
    app.add_exception_handler(RateLimitExceededError, rate_limit_handler)
    app.add_exception_handler(LastAdminKeyError, conflict_handler)
    app.add_exception_handler(NotFoundError, not_found_handler)
    app.add_exception_handler(EvidenceNotFoundError, evidence_not_found_handler)
    app.add_exception_handler(DecisionExistsError, conflict_handler)
    app.add_exception_handler(ProvenanceError, unprocessable_handler)
    app.add_exception_handler(WeightError, unprocessable_handler)
    app.add_exception_handler(RegistryError, registry_error_handler)
    app.add_exception_handler(RoutingError, routing_error_handler)
    app.add_exception_handler(CompletionFailedError, completion_failed_handler)
    app.add_exception_handler(ExtractionError, extraction_error_handler)
    app.add_exception_handler(IngestionError, ingestion_error_handler)
    app.add_exception_handler(ProviderError, upstream_error_handler)
    app.add_exception_handler(VectorSearchUnavailableError, upstream_error_handler)
