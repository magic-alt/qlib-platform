from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from qlib_platform.auth.policy import RESEARCH_PERMISSIONS, RESEARCH_RESOURCE_KINDS

_PROJECT_ROLES = frozenset({"owner", "maintainer", "researcher", "viewer"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


@dataclass(frozen=True)
class ResearchProject:
    project_id: str
    display_name: str
    owner_subject: str
    created_at_utc: str


class ResearchGovernanceStore:
    """Durable project ownership, membership, resource binding and explicit grants."""

    def __init__(self, database: str | Path):
        self.database = Path(database).expanduser().resolve()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_projects (
                    project_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    owner_subject TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_members (
                    project_id TEXT NOT NULL REFERENCES research_projects(project_id) ON DELETE CASCADE,
                    subject TEXT NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY(project_id, subject)
                );
                CREATE TABLE IF NOT EXISTS resource_bindings (
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES research_projects(project_id) ON DELETE CASCADE,
                    owner_subject TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(resource_kind, resource_id)
                );
                CREATE TABLE IF NOT EXISTS resource_grants (
                    subject TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    PRIMARY KEY(subject, resource_kind, resource_id, permission)
                );
                """
            )

    def create_project(
        self,
        project_id: str,
        *,
        display_name: str,
        owner_subject: str,
        created_at_utc: str | None = None,
    ) -> ResearchProject:
        self.initialize()
        identifier = _required(project_id, "project_id")
        name = _required(display_name, "display_name")
        owner = _required(owner_subject, "owner_subject")
        created = created_at_utc or _utc_now()
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "INSERT INTO research_projects VALUES(?,?,?,?)", (identifier, name, owner, created)
            )
            connection.execute("INSERT INTO project_members VALUES(?,?,?)", (identifier, owner, "owner"))
        return ResearchProject(identifier, name, owner, created)

    def get_project(self, project_id: str) -> ResearchProject | None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT project_id,display_name,owner_subject,created_at_utc
                FROM research_projects WHERE project_id=?""",
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ResearchProject(str(row[0]), str(row[1]), str(row[2]), str(row[3]))

    def add_member(self, project_id: str, subject: str, role: str) -> None:
        self.initialize()
        normalized_role = role.strip().lower()
        if normalized_role not in _PROJECT_ROLES:
            raise ValueError(f"unsupported project role {role!r}")
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown research project {project_id!r}")
        normalized_subject = _required(subject, "subject")
        if normalized_role == "owner" and normalized_subject != project.owner_subject:
            raise ValueError("use transfer_ownership to change project owner")
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """INSERT INTO project_members VALUES(?,?,?)
                ON CONFLICT(project_id,subject) DO UPDATE SET role=excluded.role""",
                (project_id, normalized_subject, normalized_role),
            )

    def remove_member(self, project_id: str, subject: str) -> None:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown research project {project_id!r}")
        if project.owner_subject == subject:
            raise ValueError("project owner cannot be removed")
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "DELETE FROM project_members WHERE project_id=? AND subject=?", (project_id, subject)
            )

    def transfer_ownership(self, project_id: str, new_owner_subject: str) -> None:
        project = self.get_project(project_id)
        if project is None:
            raise KeyError(f"unknown research project {project_id!r}")
        new_owner = _required(new_owner_subject, "new_owner_subject")
        with sqlite3.connect(self.database) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE research_projects SET owner_subject=? WHERE project_id=?",
                (new_owner, project_id),
            )
            connection.execute(
                """INSERT INTO project_members VALUES(?,?,?)
                ON CONFLICT(project_id,subject) DO UPDATE SET role=excluded.role""",
                (project_id, new_owner, "owner"),
            )
            if project.owner_subject != new_owner:
                connection.execute(
                    "UPDATE project_members SET role='maintainer' WHERE project_id=? AND subject=?",
                    (project_id, project.owner_subject),
                )

    def project_role(self, project_id: str, subject: str) -> str | None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT role FROM project_members WHERE project_id=? AND subject=?",
                (project_id, subject),
            ).fetchone()
        return str(row[0]) if row else None

    def bind_resource(
        self,
        resource_kind: str,
        resource_id: str,
        *,
        project_id: str,
        owner_subject: str,
    ) -> None:
        self.initialize()
        if resource_kind not in RESEARCH_RESOURCE_KINDS - {"project"}:
            raise ValueError(f"unsupported bindable resource kind {resource_kind!r}")
        identifier = _required(resource_id, "resource_id")
        owner = _required(owner_subject, "owner_subject")
        if self.get_project(project_id) is None:
            raise KeyError(f"unknown research project {project_id!r}")
        with sqlite3.connect(self.database) as connection:
            existing = connection.execute(
                "SELECT project_id FROM resource_bindings WHERE resource_kind=? AND resource_id=?",
                (resource_kind, identifier),
            ).fetchone()
            if existing is not None and str(existing[0]) != project_id:
                raise ValueError(
                    f"resource {resource_kind}:{identifier} is already bound to project {existing[0]}"
                )
            connection.execute(
                """INSERT INTO resource_bindings VALUES(?,?,?,?,?)
                ON CONFLICT(resource_kind,resource_id) DO UPDATE SET
                owner_subject=excluded.owner_subject""",
                (resource_kind, identifier, project_id, owner, _utc_now()),
            )

    def resource_project(self, resource_kind: str, resource_id: str) -> str | None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                "SELECT project_id FROM resource_bindings WHERE resource_kind=? AND resource_id=?",
                (resource_kind, resource_id),
            ).fetchone()
        return str(row[0]) if row else None

    def grant(
        self,
        subject: str,
        resource_kind: str,
        resource_id: str,
        permission: str,
    ) -> None:
        self.initialize()
        if resource_kind not in RESEARCH_RESOURCE_KINDS:
            raise ValueError(f"unsupported governed resource kind {resource_kind!r}")
        normalized_permission = permission.strip().lower()
        if normalized_permission not in RESEARCH_PERMISSIONS:
            raise ValueError(f"unsupported research permission {permission!r}")
        normalized_subject = _required(subject, "subject")
        identifier = _required(resource_id, "resource_id")
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO resource_grants VALUES(?,?,?,?,?)",
                (
                    normalized_subject,
                    resource_kind,
                    identifier,
                    normalized_permission,
                    _utc_now(),
                ),
            )

    def revoke_grant(
        self,
        subject: str,
        resource_kind: str,
        resource_id: str,
        permission: str,
    ) -> None:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """DELETE FROM resource_grants
                WHERE subject=? AND resource_kind=? AND resource_id=? AND permission=?""",
                (subject, resource_kind, resource_id, permission.strip().lower()),
            )

    def has_direct_grant(
        self,
        subject: str,
        resource_kind: str,
        resource_id: str,
        permission: str,
    ) -> bool:
        self.initialize()
        with sqlite3.connect(self.database) as connection:
            row = connection.execute(
                """SELECT 1 FROM resource_grants
                WHERE subject=? AND resource_kind=? AND resource_id=? AND permission=? LIMIT 1""",
                (subject, resource_kind, resource_id, permission),
            ).fetchone()
        return row is not None
