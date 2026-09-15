from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qlib_platform.alpha.registry import get_alpha_pack
from qlib_platform.research.contracts.research_profile import (
    InstrumentSpec,
    SymbolAlias,
    require_research_profile,
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload).hexdigest()


def _normalized_scalar(value: object) -> str | None:
    if pd.isna(value):
        return None
    return format(float(value), ".12g")


def _frame_payload(frame: pd.DataFrame) -> dict[str, object]:
    ordered = frame.sort_index()
    return {
        "index": [
            [pd.Timestamp(timestamp).strftime("%Y-%m-%d"), str(instrument)]
            for timestamp, instrument in ordered.index
        ],
        "columns": [str(column) for column in ordered.columns],
        "values": [
            [_normalized_scalar(value) for value in row]
            for row in ordered.to_numpy()
        ],
    }


def _legacy_fixture_output_sha256() -> str:
    dates = pd.bdate_range("2026-01-05", periods=30)
    instruments = ["SH600000", "SZ000001"]
    index = pd.MultiIndex.from_product(
        [dates, instruments],
        names=["datetime", "instrument"],
    )
    feature_rows: list[list[np.float32]] = []
    label_rows: list[np.float32] = []
    for day_number in range(len(dates)):
        for offset, _instrument in enumerate(instruments):
            close = (
                10.0
                + offset
                + np.arange(len(dates), dtype=float) * (0.1 + offset * 0.02)
            ).astype("<f4")
            feature_rows.append(
                [
                    close[day_number],
                    np.float32(0.0),
                    np.float32(500.0),
                    np.float32(5_000_000_000.0),
                    np.float32(50_000_000.0),
                    np.float32(0.0),
                ]
            )
            if day_number + 7 < len(dates):
                label_rows.append(
                    np.float32(
                        close[day_number + 7] / close[day_number + 1] - np.float32(1.0)
                    )
                )
            else:
                label_rows.append(np.float32(np.nan))

    features = pd.DataFrame(
        feature_rows,
        index=index,
        columns=[
            "CLOSE",
            "PAUSED",
            "LISTED_DAYS",
            "CIRC_MV",
            "MONEY20",
            "IS_ST",
        ],
    )
    labels = pd.DataFrame({"LABEL0": label_rows}, index=index)
    payload = {
        "fixture": "tests/test_label_spec.py::_mini_qlib_provider",
        "features": _frame_payload(features),
        "labels": _frame_payload(labels),
        "dates": [value.strftime("%Y-%m-%d") for value in dates],
        "splits": {
            "train": (str(dates[0].date()), str(dates[9].date())),
            "valid": (str(dates[10].date()), str(dates[14].date())),
            "test": (str(dates[15].date()), str(dates[-1].date())),
        },
        "labelSpec": {
            "horizonDays": 5,
            "signalLagDays": 2,
            "specId": "return_5d_t2_v1",
            "expression": "Ref($close, -7)/Ref($close, -1) - 1",
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _compatibility_golden() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    path = (
        root
        / "tests"
        / "fixtures"
        / "research_profile"
        / "ashare_equity_v1_pre_profile_golden.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_legacy_ashare_fixture_and_pack_identities_are_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    golden = _compatibility_golden()

    assert golden["sourceCommit"] == "9efc465c3556a642bba68368f7cc9cfc2d69a34b"
    for relative_path, expected_blob in golden["sourceFiles"].items():
        assert _git_blob_sha(root / relative_path) == expected_blob

    for pack_id, expected_fingerprint in golden["legacyAlphaPackFingerprints"].items():
        assert get_alpha_pack(pack_id).fingerprint == expected_fingerprint

    assert golden["defaultAlphaPack"] == "alpha158_pit_v1"
    assert _legacy_fixture_output_sha256() == golden["legacyFixture"]["outputSha256"]


def test_legacy_ashare_default_semantics_remain_research_only() -> None:
    expected = _compatibility_golden()["defaultResearchSemantics"]
    profile = require_research_profile("ashare_equity_v1")

    assert profile.asset_class == expected["assetClass"]
    assert profile.subtype == expected["subtype"]
    assert profile.market == expected["market"]
    assert profile.currency == expected["currency"]
    assert profile.calendar.calendar_id == expected["calendarId"]
    assert profile.frequency == expected["frequency"]
    assert profile.adjustment == expected["adjustment"]
    assert profile.price_field == expected["priceField"]
    assert profile.label.label_id == expected["labelId"]
    assert profile.label.horizon_sessions == expected["labelHorizonSessions"]
    assert profile.label.execution_lag_sessions == expected["labelExecutionLagSessions"]
    assert list(profile.allowed_workflows) == expected["allowedWorkflows"]
    assert profile.promotion_scope == expected["promotionScope"] == "research_only"


def test_derivative_handoff_distinguishes_research_series_from_tradable_contracts() -> None:
    continuous = InstrumentSpec(
        instrument_id="CN.CFFEX.FUTURE.IF.CONTINUOUS",
        asset_class="future",
        subtype="continuous_future",
        market="CN",
        venue="CFFEX",
        currency="CNY",
        calendar_id="cn_future_fixture_v1",
        aliases=(SymbolAlias("research", "IF.CONTINUOUS"),),
        tradable=True,
        continuous_series=True,
    )
    with pytest.raises(ValueError, match="continuous research series"):
        continuous.assert_governed_handoff_ready()

    incomplete_contract = InstrumentSpec(
        instrument_id="CN.CFFEX.FUTURE.IF.202612",
        asset_class="future",
        subtype="index_future",
        market="CN",
        venue="CFFEX",
        currency="CNY",
        calendar_id="cn_future_fixture_v1",
        tradable=True,
        aliases=(SymbolAlias("fixture", "IF2612"),),
        underlying_id="CN.INDEX.CSI300",
        expiry="2026-12-18",
        multiplier=None,
    )
    with pytest.raises(ValueError, match="multiplier"):
        incomplete_contract.assert_governed_handoff_ready()
