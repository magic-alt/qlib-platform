from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

from qlib_platform.lineage import sha256_json
from qlib_platform.data.store import sha256_file


REGIME_SCHEMA = "ashare_regime_v1"
REQUIRED_DIMENSIONS = (
    "market_trend",
    "market_volatility",
    "market_activity",
    "size_style",
    "industry_breadth",
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed < 1:
        raise ValueError(f"{name} must be positive")
    return parsed


@dataclass(frozen=True)
class RegimeSpec:
    regime_id: str
    minimum_sessions: int
    hac_lag: int
    fdr_alpha: float
    topk: int
    stable_features: tuple[str, ...]
    hypothesis_features: tuple[str, ...]
    composites: Mapping[str, tuple[str, ...]]
    dimensions: Mapping[str, Mapping[str, Any]]
    semantic_sha256: str
    file_sha256: str

    @property
    def diagnostic_features(self) -> tuple[str, ...]:
        return self.stable_features + self.hypothesis_features

    def to_manifest(self) -> dict[str, object]:
        return {
            "schema": REGIME_SCHEMA,
            "regimeId": self.regime_id,
            "minimumSessions": self.minimum_sessions,
            "hacLag": self.hac_lag,
            "fdrAlpha": self.fdr_alpha,
            "topK": self.topk,
            "stableFeatures": list(self.stable_features),
            "hypothesisFeatures": list(self.hypothesis_features),
            "composites": {key: list(value) for key, value in sorted(self.composites.items())},
            "dimensions": {key: dict(value) for key, value in sorted(self.dimensions.items())},
            "semanticSha256": self.semantic_sha256,
            "fileSha256": self.file_sha256,
        }


def load_regime_spec(path: str | Path) -> RegimeSpec:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"regime config is missing: {source}")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw = _mapping(payload, "regime config")
    if raw.get("schema") != REGIME_SCHEMA:
        raise ValueError(f"unsupported regime schema: {raw.get('schema')}")
    regime_id = str(raw.get("regimeId") or "").strip()
    if not regime_id:
        raise ValueError("regimeId is required")
    minimum_sessions = _positive_int(raw.get("minimumSessions"), "minimumSessions")
    hac_lag = _positive_int(raw.get("hacLag"), "hacLag")
    topk = _positive_int(raw.get("topK"), "topK")
    fdr_alpha = float(str(raw.get("fdrAlpha")))
    if not 0 < fdr_alpha < 1:
        raise ValueError("fdrAlpha must be in (0, 1)")

    features = _mapping(raw.get("features"), "features")
    stable = tuple(str(value) for value in features.get("stable", ()))
    hypotheses = tuple(str(value) for value in features.get("hypothesisOnly", ()))
    if not stable or len(set(stable + hypotheses)) != len(stable + hypotheses):
        raise ValueError("regime feature lists must be non-empty and unique")

    raw_composites = _mapping(raw.get("composites"), "composites")
    composites = {key: tuple(str(value) for value in values) for key, values in raw_composites.items()}
    if set(composites) != {"value", "low_vol"} or any(not values for values in composites.values()):
        raise ValueError("composites must define non-empty value and low_vol groups")
    if any(value not in stable for values in composites.values() for value in values):
        raise ValueError("composite features must belong to the stable feature list")

    raw_dimensions = _mapping(raw.get("dimensions"), "dimensions")
    if set(raw_dimensions) != set(REQUIRED_DIMENSIONS):
        raise ValueError(f"regime dimensions must be exactly {list(REQUIRED_DIMENSIONS)}")
    dimensions = {key: dict(_mapping(value, f"dimension {key}")) for key, value in raw_dimensions.items()}
    for name, definition in dimensions.items():
        classifier = str(definition.get("classifier") or "")
        states = definition.get("states")
        if classifier not in {"symmetric_threshold", "expanding_quantiles"}:
            raise ValueError(f"unsupported classifier for {name}: {classifier}")
        if not isinstance(states, list) or len(states) != 3 or len(set(states)) != 3:
            raise ValueError(f"{name} must declare exactly three unique states")
        if classifier == "symmetric_threshold":
            if float(str(definition.get("threshold"))) <= 0:
                raise ValueError(f"{name} threshold must be positive")
        else:
            quantiles = definition.get("quantiles")
            if not isinstance(quantiles, list) or len(quantiles) != 2:
                raise ValueError(f"{name} quantiles must contain two values")
            lower, upper = (float(str(value)) for value in quantiles)
            if not 0 < lower < upper < 1:
                raise ValueError(f"{name} quantiles must satisfy 0 < lower < upper < 1")
            _positive_int(definition.get("minHistory"), f"{name}.minHistory")

    semantic = {
        "schema": REGIME_SCHEMA,
        "regimeId": regime_id,
        "minimumSessions": minimum_sessions,
        "hacLag": hac_lag,
        "fdrAlpha": fdr_alpha,
        "topK": topk,
        "features": {"stable": list(stable), "hypothesisOnly": list(hypotheses)},
        "composites": {key: list(value) for key, value in sorted(composites.items())},
        "dimensions": {key: dimensions[key] for key in sorted(dimensions)},
    }
    return RegimeSpec(
        regime_id=regime_id,
        minimum_sessions=minimum_sessions,
        hac_lag=hac_lag,
        fdr_alpha=fdr_alpha,
        topk=topk,
        stable_features=stable,
        hypothesis_features=hypotheses,
        composites=composites,
        dimensions=dimensions,
        semantic_sha256=sha256_json(semantic),
        file_sha256=sha256_file(source),
    )


def _expanding_quantile_states(
    score: pd.Series,
    *,
    quantiles: tuple[float, float],
    min_history: int,
    states: tuple[str, str, str],
) -> pd.DataFrame:
    ordered = pd.to_numeric(score, errors="coerce").sort_index()
    causal_history = ordered.shift(1)
    lower = causal_history.expanding(min_periods=min_history).quantile(quantiles[0])
    upper = causal_history.expanding(min_periods=min_history).quantile(quantiles[1])
    state = pd.Series("INSUFFICIENT_HISTORY", index=ordered.index, dtype="object")
    valid = ordered.notna() & lower.notna() & upper.notna() & upper.gt(lower)
    state.loc[valid & ordered.le(lower)] = states[0]
    state.loc[valid & ordered.gt(lower) & ordered.lt(upper)] = states[1]
    state.loc[valid & ordered.ge(upper)] = states[2]
    state.loc[ordered.isna()] = "INPUT_UNAVAILABLE"
    return pd.DataFrame(
        {"score": ordered, "lower_threshold": lower, "upper_threshold": upper, "state": state}
    )


def _symmetric_threshold_states(
    score: pd.Series,
    *,
    threshold: float,
    states: tuple[str, str, str],
) -> pd.DataFrame:
    ordered = pd.to_numeric(score, errors="coerce").sort_index()
    state = pd.Series(states[1], index=ordered.index, dtype="object")
    state.loc[ordered.le(-threshold)] = states[0]
    state.loc[ordered.ge(threshold)] = states[2]
    state.loc[ordered.isna()] = "INSUFFICIENT_HISTORY"
    return pd.DataFrame(
        {
            "score": ordered,
            "lower_threshold": -float(threshold),
            "upper_threshold": float(threshold),
            "state": state,
        }
    )


def _classify(score: pd.Series, definition: Mapping[str, Any]) -> pd.DataFrame:
    raw_states = tuple(str(value) for value in definition["states"])
    states = (raw_states[0], raw_states[1], raw_states[2])
    classifier = str(definition["classifier"])
    if classifier == "symmetric_threshold":
        return _symmetric_threshold_states(
            score,
            threshold=float(str(definition["threshold"])),
            states=states,
        )
    raw_quantiles = tuple(float(str(value)) for value in definition["quantiles"])
    quantiles = (raw_quantiles[0], raw_quantiles[1])
    return _expanding_quantile_states(
        score,
        quantiles=quantiles,
        min_history=int(str(definition["minHistory"])),
        states=states,
    )


def _market_trend(benchmark_close: pd.Series, definition: Mapping[str, Any]) -> pd.Series:
    close = pd.to_numeric(benchmark_close, errors="coerce").sort_index()
    window = _positive_int(definition.get("window"), "market_trend.window")
    return close.div(close.rolling(window, min_periods=window).mean()).sub(1.0)


def _market_volatility(benchmark_close: pd.Series, definition: Mapping[str, Any]) -> pd.Series:
    close = pd.to_numeric(benchmark_close, errors="coerce").sort_index()
    window = _positive_int(definition.get("window"), "market_volatility.window")
    annualization = float(str(definition.get("annualization", 252)))
    return (
        close.pct_change(fill_method=None)
        .rolling(window, min_periods=window)
        .std(ddof=1)
        .mul(np.sqrt(annualization))
    )


def _market_activity(features: pd.DataFrame, definition: Mapping[str, Any]) -> pd.Series:
    field = str(definition.get("field") or "TURNOVER_F")
    if field not in features:
        return pd.Series(dtype=float)
    values = pd.to_numeric(features[field], errors="coerce")
    return values.groupby(level="datetime", sort=True).median()


def _size_style(
    features: pd.DataFrame,
    stock_returns: pd.Series,
    definition: Mapping[str, Any],
) -> pd.Series:
    field = str(definition.get("sizeField") or "LOG_CIRC_MV")
    if field not in features or stock_returns.empty:
        return pd.Series(dtype=float)
    quantile = float(str(definition.get("basketQuantile", 0.30)))
    if not 0 < quantile < 0.5:
        raise ValueError("size_style.basketQuantile must be in (0, 0.5)")
    lag = _positive_int(definition.get("bucketLagSessions", 1), "size_style.bucketLagSessions")
    window = _positive_int(definition.get("window"), "size_style.window")
    size = pd.to_numeric(features[field], errors="coerce").sort_index()
    size = size.groupby(level="instrument", sort=False).shift(lag)
    paired = pd.concat([size.rename("size"), stock_returns.rename("return")], axis=1).dropna()
    rows: list[tuple[pd.Timestamp, float]] = []
    for date, block in paired.groupby(level="datetime", sort=True):
        if len(block) < 20 or block["size"].nunique() < 3:
            rows.append((pd.Timestamp(date).normalize(), float("nan")))
            continue
        lower = float(block["size"].quantile(quantile))
        upper = float(block["size"].quantile(1.0 - quantile))
        small = block.loc[block["size"].le(lower), "return"].mean()
        large = block.loc[block["size"].ge(upper), "return"].mean()
        rows.append((pd.Timestamp(date).normalize(), float(small - large)))
    daily = pd.Series(dict(rows), dtype=float).sort_index()
    return daily.rolling(window, min_periods=window).sum()


def _industry_breadth(
    stock_returns: pd.Series,
    industries: pd.Series,
    definition: Mapping[str, Any],
) -> pd.Series:
    if stock_returns.empty or industries.empty:
        return pd.Series(dtype=float)
    window = _positive_int(definition.get("window", 20), "industry_breadth.window")
    minimum = _positive_int(definition.get("minimumIndustries", 10), "minimumIndustries")
    paired = pd.concat([stock_returns.rename("return"), industries.rename("industry")], axis=1).dropna()
    daily_industry = paired.groupby(["datetime", "industry"], sort=True)["return"].mean()
    count = daily_industry.groupby(level="datetime").count()
    breadth = daily_industry.gt(0).groupby(level="datetime").mean().where(count.ge(minimum))
    return breadth.rolling(window, min_periods=window).mean()


def build_regime_labels(
    spec: RegimeSpec,
    *,
    benchmark_close: pd.Series,
    features: pd.DataFrame,
    stock_returns: pd.Series,
    industries: pd.Series | None,
    evaluation_dates: pd.DatetimeIndex,
) -> pd.DataFrame:
    if not isinstance(features.index, pd.MultiIndex) or features.index.names != [
        "datetime",
        "instrument",
    ]:
        raise ValueError("regime features require a datetime/instrument MultiIndex")
    if not stock_returns.empty and (
        not isinstance(stock_returns.index, pd.MultiIndex)
        or stock_returns.index.names != ["datetime", "instrument"]
    ):
        raise ValueError("stock returns require a datetime/instrument MultiIndex")
    normalized_dates = pd.DatetimeIndex(evaluation_dates).normalize().sort_values().unique()
    definitions = spec.dimensions
    scores = {
        "market_trend": _market_trend(benchmark_close, definitions["market_trend"]),
        "market_volatility": _market_volatility(benchmark_close, definitions["market_volatility"]),
        "market_activity": _market_activity(features, definitions["market_activity"]),
        "size_style": _size_style(features, stock_returns, definitions["size_style"]),
        "industry_breadth": _industry_breadth(
            stock_returns,
            industries if industries is not None else pd.Series(dtype=float),
            definitions["industry_breadth"],
        ),
    }
    rows: list[pd.DataFrame] = []
    for dimension in REQUIRED_DIMENSIONS:
        score = scores[dimension]
        if score.empty:
            result = pd.DataFrame(
                {
                    "score": np.nan,
                    "lower_threshold": np.nan,
                    "upper_threshold": np.nan,
                    "state": "INPUT_UNAVAILABLE",
                },
                index=normalized_dates,
            )
        else:
            result = _classify(score, definitions[dimension]).reindex(normalized_dates)
            result["state"] = result["state"].fillna("INPUT_UNAVAILABLE")
        result.index.name = "date"
        result = result.reset_index()
        result.insert(1, "dimension", dimension)
        result["status"] = np.where(
            result["state"].isin(["INPUT_UNAVAILABLE", "INSUFFICIENT_HISTORY"]),
            result["state"],
            "AVAILABLE",
        )
        previous = result["state"].shift(1)
        result["transition"] = previous.notna() & result["state"].ne(previous)
        rows.append(result)
    return (
        pd.concat(rows, ignore_index=True)
        .sort_values(["date", "dimension"], kind="stable")
        .reset_index(drop=True)
    )
