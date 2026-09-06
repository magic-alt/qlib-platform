from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from qlib_platform.auth.backend import Principal


def _non_empty_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PermissionError(f"federated identity is missing {name}")
    return text


def _audiences(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value)
    raise PermissionError("federated identity has invalid audience claim")


def _groups(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(str(item) for item in value if str(item).strip())
    raise PermissionError("federated identity has invalid groups claim")


@dataclass(frozen=True)
class FederatedIdentityConfig:
    """Trust-boundary configuration for claims from an already verified OIDC token.

    Signature/JWK validation belongs to the deployment-specific OIDC/OAuth2 adapter. This
    class deliberately accepts *verified claims* rather than raw JWT text so qlib-platform
    never silently treats an unsigned token as trusted identity.
    """

    issuer: str
    audience: str
    username_claim: str = "preferred_username"
    groups_claim: str = "groups"
    group_role_mapping: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    default_roles: tuple[str, ...] = ()
    clock_skew_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.audience.strip():
            raise ValueError("issuer and audience are required")
        if not self.username_claim.strip() or not self.groups_claim.strip():
            raise ValueError("username_claim and groups_claim are required")
        if self.clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must be non-negative")


class OIDCIdentityMapper:
    """Map claims from a verified OIDC/OAuth2 identity into the platform Principal."""

    def __init__(self, config: FederatedIdentityConfig):
        self.config = config

    def map_verified_claims(
        self,
        claims: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> Principal:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        current_seconds = current.timestamp()
        skew = float(self.config.clock_skew_seconds)

        issuer = _non_empty_text(claims.get("iss"), "issuer")
        if issuer != self.config.issuer:
            raise PermissionError("federated identity issuer mismatch")
        if self.config.audience not in _audiences(claims.get("aud")):
            raise PermissionError("federated identity audience mismatch")

        try:
            expires_at = float(claims["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionError("federated identity has invalid expiration") from exc
        if expires_at + skew <= current_seconds:
            raise PermissionError("federated identity has expired")

        if "nbf" in claims:
            try:
                not_before = float(claims["nbf"])
            except (TypeError, ValueError) as exc:
                raise PermissionError("federated identity has invalid not-before claim") from exc
            if not_before - skew > current_seconds:
                raise PermissionError("federated identity is not active yet")

        subject = _non_empty_text(claims.get("sub"), "subject")
        username = _non_empty_text(claims.get(self.config.username_claim), self.config.username_claim)
        groups = _groups(claims.get(self.config.groups_claim))
        roles = set(self.config.default_roles)
        for group in groups:
            roles.update(self.config.group_role_mapping.get(group, ()))
        return Principal(subject=subject, username=username, roles=tuple(sorted(roles)))


@dataclass(frozen=True)
class DirectoryRecord:
    """Normalized LDAP/Active Directory identity returned by a deployment adapter."""

    subject: str
    username: str
    groups: tuple[str, ...] = ()
    enabled: bool = True


class DirectoryAdapter(Protocol):
    """Deployment adapter surface for LDAP/Active Directory/enterprise directories."""

    def lookup(self, username: str) -> DirectoryRecord | None: ...


@dataclass(frozen=True)
class DirectoryIdentityConfig:
    group_role_mapping: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    default_roles: tuple[str, ...] = ()


class DirectoryIdentityMapper:
    def __init__(self, config: DirectoryIdentityConfig):
        self.config = config

    def map_record(self, record: DirectoryRecord) -> Principal:
        if not record.enabled:
            raise PermissionError("directory identity is disabled")
        subject = _non_empty_text(record.subject, "subject")
        username = _non_empty_text(record.username, "username")
        roles = set(self.config.default_roles)
        for group in record.groups:
            roles.update(self.config.group_role_mapping.get(group, ()))
        return Principal(subject=subject, username=username, roles=tuple(sorted(roles)))
