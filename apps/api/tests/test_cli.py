import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api import cli
from cortex_api.core.security import hash_api_key
from cortex_api.models.organization import Organization
from cortex_api.repositories.api_key import ApiKeyRepository

pytestmark = pytest.mark.database


async def invoke(session: AsyncSession, *argv: str) -> None:
    args = cli.parser().parse_args(argv)
    await args.handler(session, args)


async def test_key_lifecycle_from_the_command_line(
    session: AsyncSession, organization: Organization, capsys: pytest.CaptureFixture[str]
) -> None:
    await invoke(session, "create-api-key", "--organization", organization.slug, "--name", "ops")
    first = capsys.readouterr()
    secret = first.out.strip()
    assert secret.startswith("ctx_") and "\n" not in secret
    assert "role:         admin" in first.err

    repository = ApiKeyRepository(session)
    key = await repository.get_by_hash(hash_api_key(secret))
    assert key is not None and key.role == "admin"

    await invoke(session, "list-api-keys", "--organization", str(organization.id))
    listing = capsys.readouterr().out
    assert str(key.id) in listing and secret not in listing and "active" in listing

    await invoke(
        session,
        "rotate-api-key",
        "--organization",
        organization.slug,
        str(key.id),
        "--grace-seconds",
        "0",
    )
    replacement = capsys.readouterr().out.strip()
    assert replacement != secret
    assert await repository.get_by_hash(hash_api_key(secret)) is None

    new_key = await repository.get_by_hash(hash_api_key(replacement))
    assert new_key is not None
    with pytest.raises(cli.CommandError, match="last usable admin key"):
        await invoke(
            session, "revoke-api-key", "--organization", organization.slug, str(new_key.id)
        )


async def test_member_role_and_unknown_keys(
    session: AsyncSession, organization: Organization, capsys: pytest.CaptureFixture[str]
) -> None:
    await invoke(
        session,
        "create-api-key",
        "--organization",
        organization.slug,
        "--name",
        "app",
        "--role",
        "member",
        "--expires-in-days",
        "7",
    )
    secret = capsys.readouterr().out.strip()
    key = await ApiKeyRepository(session).get_by_hash(hash_api_key(secret))
    assert key is not None and key.role == "member" and key.expires_at is not None

    with pytest.raises(cli.CommandError, match="no active API key"):
        await invoke(
            session,
            "revoke-api-key",
            "--organization",
            organization.slug,
            "01a0e253-3d76-750f-b51c-e1d5a0dd7e8e",
        )
    with pytest.raises(cli.CommandError, match="not a key id"):
        await invoke(session, "revoke-api-key", "--organization", organization.slug, "nope")
