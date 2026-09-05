from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class StressScenario:
    """First-order asset/factor shock definition for research stress testing."""

    name: str
    factor_shocks: Mapping[str, float] = field(default_factory=dict)
    asset_shocks: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StressResult:
    scenario: str
    portfolio_return: float
    benchmark_return: float | None
    active_return: float | None
    factor_return: float
    asset_specific_return: float
    instrument_shocks: pd.Series


def _validate_weights(weights: pd.Series, instruments: pd.Index, *, name: str) -> np.ndarray:
    if weights.index.has_duplicates or not weights.index.equals(instruments):
        raise ValueError(f"{name} instruments must exactly match scenario instruments and order")
    numeric = pd.to_numeric(weights, errors="coerce")
    values: np.ndarray = np.asarray(numeric, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError(f"{name} must contain only finite values")
    return values


def _validate_scenario(scenario: StressScenario) -> None:
    if not scenario.name.strip():
        raise ValueError("stress scenario name is required")
    for collection_name, values in {
        "factor_shocks": scenario.factor_shocks,
        "asset_shocks": scenario.asset_shocks,
    }.items():
        for key, value in values.items():
            if not str(key).strip() or not np.isfinite(float(value)):
                raise ValueError(f"{collection_name} must contain named finite shocks")


def evaluate_stress_scenario(
    weights: pd.Series,
    scenario: StressScenario,
    *,
    factor_exposures: pd.DataFrame | None = None,
    benchmark_weights: pd.Series | None = None,
) -> StressResult:
    """Evaluate a deterministic linear stress scenario.

    Instrument shock = explicit asset shock + exposure-weighted factor shock.
    Unknown instruments/factors fail closed instead of being silently ignored.
    """

    _validate_scenario(scenario)
    if factor_exposures is not None:
        instruments = factor_exposures.index
        if instruments.has_duplicates or factor_exposures.columns.has_duplicates:
            raise ValueError("factor exposure instruments and factors must be unique")
        exposure_values = factor_exposures.to_numpy(dtype=float)
        if not np.isfinite(exposure_values).all():
            raise ValueError("factor exposures must contain only finite values")
    else:
        instruments = weights.index
        exposure_values = None

    weight_values = _validate_weights(weights, instruments, name="weights")
    benchmark_values = (
        _validate_weights(benchmark_weights, instruments, name="benchmark_weights")
        if benchmark_weights is not None
        else None
    )

    unknown_assets = set(scenario.asset_shocks) - set(instruments.astype(str))
    if unknown_assets:
        raise ValueError(f"stress scenario references unknown instruments: {sorted(unknown_assets)}")

    factor_component = np.zeros(len(instruments), dtype=float)
    if scenario.factor_shocks:
        if factor_exposures is None:
            raise ValueError("factor shocks require factor_exposures")
        unknown_factors = set(scenario.factor_shocks) - set(factor_exposures.columns.astype(str))
        if unknown_factors:
            raise ValueError(f"stress scenario references unknown factors: {sorted(unknown_factors)}")
        factor_shock_vector = np.array(
            [float(scenario.factor_shocks.get(str(factor), 0.0)) for factor in factor_exposures.columns],
            dtype=float,
        )
        factor_component = exposure_values @ factor_shock_vector

    asset_component = np.array(
        [float(scenario.asset_shocks.get(str(instrument), 0.0)) for instrument in instruments],
        dtype=float,
    )
    total_shock = factor_component + asset_component
    portfolio_return = float(weight_values @ total_shock)
    factor_return = float(weight_values @ factor_component)
    asset_specific_return = float(weight_values @ asset_component)

    if benchmark_values is None:
        benchmark_return = None
        active_return = None
    else:
        benchmark_return = float(benchmark_values @ total_shock)
        active_return = portfolio_return - benchmark_return

    return StressResult(
        scenario=scenario.name,
        portfolio_return=portfolio_return,
        benchmark_return=benchmark_return,
        active_return=active_return,
        factor_return=factor_return,
        asset_specific_return=asset_specific_return,
        instrument_shocks=pd.Series(total_shock, index=instruments, name="stress_return"),
    )


def evaluate_stress_suite(
    weights: pd.Series,
    scenarios: Sequence[StressScenario],
    *,
    factor_exposures: pd.DataFrame | None = None,
    benchmark_weights: pd.Series | None = None,
) -> list[StressResult]:
    names = [scenario.name for scenario in scenarios]
    if len(set(names)) != len(names):
        raise ValueError("stress scenario names must be unique")
    return [
        evaluate_stress_scenario(
            weights,
            scenario,
            factor_exposures=factor_exposures,
            benchmark_weights=benchmark_weights,
        )
        for scenario in scenarios
    ]
