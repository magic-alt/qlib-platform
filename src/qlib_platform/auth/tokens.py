from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qlib_platform.auth.backend import Principal

ALLOWED_SERVICE_SCOPES = frozenset(
    {
        "project:read",
        "project:manage",
        "experiment:read",
        "experiment:write",
        "dataset:read",
        "artifact:read",
        "artifact:write",
        "research:run",
    }
)
_FORBIDDEN_SCOPE_PREFIXES = ("promotion:", "execution:", "broker:", "oms:")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("stored token timestamp must be timezone-aware")
    return parsed


def _validate_scopes(scopes: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(scope.strip() for scope in scopes if scope.strip())))
    if not normalized:
        raise ValueError("at least one service token scope is required")
    forbidden = [scope for scope in normalized if scope.startswith(_FORBIDDEN_SCOPE_PREFIXES)]
    if forbidden:
        raise ValueError(f"service tokens cannot acquire execution/promotion scopes: {forbidden}")
    unknown = set(normalized) - ALLOWED_SERVICE_SCOPES
    if unknown:
        raise ValueError(f"unknown service token scopes: {sorted(unknown)}")
    return normalized


@dataclass(frozen=True)
class ServiceAccount:
    account_id: str
    name: str
    owner_subject: str
    enabled: bool


@dataclass(frozen=True)
class ServiceCredential:
    principal: Principal
    token_id: str
    scopes: tuple[str, ...]
    expires_at_utc: str

    def allows(self, scope: str) -> bool:
        return scope in self.scopes


class ServiceTokenStore:
    """Hash-only lifecycle store for high-entropy service-account API tokens."""

    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS service_accounts (
                    account_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    owner_subject TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS service_tokens (
                    token_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL REFERENCES service_accounts(account_id),
                    secret_sha256 TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    expires_at_utc TEXT NOT NULL,
                    revoked_at_utc TEXT,
                    rotated_to_token_id TEXT
                );
                """
            )

    def create_service_account(
        self,
        name: str,
        *,
        owner_subject: str,
        account_id: str | None = None,
    ) -> ServiceAccount:
        self.initialize()
        normalized_name = name.strip()
        normalized_owner = owner_subject.strip()
        if not normalized_name or not normalized_owner:
            raise ValueError("service account name and owner_subject are required")
        identifier = account_id or uuid.uuid4().hex
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO service_accounts VALUES(?,?,?,?,?)",
                (identifier, normalized_name, normalized_owner, 1, _utc_now().isoformat()),
            )
        return ServiceAccount(identifier, normalized_name, normalized_owner, True)

    def get_service_account(self, account_id: str) -> ServiceAccount | None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT account_id,name,owner_subject,enabled FROM service_accounts WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return ServiceAccount(str(row[0]), str(row[1]), str(row[2]), bool(row[3]))

    def disable_service_account(self, account_id: str) -> None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                "UPDATE service_accounts SET enabled=0 WHERE account_id=?", (account_id,)
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown service account {account_id!r}")

    def issue_token(
        self,
        account_id: str,
        *,
        scopes: tuple[str, ...],
        ttl: timedelta = timedelta(days=30),
        now: datetime | None = None,
    ) -> str:
        self.initialize()
        account = self.get_service_account(account_id)
        if account is None or not account.enabled:
            raise PermissionError("service account is unavailable")
        if ttl.total_seconds() <= 0:
            raise ValueError("service token ttl must be positive")
        normalized_scopes = _validate_scopes(scopes)
        issued_at = now or _utc_now()
        if issued_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        token_id = uuid.uuid4().hex
        secret = secrets.token_urlsafe(32)
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        expires_at = issued_at + ttl
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO service_tokens VALUES(?,?,?,?,?,?,NULL,NULL)",
                (
                    token_id,
                    account_id,
                    digest,
                    json.dumps(normalized_scopes, separators=(",", ":")),
                    issued_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
        return f"qpt_{token_id}.{secret}"

    def verify_token(self, token: str, *, now: datetime | None = None) -> ServiceCredential:
        self.initialize()
        try:
            public, secret = token.split(".", 1)
            if not public.startswith("qpt_") or not secret:
                raise ValueError
            token_id = public.removeprefix("qpt_")
        except ValueError as exc:
            raise PermissionError("invalid service token") from exc
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT t.secret_sha256,t.scopes_json,t.expires_at_utc,t.revoked_at_utc,
                a.account_id,a.name,a.enabled FROM service_tokens t
                JOIN service_accounts a ON a.account_id=t.account_id WHERE t.token_id=?""",
                (token_id,),
            ).fetchone()
        if row is None or not bool(row[6]) or row[3] is not None:
            raise PermissionError("invalid or revoked service token")
        digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, str(row[0])):
            raise PermissionError("invalid service token")
        current = now or _utc_now()
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if _parse_utc(str(row[2])) <= current:
            raise PermissionError("service token has expired")
        scopes = tuple(str(scope) for scope in json.loads(str(row[1])))
        account_id = str(row[4])
        return ServiceCredential(
            principal=Principal(
                subject=f"service:{account_id}",
                username=str(row[5]),
                roles=("service-account",),
            ),
            token_id=token_id,
            scopes=scopes,
            expires_at_utc=str(row[2]),
        )

    def revoke_token(self, token_id: str, *, now: datetime | None = None) -> None:
        self.initialize()
        revoked_at = now or _utc_now()
        if revoked_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        with sqlite3.connect(self.database) as connection:
            cursor = connection.execute(
                """UPDATE service_tokens SET revoked_at_utc=COALESCE(revoked_at_utc, ?)
                WHERE token_id=?""",
                (revoked_at.isoformat(), token_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown service token {token_id!r}")

    def rotate_token(
        self,
        token: str,
        *,
        ttl: timedelta = timedelta(days=30),
        now: datetime | None = None,
    ) -> str:
        current = now or _utc_now()
        credential = self.verify_token(token, now=current)
        account_id = credential.principal.subject.removeprefix("service:")
        replacement = self.issue_token(account_id, scopes=credential.scopes, ttl=ttl, now=current)
        replacement_id = replacement.split(".", 1)[0].removeprefix("qpt_")
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """UPDATE service_tokens SET revoked_at_utc=?,rotated_to_token_id=? WHERE token_id=?""",
                (current.isoformat(), replacement_id, credential.token_id),
            )
        return replacement
