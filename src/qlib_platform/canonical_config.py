from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from qlib_platform.models.model_runtime import ResolvedRuntime
from qlib_platform.backtesting.portfolio import PortfolioPolicy
from qlib_platform.research.research_gate import ResearchThresholds
from qlib_platform.settings import Settings
from qlib_platform.backtesting.topk_dropout import RankBufferPolicy, TopkDropoutPolicy


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    source: str
    universe_name: str
    membership_type: str
    secondary_filters: dict[str, object]

    @classmethod
    def from_settings(cls, settings: Settings) -> "DatasetSpec":
        qlib = _mapping(settings.data.get("qlib"))
        source_cfg = _mapping(settings.data.get("data_source"))
        mysql = _mapping(source_cfg.get("mysql"))
        platform_release = _mapping(source_cfg.get("platform_release"))
        universe = dict(_mapping(settings.data.get("universe")))
        manifest_path = settings.qlib_data_uri / "dataset_manifest.json"
        manifest: Mapping[str, Any] = {}
        semantic: Mapping[str, Any] = {}
        if manifest_path.is_file():
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = loaded if isinstance(loaded, Mapping) else {}
            raw_semantic = manifest.get("semantic_contract")
            semantic = raw_semantic if isinstance(raw_semantic, Mapping) else {}
        materialized_release = str(
            semantic.get("data_release_id") or manifest.get("data_release_id") or ""
        ).strip()
        materialized_version = str(manifest.get("version_id") or "").strip()
        materialized_source = str(semantic.get("source_type") or "").strip()
        source = (
            materialized_source
            if materialized_source
            else "platform_release"
            if settings.uses_platform_release()
            else "tushare"
            if settings.uses_tushare_source()
            else "lean_mysql"
        )
        configured = (
            platform_release.get("universe")
            if source == "platform_release"
            else mysql.get("universe")
            if source == "lean_mysql"
            else universe.get("instruments")
        )
        # Prefer the immutable upstream DataRelease when present. Standalone local
        # DatasetVersions may legitimately have no DataRelease lineage; in that case
        # bind research identity to the immutable manifest version_id rather than a
        # mutable/profile-level label such as qlib.dataset_version="local".
        dataset_id = (
            materialized_release
            or materialized_version
            or (platform_release.get("id") if source == "platform_release" else None)
        )
        name = str(universe.get("label") or configured or "all")
        membership_type = "point_in_time" if configured and str(configured).lower() != "all" else "filtered"
        return cls(
            dataset_id=str(dataset_id or qlib.get("dataset_version", settings.qlib_data_uri.name)),
            source=source,
            universe_name=name,
            membership_type=membership_type,
            secondary_filters=universe,
        )


@dataclass(frozen=True)
class ModelSpec:
    profile_name: str
    family: str
    requested_device: str
    resolved_device: str
    parameters: dict[str, object]

    @classmethod
    def from_runtime(
        cls, runtime: ResolvedRuntime, *, parameters: Mapping[str, object] | None = None
    ) -> "ModelSpec":
        return cls(
            profile_name=runtime.profile.name,
            family=runtime.profile.family,
            requested_device=runtime.profile.device,
            resolved_device=runtime.resolved_device,
            parameters=dict(parameters if parameters is not None else runtime.profile.model_kwargs),
        )


@dataclass(frozen=True)
class StrategySpec:
    policy: str = "topk_dropout_v1"
    topk: int = 30
    n_drop: int = 5
    hold_thresh: int = 5
    only_tradable: bool = True
    forbid_all_trade_at_limit: bool = True
    risk_degree: float = 0.95
    # Rank-buffer parameters; only meaningful when policy == "rank_buffer_v1".
    target_size: int = 10
    entry_rank: int = 10
    exit_rank: int = 20
    max_replacements: int = 3

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        topk_override: int | None = None,
        n_drop_override: int | None = None,
        hold_thresh_override: int | None = None,
    ) -> "StrategySpec":
        strategy = _mapping(settings.data.get("strategy"))
        policy = str(strategy.get("policy") or "topk_dropout_v1")
        if policy == "rank_buffer_v1":
            configured = _mapping(strategy.get("rank_buffer"))
            values = dict(configured)
            if topk_override is not None:
                values["target_size"] = topk_override
            if n_drop_override is not None:
                values["max_replacements"] = n_drop_override
            if hold_thresh_override is not None:
                values["hold_thresh"] = hold_thresh_override
            parsed = RankBufferPolicy.from_mapping(values)
            parsed.validate()
            return cls(policy=policy, **asdict(parsed))
        if policy != "topk_dropout_v1":
            raise ValueError(f"unknown strategy policy: {policy}")
        configured = _mapping(strategy.get("topk_dropout"))
        values = dict(configured)
        if topk_override is not None:
            values["topk"] = topk_override
        if n_drop_override is not None:
            values["n_drop"] = n_drop_override
        if hold_thresh_override is not None:
            values["hold_thresh"] = hold_thresh_override
        spec = cls(
            policy=policy,
            topk=int(values.get("topk", cls.topk)),
            n_drop=int(values.get("n_drop", cls.n_drop)),
            hold_thresh=int(values.get("hold_thresh", cls.hold_thresh)),
            only_tradable=bool(values.get("only_tradable", cls.only_tradable)),
            forbid_all_trade_at_limit=bool(
                values.get("forbid_all_trade_at_limit", cls.forbid_all_trade_at_limit)
            ),
            risk_degree=float(values.get("risk_degree", cls.risk_degree)),
        )
        spec.to_policy().validate()
        return spec

    def to_policy(self) -> TopkDropoutPolicy | RankBufferPolicy:
        if self.policy == "rank_buffer_v1":
            return RankBufferPolicy(
                target_size=self.target_size,
                entry_rank=self.entry_rank,
                exit_rank=self.exit_rank,
                max_replacements=self.max_replacements,
                hold_thresh=self.hold_thresh,
                only_tradable=self.only_tradable,
                forbid_all_trade_at_limit=self.forbid_all_trade_at_limit,
                risk_degree=self.risk_degree,
            )
        return TopkDropoutPolicy(
            topk=self.topk,
            n_drop=self.n_drop,
            hold_thresh=self.hold_thresh,
            only_tradable=self.only_tradable,
            forbid_all_trade_at_limit=self.forbid_all_trade_at_limit,
            risk_degree=self.risk_degree,
        )


@dataclass(frozen=True)
class PortfolioSpec:
    top_n: int = 20
    min_score: float | None = None
    weighting: str = "score_vol"
    max_position: float = 0.08
    max_exposure: float = 0.90
    max_group_exposure: float = 0.25
    max_turnover: float | None = 0.30
    min_position: float = 0.002
    volatility_floor: float = 0.01

    @classmethod
    def from_settings(cls, settings: Settings) -> "PortfolioSpec":
        data = _mapping(settings.data.get("portfolio"))
        policy = PortfolioPolicy.from_mapping(data)
        policy.validate()
        return cls(**asdict(policy))

    def to_policy(self) -> PortfolioPolicy:
        return PortfolioPolicy.from_mapping(asdict(self))


@dataclass(frozen=True)
class RiskSpec:
    max_gross_exposure: float = 0.95
    max_single_name: float = 0.10
    max_sector_exposure: float = 0.30
    max_daily_loss: float = 0.03
    kill_switch: bool = False
    exposure_overlay: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RiskSpec":
        data = _mapping(settings.data.get("risk"))
        values = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        spec = cls(**values)
        if not 0 < spec.max_gross_exposure <= 1:
            raise ValueError("risk.max_gross_exposure must be in (0, 1]")
        if not 0 < spec.max_single_name <= 1:
            raise ValueError("risk.max_single_name must be in (0, 1]")
        if not 0 < spec.max_sector_exposure <= 1:
            raise ValueError("risk.max_sector_exposure must be in (0, 1]")
        if not 0 < spec.max_daily_loss < 1:
            raise ValueError("risk.max_daily_loss must be in (0, 1)")
        from qlib_platform.backtesting.exposure_overlay import ExposureOverlayPolicy

        ExposureOverlayPolicy.from_mapping(spec.exposure_overlay)
        return spec


@dataclass(frozen=True)
class CanonicalConfig:
    dataset: DatasetSpec
    model: ModelSpec
    strategy: StrategySpec
    portfolio: PortfolioSpec
    risk: RiskSpec
    promotion: ResearchThresholds

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        runtime: ResolvedRuntime,
        *,
        topk_override: int | None = None,
        model_parameters: Mapping[str, object] | None = None,
    ) -> "CanonicalConfig":
        research = _mapping(settings.data.get("research"))
        return cls(
            dataset=DatasetSpec.from_settings(settings),
            model=ModelSpec.from_runtime(runtime, parameters=model_parameters),
            strategy=StrategySpec.from_settings(settings, topk_override=topk_override),
            portfolio=PortfolioSpec.from_settings(settings),
            risk=RiskSpec.from_settings(settings),
            promotion=ResearchThresholds.from_mapping(_mapping(research.get("promotion_thresholds"))),
        )

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)
