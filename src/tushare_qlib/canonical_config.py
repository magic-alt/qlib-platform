from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .model_runtime import ResolvedRuntime
from .research_gate import ResearchThresholds
from .settings import Settings
from .topk_dropout import TopkDropoutPolicy


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
        universe = dict(_mapping(settings.data.get("universe")))
        source = "tushare" if settings.uses_tushare_source() else "lean_mysql"
        configured = mysql.get("universe") if source == "lean_mysql" else universe.get("instruments")
        name = str(universe.get("label") or configured or "all")
        membership_type = "point_in_time" if source == "lean_mysql" and mysql.get("universe") else "filtered"
        return cls(
            dataset_id=str(qlib.get("dataset_version", settings.qlib_data_uri.name)),
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
    topk: int = 30
    n_drop: int = 5
    hold_thresh: int = 5
    only_tradable: bool = True
    forbid_all_trade_at_limit: bool = True
    risk_degree: float = 0.95

    @classmethod
    def from_settings(cls, settings: Settings, *, topk_override: int | None = None) -> "StrategySpec":
        strategy = _mapping(settings.data.get("strategy"))
        configured = _mapping(strategy.get("topk_dropout"))
        if not configured:
            # Compatibility is read-only: new manifests always serialize the canonical strategy section.
            configured = _mapping(_mapping(settings.data.get("execution")).get("topk_dropout"))
        values = dict(configured)
        if topk_override is not None:
            values["topk"] = topk_override
        spec = cls(
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

    def to_policy(self) -> TopkDropoutPolicy:
        return TopkDropoutPolicy(**asdict(self))


@dataclass(frozen=True)
class ExecutionSpec:
    board_lot: int = 100
    max_participation_rate: float = 0.05
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_duty_sell: float = 0.0005
    transfer_fee_rate: float = 0.00001
    price_buffer_buy: float = 0.002
    price_buffer_sell: float = 0.002
    block_limit_up_buy: bool = True
    block_limit_down_sell: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> "ExecutionSpec":
        data = _mapping(settings.data.get("execution"))
        values = {name: data[name] for name in cls.__dataclass_fields__ if name in data}
        return cls(**values)


@dataclass(frozen=True)
class RiskSpec:
    max_gross_exposure: float = 0.95
    max_single_name: float = 0.10
    max_sector_exposure: float = 0.30
    max_daily_loss: float = 0.03
    kill_switch: bool = False

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
        return spec


@dataclass(frozen=True)
class CanonicalConfig:
    dataset: DatasetSpec
    model: ModelSpec
    strategy: StrategySpec
    execution: ExecutionSpec
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
            execution=ExecutionSpec.from_settings(settings),
            risk=RiskSpec.from_settings(settings),
            promotion=ResearchThresholds.from_mapping(_mapping(research.get("promotion_thresholds"))),
        )

    def to_manifest(self) -> dict[str, object]:
        return asdict(self)
