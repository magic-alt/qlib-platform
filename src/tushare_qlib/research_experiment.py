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
    label_spec_id: str
    label: dict[str, object]
    split_profile_id: str
    split_sha256: str
    model_profile_id: str
    model_profile_sha256: str
    portfolio_policy_id: str
    portfolio_policy_sha256: str
    benchmark: str

    @property
    def experiment_id(self) -> str:
        return "exp_" + sha256_json(asdict(self))

    def to_manifest(self) -> dict[str, Any]:
        return {**asdict(self), "experiment_id": self.experiment_id}

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
        return cls(
            data_release_id=canonical.dataset.dataset_id,
            alpha_pack_id=alpha_pack.pack_id,
            alpha_pack_sha256=alpha_pack.fingerprint,
            label_spec_id=label_spec.spec_id,
            label=label_spec.to_manifest(),
            split_profile_id=split.profile_id,
            split_sha256=split.fingerprint,
            model_profile_id=runtime.profile.name,
            model_profile_sha256=runtime.profile.fingerprint,
            portfolio_policy_id=portfolio_policy_id,
            portfolio_policy_sha256=sha256_json(canonical.strategy.__dict__),
            benchmark=benchmark,
        )
