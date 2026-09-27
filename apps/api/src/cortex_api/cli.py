"""Operator commands that must not be reachable over HTTP.

uv run python -m cortex_api.cli create-organization --name Acme --slug acme
uv run python -m cortex_api.cli create-api-key --organization acme --name "CI" [--role member]
uv run python -m cortex_api.cli list-api-keys --organization acme
uv run python -m cortex_api.cli rotate-api-key --organization acme <key-id> [--grace-seconds 3600]
uv run python -m cortex_api.cli revoke-api-key --organization acme <key-id>

Secrets go to stdout alone, so they can be captured: KEY=$(... create-api-key ...)
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import get_settings
from cortex_api.database.config import DatabaseConfig
from cortex_api.database.session import create_engine
from cortex_api.models.api_key import ApiKeyRole
from cortex_api.models.organization import Organization
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.organization import OrganizationRepository
from cortex_api.services.api_keys import ApiKeyService, IssuedKey, LastAdminKeyError


class CommandError(Exception):
    pass


async def _organization(session: AsyncSession, reference: str) -> Organization:
    repository = OrganizationRepository(session)
    try:
        organization = await repository.get(uuid.UUID(reference))
    except ValueError:
        organization = await repository.get_by_slug(reference)
    if organization is None:
        raise CommandError(f"no organization {reference!r}")
    return organization


async def create_organization(session: AsyncSession, args: argparse.Namespace) -> None:
    organization = await OrganizationRepository(session).create(name=args.name, slug=args.slug)
    await session.commit()
    print(organization.id)


def _key_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise CommandError(f"not a key id: {value!r}") from exc


def _expiry(days: int | None) -> datetime | None:
    return None if days is None else datetime.now(UTC) + timedelta(days=days)


def _print_secret(issued: IssuedKey, organization: Organization) -> None:
    print(f"id:           {issued.api_key.id}", file=sys.stderr)
    print(f"organization: {organization.id} ({organization.slug})", file=sys.stderr)
    print(f"role:         {issued.api_key.role}", file=sys.stderr)
    print("Store this key now; it cannot be shown again.", file=sys.stderr)
    print(issued.secret)


async def create_api_key(session: AsyncSession, args: argparse.Namespace) -> None:
    organization = await _organization(session, args.organization)
    issued = await ApiKeyService(session).issue(
        organization.id,
        name=args.name,
        role=ApiKeyRole(args.role),
        expires_at=_expiry(args.expires_in_days),
    )
    await session.commit()
    _print_secret(issued, organization)


async def list_api_keys(session: AsyncSession, args: argparse.Namespace) -> None:
    organization = await _organization(session, args.organization)
    now = datetime.now(UTC)
    keys = await ApiKeyService(session).list(organization.id, include_revoked=args.all)
    for key in keys:
        state = "revoked" if key.is_deleted else "expired" if key.is_expired(now) else "active"
        used = key.last_used_at.isoformat(timespec="seconds") if key.last_used_at else "never"
        print(
            f"{key.id}  {key.prefix or '-':<12}  {key.role:<6}  {state:<7}  {used:<25}  {key.name}"
        )


async def rotate_api_key(session: AsyncSession, args: argparse.Namespace) -> None:
    organization = await _organization(session, args.organization)
    try:
        issued = await ApiKeyService(session).rotate(
            organization.id,
            _key_id(args.key_id),
            grace_period=timedelta(seconds=args.grace_seconds),
            expires_at=_expiry(args.expires_in_days),
        )
    except NotFoundError as exc:
        raise CommandError(f"no active API key {args.key_id} in {organization.slug}") from exc
    await session.commit()
    _print_secret(issued, organization)


async def revoke_api_key(session: AsyncSession, args: argparse.Namespace) -> None:
    organization = await _organization(session, args.organization)
    try:
        api_key = await ApiKeyService(session).revoke(organization.id, _key_id(args.key_id))
    except NotFoundError as exc:
        raise CommandError(f"no active API key {args.key_id} in {organization.slug}") from exc
    except LastAdminKeyError as exc:
        raise CommandError(str(exc)) from exc
    await session.commit()
    print(f"revoked {api_key.id}", file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="cortex_api.cli")
    commands = root.add_subparsers(dest="command", required=True)

    org = commands.add_parser("create-organization", help="Create a tenant.")
    org.add_argument("--name", required=True)
    org.add_argument("--slug", required=True)
    org.set_defaults(handler=create_organization)

    key = commands.add_parser("create-api-key", help="Mint a key; prints the secret once.")
    key.add_argument("--organization", required=True, help="Organization id or slug.")
    key.add_argument("--name", required=True, help="What the key is for, e.g. 'CI'.")
    key.add_argument(
        "--role",
        choices=[role.value for role in ApiKeyRole],
        default=ApiKeyRole.ADMIN.value,
        help="admin keys can also manage API keys (default: admin).",
    )
    key.add_argument("--expires-in-days", type=int, default=None)
    key.set_defaults(handler=create_api_key)

    listing = commands.add_parser("list-api-keys", help="Show keys (never their secrets).")
    listing.add_argument("--organization", required=True, help="Organization id or slug.")
    listing.add_argument("--all", action="store_true", help="Include revoked keys.")
    listing.set_defaults(handler=list_api_keys)

    rotate = commands.add_parser("rotate-api-key", help="Replace a key; prints the new secret.")
    rotate.add_argument("--organization", required=True, help="Organization id or slug.")
    rotate.add_argument("key_id")
    rotate.add_argument(
        "--grace-seconds",
        type=int,
        default=3600,
        help="How long the old key keeps working (0 revokes it now; default 3600).",
    )
    rotate.add_argument("--expires-in-days", type=int, default=None)
    rotate.set_defaults(handler=rotate_api_key)

    revoke = commands.add_parser("revoke-api-key", help="Revoke a key by id.")
    revoke.add_argument("--organization", required=True, help="Organization id or slug.")
    revoke.add_argument("key_id")
    revoke.set_defaults(handler=revoke_api_key)
    return root


async def run(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    engine = create_engine(DatabaseConfig.from_settings(get_settings()), null_pool=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await args.handler(session, args)
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
