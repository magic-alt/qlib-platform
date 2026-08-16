from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .alpha.base import AlphaPackSpec
from .canonical_config import CanonicalConfig
from .lineage import sha256_json
from .model_runtime import ResolvedRuntime
from .research_timing import LabelSpec
from .settings import Settings


@dataclass(frozen=True)
class SplitSpec:
    profile_id: str
    run_kind: str
    segments: dict[str, tuple[str, str]]

    @property
    def fingerprint(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class ResearchExperimentSpec:
    data_release_id: str
    alpha_pack_id: str
    alpha_pack_sha256: str
    feature_set_id: str
    feature_set_sha256: str
    label_spec_id: str
    label: dict[str, object]
    split_profile_id: str
    split_sha256: str
    model_profile_id: str
    model_profile_sha256: str
    portfolio_policy_id: str
    portfolio_policy_sha256: str
    benchmark: str
    hypothesis_id: str | None = None
    hypothesis_role: str | None = None
    hypothesis_definition_sha256: str | None = None
    hypothesis_binding_sha256: str | None = None

    @property
    def experiment_id(self) -> str:
        return "exp_" + sha256_json({key: value for key, value in asdict(self).items() if value is not None})

    def to_manifest(self) -> dict[str, Any]:
        payload = {key: value for key, value in asdict(self).items() if value is not None}
        return {**payload, "experiment_id": self.experiment_id}

    def hypothesis_manifest(self) -> dict[str, str] | None:
        if self.hypothesis_id is None:
            return None
        return {
            "hypothesisId": self.hypothesis_id,
            "role": str(self.hypothesis_role),
            "hypothesisDefinitionSha256": str(self.hypothesis_definition_sha256),
            "hypothesisBindingSha256": str(self.hypothesis_binding_sha256),
        }

    @classmethod
    def resolve(
        cls,
        settings: Settings,
        *,
        runtime: ResolvedRuntime,
        canonical: CanonicalConfig,
        alpha_pack: AlphaPackSpec,
        label_spec: LabelSpec,
        train: tuple[str, str],
        valid: tuple[str, str],
        test: tuple[str, str],
        run_kind: str,
        benchmark: str,
    ) -> "ResearchExperimentSpec":
        experiment = settings.data.get("experiment", {})
        experiment = experiment if isinstance(experiment, Mapping) else {}
        configured_release = str(experiment.get("data_release") or "").strip()
        if configured_release and configured_release != canonical.dataset.dataset_id:
            raise ValueError("experiment.data_release does not match the pinned DataRelease")
        split_config = experiment.get("split", {})
        split_config = split_config if isinstance(split_config, Mapping) else {}
        default_split = (
            "wf_1500_126_63_v1"
            if run_kind in {"walk_forward", "walk_forward_fold", "final_holdout"}
            else "fixed_split_v1"
        )
        split = SplitSpec(
            profile_id=str(split_config.get("profile") or default_split),
            run_kind=run_kind,
            segments={"train": train, "valid": valid, "test": test},
        )
        portfolio_config = experiment.get("portfolio", {})
        portfolio_config = portfolio_config if isinstance(portfolio_config, Mapping) else {}
        portfolio_policy_id = str(portfolio_config.get("policy") or "topk_dropout_v1")
        if portfolio_policy_id != "topk_dropout_v1":
            raise ValueError(f"unknown portfolio policy: {portfolio_policy_id}")
        alpha_config = experiment.get("alpha", {})
        alpha_config = alpha_config if isinstance(alpha_config, Mapping) else {}
        feature_set_id = str(alpha_config.get("feature_set") or alpha_pack.pack_id)
        hypothesis_config = experiment.get("phase2_hypothesis", {})
        hypothesis_config = hypothesis_config if isinstance(hypothesis_config, Mapping) else {}
        hypothesis_id = str(hypothesis_config.get("hypothesisId") or "").strip() or None
        hypothesis_role = str(hypothesis_config.get("role") or "").strip() or None
        hypothesis_definition_sha256 = (
            str(hypothesis_config.get("hypothesisDefinitionSha256") or "").strip() or None
        )
        hypothesis_binding_sha256 = (
            str(hypothesis_config.get("hypothesisBindingSha256") or "").strip() or None
        )
        if alpha_pack.processor_recipe == "phase2_feature_set_v1":
            from .research.phase2_features import feature_set

            feature_set_sha256 = feature_set(feature_set_id).fingerprint
        else:
            feature_set_sha256 = alpha_pack.fingerprint
        if hypothesis_id is not None:
            required = {
                "hypothesis_role": hypothesis_role,
                "hypothesis_definition_sha256": hypothesis_definition_sha256,
                "hypothesis_binding_sha256": hypothesis_binding_sha256,
            }
            if missing := sorted(key for key, value in required.items() if not value):
                raise ValueError(f"Phase 2 hypothesis binding is incomplete: {missing}")
            if alpha_pack.processor_recipe != "phase2_feature_set_v1":
                raise ValueError("Phase 2 hypothesis runs require the Phase 2 alpha pack")
            from .research.phase2_hypotheses import hypothesis_feature_set

            expected = hypothesis_feature_set(hypothesis_id, str(hypothesis_role))
            if feature_set_id != expected.feature_set_id:
                raise ValueError("Phase 2 hypothesis feature-set binding drift")
            if runtime.profile.family != "ridge":
                raise ValueError("formal Phase 2 hypothesis runs require Ridge")
        return cls(
            data_release_id=canonical.dataset.dataset_id,
            alpha_pack_id=alpha_pack.pack_id,
            alpha_pack_sha256=alpha_pack.fingerprint,
            feature_set_id=feature_set_id,
            feature_set_sha256=feature_set_sha256,
            label_spec_id=label_spec.spec_id,
            label=label_spec.to_manifest(),
            split_profile_id=split.profile_id,
            split_sha256=split.fingerprint,
            model_profile_id=runtime.profile.name,
            model_profile_sha256=runtime.profile.fingerprint,
            portfolio_policy_id=portfolio_policy_id,
            portfolio_policy_sha256=sha256_json(canonical.strategy.__dict__),
            benchmark=benchmark,
            hypothesis_id=hypothesis_id,
            hypothesis_role=hypothesis_role,
            hypothesis_definition_sha256=hypothesis_definition_sha256,
            hypothesis_binding_sha256=hypothesis_binding_sha256,
        )
