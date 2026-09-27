"""API-key credentials.

Keys are shown once at creation and only their SHA-256 digest is stored. The
secrets carry 256 bits of entropy, so a fast unsalted hash is sufficient: the
digest cannot be brute-forced, and lookups stay a single indexed equality.
"""

import hashlib
import secrets

API_KEY_PREFIX = "ctx_"
# 8 random characters (48 bits) identify a key in listings without weakening the rest.
DISPLAY_PREFIX_LENGTH = len(API_KEY_PREFIX) + 8


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def display_prefix(secret: str) -> str:
    return secret[:DISPLAY_PREFIX_LENGTH]


def tokens_match(supplied: str, expected: str) -> bool:
    """Constant-time comparison for shared secrets such as the admin token."""
    return secrets.compare_digest(supplied.encode(), expected.encode())


class AuthenticationError(Exception):
    """Missing, malformed, invalid, or revoked credentials (HTTP 401)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OrganizationMismatchError(Exception):
    """The API key belongs to a different organization than the one requested (HTTP 403)."""


class PermissionDeniedError(Exception):
    """Authenticated, but the credential's role does not allow the action (HTTP 403)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
