from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

from .backend import Principal


ROLE_PERMISSIONS = {
    "admin": {"*"},
    "operator": {"status:read", "dataset:write", "research:run"},
    "researcher": {"status:read", "dataset:read", "research:run"},
    "viewer": {"status:read", "dataset:read", "research:read"},
}


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class LocalAuthBackend:
    """Self-contained auth backend for a standalone multi-user API/UI deployment.

    Local CLI commands deliberately do not depend on this backend; OS and filesystem
    permissions remain authoritative for local-process access.
    """

    def __init__(self, database: str | Path, signing_key: str | Path):
        self.database = Path(database).expanduser().resolve()
        self.signing_key = Path(signing_key).expanduser().resolve()
        self.hasher = PasswordHasher(type=Type.ID)

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS roles (
                    role TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    role TEXT NOT NULL REFERENCES roles(role),
                    PRIMARY KEY (user_id, role)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    jti_sha256 TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at_utc TEXT NOT NULL,
                    revoked_at_utc TEXT
                );
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_sha256 TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at_utc TEXT NOT NULL,
                    revoked_at_utc TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT OR IGNORE INTO roles(role) VALUES(?)",
                [(role,) for role in ROLE_PERMISSIONS],
            )

    def status(self) -> str:
        if not self.database.is_file():
            return "uninitialized"
        try:
            uri = f"file:{self.database.as_posix()}?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                count = int(connection.execute("SELECT COUNT(*) FROM users WHERE enabled=1").fetchone()[0])
            return "ready" if count else "uninitialized"
        except sqlite3.Error:
            return "unavailable"

    def _key(self) -> bytes:
        if not self.signing_key.is_file():
            self.signing_key.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.signing_key.with_suffix(self.signing_key.suffix + ".tmp")
            temporary.write_bytes(secrets.token_bytes(32))
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.signing_key)
        value = self.signing_key.read_bytes()
        if len(value) < 32:
            raise ValueError("local auth signing key must contain at least 256 bits")
        return value

    def create_user(self, username: str, password: str, *, roles: tuple[str, ...]) -> Principal:
        self.initialize()
        normalized = username.strip()
        if not normalized or len(password) < 12:
            raise ValueError("username is required and password must contain at least 12 characters")
        unknown = set(roles) - set(ROLE_PERMISSIONS)
        if unknown:
            raise ValueError(f"unknown roles: {sorted(unknown)}")
        user_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO users VALUES(?,?,?,?,?)",
                (user_id, normalized, self.hasher.hash(password), 1, now),
            )
            connection.executemany(
                "INSERT INTO user_roles(user_id,role) VALUES(?,?)",
                [(user_id, role) for role in sorted(set(roles))],
            )
        return Principal(user_id, normalized, tuple(sorted(set(roles))))

    def bootstrap_admin(self, username: str, password: str) -> Principal:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise ValueError("local auth is already bootstrapped")
        self._key()
        return self.create_user(username, password, roles=("admin",))

    def list_users(self) -> list[Principal]:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute(
                """SELECT u.user_id,u.username,r.role FROM users u
                   LEFT JOIN user_roles ur ON ur.user_id=u.user_id
                   LEFT JOIN roles r ON r.role=ur.role
                   WHERE u.enabled=1 ORDER BY u.username,r.role"""
            ).fetchall()
        grouped: dict[tuple[str, str], list[str]] = {}
        for user_id, username, role in rows:
            grouped.setdefault((str(user_id), str(username)), [])
            if role:
                grouped[(str(user_id), str(username))].append(str(role))
        return [Principal(key[0], key[1], tuple(value)) for key, value in grouped.items()]

    def authenticate(self, username: str, password: str) -> Principal:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT user_id,username,password_hash,enabled FROM users WHERE username=?",
                (username.strip(),),
            ).fetchone()
        if row is None or not bool(row[3]):
            raise PermissionError("invalid credentials")
        try:
            self.hasher.verify(str(row[2]), password)
        except (VerifyMismatchError, InvalidHashError) as exc:
            raise PermissionError("invalid credentials") from exc
        return self._principal(str(row[0]), str(row[1]))

    def issue_token(self, principal: Principal, *, ttl: timedelta = timedelta(minutes=30)) -> str:
        self.initialize()
        now = datetime.now(timezone.utc)
        jti = uuid.uuid4().hex
        header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        claims = {
            "iss": "qlib-platform-local",
            "sub": principal.subject,
            "username": principal.username,
            "roles": list(principal.roles),
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            "jti": jti,
        }
        payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64(hmac.new(self._key(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT INTO sessions VALUES(?,?,?,NULL)",
                (hashlib.sha256(jti.encode()).hexdigest(), principal.subject, (now + ttl).isoformat()),
            )
        return f"{header}.{payload}.{signature}"

    def verify(self, token: str) -> Principal:
        try:
            header, payload, signature = token.split(".")
            expected = hmac.new(self._key(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _unb64(signature)):
                raise PermissionError("invalid token")
            claims = json.loads(_unb64(payload))
            now = datetime.now(timezone.utc)
            if claims.get("iss") != "qlib-platform-local" or int(claims["exp"]) <= int(now.timestamp()):
                raise PermissionError("expired or invalid token")
            jti_hash = hashlib.sha256(str(claims["jti"]).encode()).hexdigest()
            with sqlite3.connect(self.database) as connection:
                session = connection.execute(
                    "SELECT revoked_at_utc FROM sessions WHERE jti_sha256=?", (jti_hash,)
                ).fetchone()
            if session is None or session[0] is not None:
                raise PermissionError("invalid token session")
            return self._principal(str(claims["sub"]), str(claims["username"]))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid token") from exc

    def authorize(self, principal: Principal, permission: str) -> bool:
        granted = set().union(*(ROLE_PERMISSIONS.get(role, set()) for role in principal.roles))
        return "*" in granted or permission in granted

    def _principal(self, user_id: str, username: str) -> Principal:
        with sqlite3.connect(self.database) as connection:
            roles = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT role FROM user_roles WHERE user_id=? ORDER BY role", (user_id,)
                ).fetchall()
            )
        return Principal(user_id, username, roles)


def local_auth_backend(root: str | Path) -> LocalAuthBackend:
    data_root = Path(root).expanduser().resolve()
    return LocalAuthBackend(data_root / "auth" / "auth.sqlite", data_root / "secrets" / "jwt_hs256.key")
