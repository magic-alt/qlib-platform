from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from qlib_platform.auth.audit import TamperEvidentAuditLog
from qlib_platform.auth.backend import Principal
from qlib_platform.auth.policy import ResourceRef, ResearchAccessPolicy
from qlib_platform.research.evidence.experiment_store import ExperimentStore
from qlib_platform.research.management.store import ResearchGovernanceStore


class GovernedExperimentStore:
    """Authorization/audit façade over the existing ExperimentStore.

    It intentionally does not duplicate experiment persistence. Every operation delegates to the
    existing evidence store after the project/resource policy has admitted the caller.
    """

    def __init__(
        self,
        experiment_store: ExperimentStore,
        governance_store: ResearchGovernanceStore,
        audit_log: TamperEvidentAuditLog,
    ) -> None:
        self.experiments = experiment_store
        self.governance = governance_store
        self.audit = audit_log
        self.policy = ResearchAccessPolicy(governance_store)

    def _require(
        self,
        principal: Principal,
        resource: ResourceRef,
        permission: str,
        *,
        action: str,
    ) -> None:
        if self.policy.authorize(principal, resource, permission):
            return
        self.audit.append(
            actor_subject=principal.subject,
            action=action,
            resource_kind=resource.kind,
            resource_id=resource.resource_id,
            outcome="DENIED",
            metadata={"permission": permission},
        )
        raise PermissionError(
            f"{principal.subject!r} is not authorized for {permission!r} on "
            f"{resource.kind}:{resource.resource_id}"
        )

    def register_experiment(
        self,
        principal: Principal,
        project_id: str,
        experiment_id: str,
        **experiment_fields: Any,
    ) -> None:
        project = ResourceRef("project", project_id)
        self._require(principal, project, "write", action="experiment.register")
        self.experiments.register_experiment(experiment_id, **experiment_fields)
        self.governance.bind_resource(
            "experiment",
            experiment_id,
            project_id=project_id,
            owner_subject=principal.subject,
        )
        self.audit.append(
            actor_subject=principal.subject,
            action="experiment.register",
            resource_kind="experiment",
            resource_id=experiment_id,
            outcome="SUCCEEDED",
            metadata={"project_id": project_id},
        )

    def get_experiment(self, principal: Principal, experiment_id: str) -> dict[str, Any] | None:
        resource = ResourceRef("experiment", experiment_id)
        self._require(principal, resource, "read", action="experiment.read")
        result = self.experiments.get_experiment(experiment_id)
        self.audit.append(
            actor_subject=principal.subject,
            action="experiment.read",
            resource_kind="experiment",
            resource_id=experiment_id,
            outcome="SUCCEEDED" if result is not None else "NOT_FOUND",
        )
        return result

    def log_metrics(
        self,
        principal: Principal,
        experiment_id: str,
        metrics: Mapping[str, float],
        *,
        split: str = "oos",
        step: int = 0,
    ) -> None:
        resource = ResourceRef("experiment", experiment_id)
        self._require(principal, resource, "write", action="experiment.metrics.write")
        self.experiments.log_metrics(experiment_id, metrics, split=split, step=step)
        self.audit.append(
            actor_subject=principal.subject,
            action="experiment.metrics.write",
            resource_kind="experiment",
            resource_id=experiment_id,
            outcome="SUCCEEDED",
            metadata={"metric_count": len(metrics), "split": split, "step": step},
        )

    def register_artifact(
        self,
        principal: Principal,
        experiment_id: str,
        *,
        kind: str,
        uri: str,
        sha256: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        experiment = ResourceRef("experiment", experiment_id)
        self._require(principal, experiment, "write", action="artifact.register")
        project_id = self.governance.resource_project("experiment", experiment_id)
        if project_id is None:
            raise PermissionError("experiment is not bound to a governed project")
        self.experiments.register_artifact(
            experiment_id,
            kind=kind,
            uri=uri,
            sha256=sha256,
            metadata=metadata,
        )
        self.governance.bind_resource(
            "artifact",
            sha256,
            project_id=project_id,
            owner_subject=principal.subject,
        )
        self.audit.append(
            actor_subject=principal.subject,
            action="artifact.register",
            resource_kind="artifact",
            resource_id=sha256,
            outcome="SUCCEEDED",
            metadata={"experiment_id": experiment_id, "kind": kind},
        )

    def list_project_experiments(
        self,
        principal: Principal,
        project_id: str,
        *,
        limit: int = 200,
    ) -> pd.DataFrame:
        project = ResourceRef("project", project_id)
        self._require(principal, project, "read", action="project.experiments.list")
        frame = self.experiments.list_experiments(limit=limit)
        if frame.empty:
            result = frame
        else:
            mask = frame["experiment_id"].map(
                lambda item: self.governance.resource_project("experiment", str(item)) == project_id
            )
            result = frame.loc[mask].reset_index(drop=True)
        self.audit.append(
            actor_subject=principal.subject,
            action="project.experiments.list",
            resource_kind="project",
            resource_id=project_id,
            outcome="SUCCEEDED",
            metadata={"result_count": len(result)},
        )
        return result
