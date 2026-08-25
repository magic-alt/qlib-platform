from __future__ import annotations

from pathlib import Path

import pytest

from tushare_qlib.auth import LocalAuthBackend


def test_local_auth_bootstraps_and_verifies_offline_session(tmp_path: Path):
    database = tmp_path / "auth" / "auth.sqlite"
    key = tmp_path / "secrets" / "jwt_hs256.key"
    backend = LocalAuthBackend(database, key)

    assert backend.status() == "uninitialized"
    admin = backend.bootstrap_admin("admin", "correct-horse-battery")
    authenticated = backend.authenticate("admin", "correct-horse-battery")
    token = backend.issue_token(authenticated)

    assert admin.roles == ("admin",)
    assert backend.status() == "ready"
    assert backend.verify(token) == authenticated
    assert backend.authorize(authenticated, "dataset:write") is True
    assert key.is_file() and len(key.read_bytes()) >= 32
    assert b"correct-horse-battery" not in database.read_bytes()


def test_local_auth_rejects_bad_password_token_and_second_bootstrap(tmp_path: Path):
    backend = LocalAuthBackend(tmp_path / "auth.sqlite", tmp_path / "jwt.key")
    backend.bootstrap_admin("admin", "correct-horse-battery")

    with pytest.raises(PermissionError, match="invalid credentials"):
        backend.authenticate("admin", "wrong-password")
    with pytest.raises(PermissionError, match="invalid token"):
        backend.verify("not.a.valid-token")
    with pytest.raises(ValueError, match="already bootstrapped"):
        backend.bootstrap_admin("other", "another-secure-password")


def test_local_auth_enforces_rbac(tmp_path: Path):
    backend = LocalAuthBackend(tmp_path / "auth.sqlite", tmp_path / "jwt.key")
    backend.initialize()
    viewer = backend.create_user("viewer", "viewer-secure-password", roles=("viewer",))

    assert backend.authorize(viewer, "dataset:read") is True
    assert backend.authorize(viewer, "dataset:write") is False
