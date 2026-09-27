from __future__ import annotations

from functools import cache
from typing import Any

import pydantic
from pydantic import BaseModel, TypeAdapter

from .errors import ResponseValidationError, ValidationError, ValidationIssue


@cache
def _adapter(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def _issues(error: pydantic.ValidationError) -> list[ValidationIssue]:
    return [
        ValidationIssue(path=".".join(str(p) for p in e["loc"]), message=e["msg"])
        for e in error.errors(include_url=False)
    ]


def _summary(issues: list[ValidationIssue], fallback: str) -> str:
    return "; ".join(f"{i.path or fallback}: {i.message}" for i in issues)


def validate_params(operation: str, tp: Any, value: Any) -> Any:
    """Checks request parameters and returns their JSON form. Raises a
    client-side `ValidationError` (status None) naming each bad field."""
    adapter = _adapter(tp)
    try:
        validated = adapter.validate_python(value)
    except pydantic.ValidationError as exc:
        issues = _issues(exc)
        raise ValidationError(
            f"{operation}: invalid input: {_summary(issues, 'input')}", issues=issues
        ) from None
    return adapter.dump_python(validated, mode="json")


def invalid(operation: str, path: str, message: str) -> ValidationError:
    return ValidationError(
        f"{operation}: invalid input: {path}: {message}",
        issues=[ValidationIssue(path=path, message=message)],
    )


def parse_response[T](
    operation: str, model: type[T], body: Any, *, request_id: str | None, strict: bool
) -> T:
    """Validates a response body. With `strict=False` a mismatch degrades to an
    unvalidated model instead of raising."""
    try:
        return _adapter(model).validate_python(body)  # type: ignore[no-any-return,arg-type]
    except pydantic.ValidationError as exc:
        if not strict and isinstance(body, dict) and issubclass(model, BaseModel):
            return model.model_construct(**body)
        issues = _issues(exc)
        raise ResponseValidationError(
            f"{operation} returned an unexpected shape (request {request_id or 'unknown'}): "
            + _summary(issues, "body"),
            issues=issues,
            body=body,
        ) from None
