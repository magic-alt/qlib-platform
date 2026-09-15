from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from qlib_platform.alpha.applicability import alpha_pack_applicability
from qlib_platform.alpha.registry import get_alpha_pack, handler_class
from qlib_platform.data.etf_handler import (
    ETF_CORE_FEATURE_EXPRESSIONS,
    ETF_CORE_FEATURE_NAMES,
    AshareEtfCore,
)
from qlib_platform.data.etf_materialization import ETF_QLIB_FIELDS, materialize_etf_qlib_fields
from qlib_platform.data.sources.base import FetchResult
from qlib_platform.data.sources.semantic import DatasetRequest, require_usable
from qlib_platform.data.sources.tushare_semantic import TushareSemanticDataSource
from qlib_platform.research.contracts.ashare_etf import (
    ETF_FORBIDDEN_STOCK_DATASETS,
    ETF_REQUIRED_DATASETS,
    EtfTradabilityObservation,
    EtfUniverseMember,
    assert_etf_data_contract,
    select_ashare_etf_universe,
)
from qlib_platform.research.contracts.research_profile import (
    ashare_etf_instrument,
    legacy_ashare_instrument,
)
from qlib_platform.research.workflow.etf_fixture import (
    EtfBacktestCostSpec,
    EtfCertificationSpec,
    certify_ashare_etf_fixture,
)


class _StubClient:
    def __init__(self, results: dict[str, FetchResult | list[FetchResult]]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> FetchResult:
        self.calls.append((api_name, {"fields": fields, "required": required, **params}))
        result = self.results[api_name]
        if isinstance(result, list):
            if not result:
                raise AssertionError(f"no recorded result left for {api_name}")
            return result.pop(0)
        return result

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def _etf_daily_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["510300.SH", "159915.SZ"],
            "trade_date": ["20260910", "20260910"],
            "open": [4.0, 2.0],
            "high": [4.1, 2.1],
            "low": [3.9, 1.9],
            "close": [4.05, 2.05],
            "pre_close": [4.0, 2.0],
            "change": [0.05, 0.05],
            "pct_chg": [1.25, 2.5],
            "vol": [1000.0, 2000.0],
            "amount": [405.0, 410.0],
        }
    )


def test_etf_data_contract_rejects_stock_only_dependencies() -> None:
    assert_etf_data_contract(ETF_REQUIRED_DATASETS)

    with pytest.raises(ValueError, match="must not borrow stock-only datasets"):
        assert_etf_data_contract((*ETF_REQUIRED_DATASETS, "equity_daily_basic"))
    with pytest.raises(ValueError, match="data contract is incomplete"):
        assert_etf_data_contract(tuple(kind for kind in ETF_REQUIRED_DATASETS if kind != "etf_daily"))

    assert "equity_daily_basic" in ETF_FORBIDDEN_STOCK_DATASETS
    assert "pit_fundamentals" in ETF_FORBIDDEN_STOCK_DATASETS


def test_etf_universe_is_listing_and_tradability_aware_and_fail_closed() -> None:
    trading_date = date(2026, 9, 10)
    eligible = EtfUniverseMember(ashare_etf_instrument("510300.SH"), date(2012, 5, 28))
    suspended = EtfUniverseMember(ashare_etf_instrument("510500.SH"), date(2013, 3, 15))
    zero_volume = EtfUniverseMember(ashare_etf_instrument("159915.SZ"), date(2011, 12, 9))
    delisted = EtfUniverseMember(
        ashare_etf_instrument("159999.SZ"),
        date(2020, 1, 1),
        date(2026, 9, 9),
    )
    missing_observation = EtfUniverseMember(ashare_etf_instrument("512000.SH"), date(2017, 8, 30))
    not_tradable = EtfUniverseMember(
        replace(ashare_etf_instrument("512010.SH"), tradable=False),
        date(2017, 9, 1),
    )

    observations = [
        EtfTradabilityObservation(eligible.instrument.instrument_id, trading_date, 1000.0),
        EtfTradabilityObservation(suspended.instrument.instrument_id, trading_date, 1000.0, True),
        EtfTradabilityObservation(zero_volume.instrument.instrument_id, trading_date, 0.0),
        EtfTradabilityObservation(delisted.instrument.instrument_id, trading_date, 1000.0),
        EtfTradabilityObservation(not_tradable.instrument.instrument_id, trading_date, 1000.0),
    ]
    selection = select_ashare_etf_universe(
        [eligible, suspended, zero_volume, delisted, missing_observation, not_tradable],
        observations,
        trading_date=trading_date,
    )

    assert selection.eligible == (eligible.instrument.instrument_id,)
    assert selection.excluded[suspended.instrument.instrument_id] == "suspended"
    assert selection.excluded[zero_volume.instrument.instrument_id] == "non_positive_volume"
    assert selection.excluded[delisted.instrument.instrument_id] == "outside_listing_window"
    assert selection.excluded[missing_observation.instrument.instrument_id] == "missing_tradability_observation"
    assert selection.excluded[not_tradable.instrument.instrument_id] == "instrument_not_tradable"

    with pytest.raises(ValueError, match="equity/etf"):
        EtfUniverseMember(legacy_ashare_instrument("SH600000"), date(2000, 1, 1))
    with pytest.raises(ValueError, match="must not precede"):
        EtfUniverseMember(ashare_etf_instrument("510300.SH"), date(2020, 1, 2), date(2020, 1, 1))
    with pytest.raises(ValueError, match="must not be negative"):
        EtfTradabilityObservation(eligible.instrument.instrument_id, trading_date, -1.0)


def test_tushare_etf_semantic_datasets_use_fund_endpoints_only() -> None:
    raw = _etf_daily_raw()
    client = _StubClient(
        {
            "fund_daily": FetchResult(raw, "success", 1),
            "fund_adj": FetchResult(
                pd.DataFrame(
                    {
                        "ts_code": ["510300.SH", "159915.SZ"],
                        "trade_date": ["20260910", "20260910"],
                        "adj_factor": [1.2, 0.8],
                    }
                ),
                "success",
                1,
            ),
        }
    )
    source = TushareSemanticDataSource(client)

    daily_request = DatasetRequest("etf_daily", start="2026-09-10", end="2026-09-10")
    daily = require_usable(source.fetch_dataset(daily_request), daily_request)
    adjustment_request = DatasetRequest(
        "etf_adjustment_factor",
        start="2026-09-10",
        end="2026-09-10",
    )
    adjustment = require_usable(source.fetch_dataset(adjustment_request), adjustment_request)

    assert daily.data["instrument"].tolist() == ["SH510300", "SZ159915"]
    assert daily.data["volume"].tolist() == [100_000.0, 200_000.0]
    assert daily.data["turnover"].tolist() == [405_000.0, 410_000.0]
    assert adjustment.data["adjustment_factor"].tolist() == [1.2, 0.8]
    assert [name for name, _ in client.calls] == ["fund_daily", "fund_adj"]
    assert all(name not in {"daily", "daily_basic", "adj_factor"} for name, _ in client.calls)


def test_tushare_etf_master_combines_etf_metadata_with_fund_lifecycle() -> None:
    listed = pd.DataFrame(
        {
            "ts_code": ["510300.SH"],
            "csname": ["沪深300ETF"],
            "extname": ["沪深300ETF"],
            "cname": ["沪深300ETF"],
            "index_code": ["000300.SH"],
            "index_name": ["沪深300"],
            "setup_date": ["20120504"],
            "list_date": ["20120528"],
            "list_status": ["L"],
            "exchange": ["SH"],
            "mgr_name": ["fixture"],
            "custod_name": ["fixture"],
            "mgt_fee": [0.5],
            "etf_type": ["股票型"],
        }
    )
    delisted = listed.copy()
    delisted.loc[:, "ts_code"] = "159999.SZ"
    delisted.loc[:, "list_status"] = "D"
    delisted.loc[:, "exchange"] = "SZ"
    lifecycle = pd.DataFrame(
        {
            "ts_code": ["510300.SH", "159999.SZ"],
            "name": ["沪深300ETF", "历史ETF"],
            "list_date": ["20120528", "20200102"],
            "delist_date": [None, "20260909"],
            "status": ["L", "D"],
            "market": ["E", "E"],
        }
    )
    client = _StubClient(
        {
            "etf_basic": [FetchResult(listed, "success", 1), FetchResult(delisted, "success", 1)],
            "fund_basic": FetchResult(lifecycle, "success", 1),
        }
    )
    source = TushareSemanticDataSource(client)
    request = DatasetRequest("etf_instrument_master")

    batch = require_usable(source.fetch_dataset(request), request)

    assert batch.data["instrument"].tolist() == ["SH510300", "SZ159999"]
    historical = batch.data.set_index("instrument").loc["SZ159999"]
    assert historical["delist_date"] == "20260909"
    assert [name for name, _ in client.calls] == ["etf_basic", "etf_basic", "fund_basic"]
    assert client.calls[-1][1]["market"] == "E"
    assert all(name != "stock_basic" for name, _ in client.calls)


def test_etf_master_fails_closed_when_lifecycle_evidence_is_missing() -> None:
    metadata = pd.DataFrame(
        {
            "ts_code": ["510300.SH"],
            "csname": ["fixture"],
            "extname": ["fixture"],
            "cname": ["fixture"],
            "index_code": ["000300.SH"],
            "index_name": ["fixture"],
            "setup_date": ["20120504"],
            "list_date": ["20120528"],
            "list_status": ["L"],
            "exchange": ["SH"],
            "mgr_name": ["fixture"],
            "custod_name": ["fixture"],
            "mgt_fee": [0.5],
            "etf_type": ["股票型"],
        }
    )
    unrelated_lifecycle = pd.DataFrame(
        {
            "ts_code": ["159915.SZ"],
            "name": ["fixture"],
            "list_date": ["20111209"],
            "delist_date": [None],
            "status": ["L"],
            "market": ["E"],
        }
    )
    source = TushareSemanticDataSource(
        _StubClient(
            {
                "etf_basic": [
                    FetchResult(metadata, "success", 1),
                    FetchResult(pd.DataFrame(columns=metadata.columns), "empty", 1),
                ],
                "fund_basic": FetchResult(unrelated_lifecycle, "success", 1),
            }
        )
    )

    envelope = source.fetch_dataset(DatasetRequest("etf_instrument_master"))

    assert envelope.status == "incomplete"
    assert envelope.error_class == "missing_etf_lifecycle"


def test_etf_materialization_produces_qlib_price_volume_contract_without_stock_fields() -> None:
    daily = pd.DataFrame(
        {
            "instrument": ["SH510300", "SH510300"],
            "trading_date": ["2026-09-09", "2026-09-10"],
            "open": [4.0, 4.1],
            "high": [4.1, 4.2],
            "low": [3.9, 4.0],
            "close": [4.0, 4.2],
            "volume": [100_000.0, 120_000.0],
            "turnover": [400_000.0, 504_000.0],
        }
    )
    adjustment = pd.DataFrame(
        {
            "instrument": ["SH510300", "SH510300"],
            "trading_date": ["2026-09-09", "2026-09-10"],
            "adjustment_factor": [1.0, 1.1],
        }
    )

    materialized = materialize_etf_qlib_fields(daily, adjustment)

    assert set(ETF_QLIB_FIELDS).issubset(materialized.columns)
    assert materialized.loc[0, "factor"] == pytest.approx(0.25)
    assert materialized.loc[1, "factor"] == pytest.approx(0.275)
    assert materialized.loc[0, "money"] == pytest.approx(400_000.0)
    assert materialized.loc[0, "vwap"] == pytest.approx(1.0)
    assert "pe_ttm" not in materialized
    assert "pb" not in materialized

    with pytest.raises(ValueError, match="adjustment factor is missing"):
        materialize_etf_qlib_fields(daily, adjustment.iloc[[0]])
    with pytest.raises(ValueError, match="duplicate"):
        materialize_etf_qlib_fields(pd.concat([daily, daily.iloc[[0]]]), adjustment)


def test_etf_pack_is_explicit_and_contains_no_stock_fundamental_dependencies() -> None:
    pack = get_alpha_pack("etf_core_v1")
    descriptor = alpha_pack_applicability(pack)
    resolved_handler = handler_class(pack)

    assert resolved_handler is AshareEtfCore
    assert descriptor.required_dataset_kinds == ETF_REQUIRED_DATASETS
    assert descriptor.supported_subtypes == ("etf",)
    assert descriptor.pit_required is False
    assert set(pack.required_release_components) == {"etf_daily", "etf_adjustment_factor"}
    forbidden_fields = {
        "turnover_rate_f",
        "circ_mv",
        "pe_ttm",
        "pb",
        "ps_ttm",
        "dv_ttm",
        "industry_l1_code",
    }
    assert forbidden_fields.isdisjoint(pack.required_qlib_fields)
    expressions = " ".join(ETF_CORE_FEATURE_EXPRESSIONS).lower()
    assert all(token not in expressions for token in ("$pe", "$pb", "$circ_mv", "$roe", "$total_assets"))


def _fixture_panel() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    list[EtfUniverseMember],
    list[EtfTradabilityObservation],
]:
    dates = pd.bdate_range("2026-08-24", periods=12)
    symbols = ["SH510300", "SH510500", "SH512000", "SZ159915", "SZ159919", "SZ159922"]
    index = pd.MultiIndex.from_product([dates, symbols], names=["datetime", "instrument"])
    day = np.repeat(np.arange(len(dates), dtype=float), len(symbols))
    cross = np.tile(np.arange(len(symbols), dtype=float), len(dates))
    base = 0.01 * day + 0.02 * cross
    features = pd.DataFrame(index=index)
    for number, name in enumerate(ETF_CORE_FEATURE_NAMES, start=1):
        features[name] = base * number + (number * 0.001)
    labels = pd.DataFrame(
        {"label": 0.6 * features["RET_5"] + 0.2 * features["MONEY_RATIO_20"]},
        index=index,
    )
    realized_returns = pd.DataFrame(
        {"return": 0.1 * features["RET_1"] + 0.02 * cross},
        index=index,
    )
    members = [
        EtfUniverseMember(ashare_etf_instrument(symbol), date(2020, 1, 1)) for symbol in symbols
    ]
    id_by_symbol = {
        next(alias.symbol for alias in member.instrument.aliases if alias.provider == "qlib"): member.instrument.instrument_id
        for member in members
    }
    observations: list[EtfTradabilityObservation] = []
    for timestamp in dates:
        for symbol in symbols:
            suspended = timestamp == dates[8] and symbol == "SH510500"
            volume = 0.0 if timestamp == dates[9] and symbol == "SZ159915" else 100_000.0
            observations.append(
                EtfTradabilityObservation(
                    id_by_symbol[symbol],
                    pd.Timestamp(timestamp).date(),
                    volume,
                    suspended,
                )
            )
    return features, labels, realized_returns, members, observations


def test_etf_fixture_executes_train_daily_backtest_and_existing_feature_diagnostics() -> None:
    features, labels, realized_returns, members, observations = _fixture_panel()
    dates = pd.DatetimeIndex(features.index.get_level_values("datetime").unique())

    result = certify_ashare_etf_fixture(
        features,
        labels,
        realized_returns,
        members,
        observations,
        train_end=dates[5],
        spec=EtfCertificationSpec(topk=2, diagnostics_min_cross_section=3),
    )

    assert result.train_rows > 0
    assert result.evaluation_rows > 0
    assert len(result.coefficients) == len(ETF_CORE_FEATURE_NAMES)
    assert not result.predictions.empty
    assert not result.daily_backtest.empty
    assert (result.daily_backtest["transaction_cost"] >= 0).all()
    assert result.daily_backtest.iloc[0]["transaction_cost"] > 0
    assert (result.daily_backtest["net_return"] <= result.daily_backtest["gross_return"]).all()
    assert set(result.diagnostics.summary["feature"]) == set(ETF_CORE_FEATURE_NAMES)
    assert not result.diagnostics.daily.empty
    assert (dates[8], "SH510500") not in result.predictions.index
    assert (dates[9], "SZ159915") not in result.predictions.index


def test_etf_fixture_rejects_stock_style_features_and_invalid_costs() -> None:
    features, labels, realized_returns, members, observations = _fixture_panel()
    bad = features.drop(columns=[ETF_CORE_FEATURE_NAMES[0]]).copy()
    bad["PE_TTM"] = 1.0

    with pytest.raises(ValueError, match="feature contract mismatch"):
        certify_ashare_etf_fixture(
            bad,
            labels,
            realized_returns,
            members,
            observations,
            train_end=pd.Timestamp("2026-08-31"),
        )
    with pytest.raises(ValueError, match="costs must be non-negative"):
        EtfBacktestCostSpec(buy_cost_bps=-1.0)


def test_etf_fixture_rejects_misaligned_realized_return_contract() -> None:
    features, labels, realized_returns, members, observations = _fixture_panel()
    misaligned = realized_returns.iloc[:-1]

    with pytest.raises(ValueError, match="realized returns.*exactly align"):
        certify_ashare_etf_fixture(
            features,
            labels,
            misaligned,
            members,
            observations,
            train_end=pd.Timestamp("2026-08-31"),
        )


def test_governed_etf_config_freezes_research_only_boundaries() -> None:
    path = Path(__file__).resolve().parents[1] / "configs" / "research" / "ashare_etf_v1.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert payload["schema"] == "ashare_etf_research_fixture_v1"
    assert payload["researchProfile"] == "ashare_etf_v1"
    assert payload["alphaPack"] == "etf_core_v1"
    assert tuple(payload["data"]["requiredDatasetKinds"]) == ETF_REQUIRED_DATASETS
    assert tuple(payload["data"]["forbiddenDatasetKinds"]) == ETF_FORBIDDEN_STOCK_DATASETS
    assert payload["backtest"]["realizedReturnSpec"] == "return_1d_t1_v1"
    assert payload["backtest"]["stockStampTaxInherited"] is False
    assert payload["promotionScope"] == "research_only"
    assert payload["publishingAuthorized"] is False
    assert payload["executionAuthority"] == "none"
    assert payload["finalHoldout"]["accessAllowed"] is False
