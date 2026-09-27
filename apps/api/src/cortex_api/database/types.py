from enum import StrEnum

from sqlalchemy import Enum


def string_enum(enum_cls: type[StrEnum], name: str, *, length: int = 32) -> Enum:
    """VARCHAR + CHECK constraint instead of a native PostgreSQL enum.

    Adding a member is then a constraint swap rather than an `ALTER TYPE`,
    which cannot run inside a transaction on older PostgreSQL versions.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )
