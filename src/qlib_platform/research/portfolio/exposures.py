from __future__ import annotations

import pandas as pd


def _standardize_style(style: pd.DataFrame) -> pd.DataFrame:
    result = style.apply(pd.to_numeric, errors="coerce").astype(float)
    if result.isna().any().any():
        raise ValueError("style exposures contain missing or non-numeric values")
    for column in result.columns:
        values = result[column]
        std = float(values.std(ddof=0))
        if std <= 1e-12:
            raise ValueError(f"style exposure {column!r} is constant")
        result[column] = (values - float(values.mean())) / std
    return result


def build_exposure_matrix(
    style_exposures: pd.DataFrame,
    industries: pd.Series | pd.DataFrame,
) -> pd.DataFrame:
    if style_exposures.index.has_duplicates:
        raise ValueError("style exposure index contains duplicates")
    style = _standardize_style(style_exposures.copy())
    style.columns = [f"STYLE_{str(column).upper()}" for column in style.columns]
    if isinstance(industries, pd.Series):
        if industries.index.has_duplicates or not industries.index.equals(style.index):
            raise ValueError("industry exposure index must exactly match style exposures")
        if industries.isna().any():
            raise ValueError("industry exposure contains missing values")
        industry = pd.get_dummies(industries.astype(str), prefix="IND", dtype=float)
        if industry.shape[1] > 1:
            industry = industry.iloc[:, 1:]
    else:
        if industries.index.has_duplicates or not industries.index.equals(style.index):
            raise ValueError("industry exposure index must exactly match style exposures")
        industry = industries.apply(pd.to_numeric, errors="coerce").astype(float)
        if industry.isna().any().any():
            raise ValueError("industry exposure matrix contains missing or non-numeric values")
        industry.columns = [f"IND_{str(column).upper()}" for column in industry.columns]
    return pd.concat([style, industry], axis=1)
