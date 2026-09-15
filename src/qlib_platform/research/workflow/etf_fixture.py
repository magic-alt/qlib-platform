from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from qlib_platform.alpha.applicability import assert_alpha_pack_profile_compatible
from qlib_platform.alpha.registry import get_alpha_pack
from qlib_platform.data.etf_handler import ETF_CORE_FEATURE_NAMES
from qlib_platform.lineage import sha256_json
from qlib_platform.research.contracts.ashare_etf import (
    ETF_REQUIRED_DATASETS,
    EtfTradabilityObservation,
    EtfUniverseMember,
    assert_etf_data_contract,
    select_ashare_etf_universe,
)
from qlib_platform.research.contracts.research_profile import require_research_profile
from qlib_platform.research.diagnostics.features import (
    FeatureDiagnosticArtifacts,
    FeatureDiagnosticsSpec,
    build_feature_diagnostics,
)
from qlib_platform.research.features.taxonomy import FactorTaxonomy, FactorTaxonomyEntry


@dataclass(frozen=True)
class EtfBacktestCostSpec:
    buy_cost_bps: float = 3.0
    sell_cost_bps: float = 3.0
    slippage_bps: float = 1.0

    def __post_init__(self) -> None:
        if min(self.buy_cost_bps, self.sell_cost_bps, self.slippage_bps) < 0:
            raise ValueError("ETF backtest costs must be non-negative")


@dataclass(frozen=True)
class EtfCertificationSpec:
    topk: int = 3
    ridge_alpha: float = 1.0
    costs: EtfBacktestCostSpec = EtfBacktestCostSpec()
    diagnostics_min_cross_section: int = 3

    def __post_init__(self) -> None:
        if self.topk < 1:
            raise ValueError("ETF certification topk must be positive")
        if self.ridge_alpha <= 0:
            raise ValueError("ETF certification ridge_alpha must be positive")
        if self.diagnostics_min_cross_section < 2:
            raise ValueError("ETF diagnostics min cross section must be at least 2")


@dataclass(frozen=True)
class EtfCertificationResult:
    train_rows: int
    evaluation_rows: int
    coefficients: tuple[float, ...]
    intercept: float
    predictions: pd.DataFrame
    daily_backtest: pd.DataFrame
    diagnostics: FeatureDiagnosticArtifacts


def certify_ashare_etf_fixture(
    features: pd.DataFrame,
    labels: pd.DataFrame,
    members: Iterable[EtfUniverseMember],
    observations: Iterable[EtfTradabilityObservation],
    *,
    train_end: str | pd.Timestamp,
    spec: EtfCertificationSpec | None = None,
) -> EtfCertificationResult:
    """Execute the deterministic ETF train -> backtest -> diagnostics certification path."""

    resolved = spec or EtfCertificationSpec()
    profile = require_research_profile("ashare_etf_v1")
    pack = get_alpha_pack("etf_core_v1")
    assert_alpha_pack_profile_compatible(pack, profile)
    assert_etf_data_contract(ETF_REQUIRED_DATASETS)

    normalized_features = _normalize_features(features)
    normalized_labels = _normalize_labels(labels, normalized_features.index)
    member_list = tuple(members)
    observation_list = tuple(observations)
    eligible_index = _eligible_index(member_list, observation_list, normalized_features.index)
    eligible_features = normalized_features.loc[eligible_index]
    eligible_labels = normalized_labels.loc[eligible_index]

    split = pd.Timestamp(train_end).normalize()
    dates = pd.DatetimeIndex(eligible_features.index.get_level_values("datetime"))
    train_mask = dates <= split
    evaluation_mask = dates > split
    if int(train_mask.sum()) < 2:
        raise ValueError("ETF certification requires at least two eligible training rows")
    if not evaluation_mask.any():
        raise ValueError("ETF certification requires eligible out-of-sample rows")

    x_train = eligible_features.loc[train_mask].to_numpy(dtype=float)
    y_train = eligible_labels.loc[train_mask, "label"].to_numpy(dtype=float)
    coefficients, intercept = _fit_ridge(x_train, y_train, resolved.ridge_alpha)

    evaluation_features = eligible_features.loc[evaluation_mask]
    evaluation_labels = eligible_labels.loc[evaluation_mask]
    scores = evaluation_features.to_numpy(dtype=float) @ coefficients + intercept
    predictions = pd.DataFrame({"score": scores}, index=evaluation_features.index)
    daily_backtest = _backtest_topk(
        predictions,
        evaluation_labels,
        topk=resolved.topk,
        costs=resolved.costs,
    )
    diagnostics = build_feature_diagnostics(
        evaluation_features,
        evaluation_labels,
        _etf_taxonomy(),
        FeatureDiagnosticsSpec(
            min_cross_section=resolved.diagnostics_min_cross_section,
            rolling_sessions=3,
            short_rolling_sessions=2,
            quantiles=3,
        ),
        fold_assignments={
            pd.Timestamp(value): "etf_fixture_oos"
            for value in evaluation_features.index.get_level_values("datetime").unique()
        },
        hac_lag=1,
    )
    return EtfCertificationResult(
        train_rows=int(train_mask.sum()),
        evaluation_rows=int(evaluation_mask.sum()),
        coefficients=tuple(float(value) for value in coefficients),
        intercept=float(intercept),
        predictions=predictions,
        daily_backtest=daily_backtest,
        diagnostics=diagnostics,
    )


def _normalize_features(features: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("ETF fixture features require a datetime/instrument MultiIndex")
    if features.index.has_duplicates:
        raise ValueError("ETF fixture features contain duplicate datetime/instrument keys")
    expected = set(ETF_CORE_FEATURE_NAMES)
    actual = {str(column) for column in features.columns}
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        raise ValueError(
            f"ETF fixture feature contract mismatch: missing={missing}, unexpected={unexpected}"
        )
    frame = features.loc[:, list(ETF_CORE_FEATURE_NAMES)].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError("ETF fixture features must be finite")
    return frame.sort_index()


def _normalize_labels(labels: pd.DataFrame, expected_index: pd.MultiIndex) -> pd.DataFrame:
    if not isinstance(labels.index, pd.MultiIndex) or labels.index.names != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("ETF fixture labels require a datetime/instrument MultiIndex")
    if "label" not in labels or len(labels.columns) != 1:
        raise ValueError("ETF fixture labels must contain exactly one label column")
    if not labels.index.equals(expected_index):
        raise ValueError("ETF fixture labels must exactly align with feature keys")
    frame = labels[["label"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(frame.to_numpy(dtype=float)).all():
        raise ValueError("ETF fixture labels must be finite")
    return frame


def _eligible_index(
    members: tuple[EtfUniverseMember, ...],
    observations: tuple[EtfTradabilityObservation, ...],
    source_index: pd.MultiIndex,
) -> pd.MultiIndex:
    keys: list[tuple[pd.Timestamp, str]] = []
    for timestamp in pd.DatetimeIndex(source_index.get_level_values("datetime").unique()).sort_values():
        selection = select_ashare_etf_universe(
            members,
            observations,
            trading_date=pd.Timestamp(timestamp).date(),
        )
        qlib_aliases = {
            member.instrument.instrument_id: next(
                alias.symbol for alias in member.instrument.aliases if alias.provider == "qlib"
            )
            for member in members
        }
        for instrument_id in selection.eligible:
            key = (pd.Timestamp(timestamp), qlib_aliases[instrument_id])
            if key in source_index:
                keys.append(key)
    if not keys:
        raise ValueError("ETF universe contract produced no eligible research observations")
    return pd.MultiIndex.from_tuples(keys, names=["datetime", "instrument"])


def _fit_ridge(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    x_mean = x.mean(axis=0)
    y_mean = float(y.mean())
    centered_x = x - x_mean
    centered_y = y - y_mean
    gram = centered_x.T @ centered_x
    coefficients = np.linalg.solve(gram + alpha * np.eye(gram.shape[0]), centered_x.T @ centered_y)
    intercept = y_mean - float(x_mean @ coefficients)
    return np.asarray(coefficients, dtype=float), float(intercept)


def _backtest_topk(
    predictions: pd.DataFrame,
    labels: pd.DataFrame,
    *,
    topk: int,
    costs: EtfBacktestCostSpec,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    previous_weights: dict[str, float] = {}
    for timestamp in pd.DatetimeIndex(predictions.index.get_level_values("datetime").unique()).sort_values():
        day_scores = predictions.xs(timestamp, level="datetime")["score"].sort_values(ascending=False)
        selected = tuple(str(value) for value in day_scores.head(topk).index)
        if not selected:
            raise ValueError(f"ETF backtest has no tradable selection on {timestamp.date()}")
        weight = 1.0 / len(selected)
        current_weights = {instrument: weight for instrument in selected}
        instruments = set(previous_weights) | set(current_weights)
        buy_turnover = sum(
            max(current_weights.get(instrument, 0.0) - previous_weights.get(instrument, 0.0), 0.0)
            for instrument in instruments
        )
        sell_turnover = sum(
            max(previous_weights.get(instrument, 0.0) - current_weights.get(instrument, 0.0), 0.0)
            for instrument in instruments
        )
        realized = labels.xs(timestamp, level="datetime")["label"].reindex(selected)
        if realized.isna().any():
            raise ValueError(f"ETF backtest is missing realized labels on {timestamp.date()}")
        gross_return = float(realized.mean())
        transaction_cost = (
            buy_turnover * costs.buy_cost_bps
            + sell_turnover * costs.sell_cost_bps
            + (buy_turnover + sell_turnover) * costs.slippage_bps
        ) / 10_000.0
        rows.append(
            {
                "date": pd.Timestamp(timestamp).normalize(),
                "holding_count": len(selected),
                "buy_turnover": buy_turnover,
                "sell_turnover": sell_turnover,
                "gross_return": gross_return,
                "transaction_cost": transaction_cost,
                "net_return": gross_return - transaction_cost,
            }
        )
        previous_weights = current_weights
    return pd.DataFrame(rows).set_index("date")


def _etf_taxonomy() -> FactorTaxonomy:
    entries: dict[str, FactorTaxonomyEntry] = {}
    for name in ETF_CORE_FEATURE_NAMES:
        if name.startswith("VOL_") or name == "RANGE_TO_CLOSE":
            family = "Volatility"
        elif "MONEY" in name or "VOLUME" in name:
            family = "Liquidity"
        else:
            family = "Momentum"
        entries[name] = FactorTaxonomyEntry(name, family, "alpha", "unknown")
    semantic = {
        "taxonomyId": "ashare_etf_core_v1",
        "alphaPackId": "etf_core_v1",
        "features": list(ETF_CORE_FEATURE_NAMES),
    }
    digest = sha256_json(semantic)
    return FactorTaxonomy("ashare_etf_core_v1", "etf_core_v1", entries, digest, digest)
