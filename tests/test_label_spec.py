from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from qlib.contrib.data.handler import check_transform_proc
from qlib.data.dataset.handler import DataHandlerLP

from qlib_platform.alpha.registry import get_alpha_pack
from qlib_platform.feature_store import prepare_feature_data
from qlib_platform.research_timing import LabelSpec
from qlib_platform.settings import Paths, Settings
from qlib_platform.train_select import build_dataset


def test_label_spec_is_the_canonical_expression_for_non_default_signal_lag():
    spec = LabelSpec(horizon_days=5, signal_lag_days=2)

    assert spec.lookahead_days == 7
    assert spec.spec_id == "return_5d_t2_v1"
    assert spec.qlib_config() == (["Ref($close, -7)/Ref($close, -1) - 1"], ["LABEL0"])
    assert spec.to_manifest()["expression"] == spec.expression


class _MiniResearchHandler(DataHandlerLP):
    """Small real Qlib loader used to exercise both production label paths."""

    _FEATURES = (
        ["$close", "$paused", "$listed_days", "$circ_mv", "$money20", "$is_st"],
        ["CLOSE", "PAUSED", "LISTED_DAYS", "CIRC_MV", "MONEY20", "IS_ST"],
    )

    def __init__(
        self,
        instruments="all",
        start_time=None,
        end_time=None,
        infer_processors=None,
        learn_processors=None,
        shared_processors=None,
        fit_start_time=None,
        fit_end_time=None,
        label=([], []),
        **kwargs,
    ) -> None:
        infer_processors = check_transform_proc(infer_processors or [], fit_start_time, fit_end_time)
        learn_processors = check_transform_proc(learn_processors or [], fit_start_time, fit_end_time)
        super().__init__(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
            data_loader={
                "class": "QlibDataLoader",
                "kwargs": {"config": {"feature": self._FEATURES, "label": label}},
            },
            shared_processors=shared_processors or [],
            infer_processors=infer_processors,
            learn_processors=learn_processors,
            **kwargs,
        )


def _write_qlib_field(root: Path, instrument: str, field: str, values: np.ndarray) -> None:
    target = root / "features" / instrument.lower() / f"{field}.day.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.concatenate([np.asarray([0.0]), values]).astype("<f4").tofile(target)


def _mini_qlib_provider(root: Path) -> pd.DatetimeIndex:
    dates = pd.bdate_range("2026-01-05", periods=30)
    (root / "calendars").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text(
        "\n".join(dates.strftime("%Y-%m-%d")) + "\n", encoding="utf-8"
    )
    (root / "instruments").mkdir()
    instruments = ["sh600000", "sz000001"]
    (root / "instruments" / "all.txt").write_text(
        "".join(f"{instrument}\t{dates[0].date()}\t{dates[-1].date()}\n" for instrument in instruments),
        encoding="utf-8",
    )
    for offset, instrument in enumerate(instruments):
        close = 10.0 + offset + np.arange(len(dates), dtype=float) * (0.1 + offset * 0.02)
        fields = {
            "close": close,
            "paused": np.zeros(len(dates)),
            "listed_days": np.full(len(dates), 500.0),
            "circ_mv": np.full(len(dates), 5_000_000_000.0),
            "money20": np.full(len(dates), 50_000_000.0),
            "is_st": np.zeros(len(dates)),
        }
        for field, values in fields.items():
            _write_qlib_field(root, instrument, field, values)
    return dates


def test_label_values_match_with_feature_store_on_and_off(tmp_path, monkeypatch):
    qlib_data = tmp_path / "qlib"
    dates = _mini_qlib_provider(qlib_data)
    settings = Settings(
        config_path=tmp_path / "configs" / "pipeline.yaml",
        data={
            "experiment": {"alpha": {"pack": "alpha158_daily_v1"}},
            "research": {
                "feature_store": {"enabled": True},
                "label_horizon_days": 5,
                "signal_lag_days": 2,
                "qlib_kernels": 1,
            },
            "universe": {
                "instruments": "all",
                "min_listed_days": 120,
                "min_circ_mv_yuan": 2_000_000_000,
                "min_money_20d_yuan": 20_000_000,
            },
        },
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=qlib_data,
    )
    monkeypatch.setattr("qlib_platform.train_select.handler_class", lambda pack: _MiniResearchHandler)

    import qlib
    from qlib.constant import REG_CN

    qlib.init(
        provider_uri=str(qlib_data),
        region=REG_CN,
        expression_cache=None,
        dataset_cache=None,
        kernels=1,
    )
    from qlib.data.dataset.loader import QlibDataLoader

    def load_raw_features(_settings, start_time, end_time, *, instruments=None):
        return QlibDataLoader({"feature": _MiniResearchHandler._FEATURES}).load(
            instruments or "all", start_time=start_time, end_time=end_time
        )

    monkeypatch.setattr("qlib_platform.feature_store._raw_features", load_raw_features)
    label_spec = LabelSpec(horizon_days=5, signal_lag_days=2)
    alpha_pack = get_alpha_pack("alpha158_daily_v1")
    train = (str(dates[0].date()), str(dates[9].date()))
    valid = (str(dates[10].date()), str(dates[14].date()))
    test = (str(dates[15].date()), str(dates[-1].date()))

    without_cache = build_dataset(
        train=train,
        valid=valid,
        test=test,
        universe=dict(settings.data["universe"]),
        label_spec=label_spec,
        alpha_pack=alpha_pack,
    )
    prepared_features, _ = prepare_feature_data(settings, train[0], test[1])
    with_cache = build_dataset(
        train=train,
        valid=valid,
        test=test,
        universe=dict(settings.data["universe"]),
        label_spec=label_spec,
        alpha_pack=alpha_pack,
        prepared_feature_data=prepared_features,
    )

    label_without_cache = without_cache.handler.fetch(col_set="label", data_key=DataHandlerLP.DK_R)["LABEL0"]
    label_with_cache = with_cache.handler.fetch(col_set="label", data_key=DataHandlerLP.DK_R)["LABEL0"]

    pd.testing.assert_series_equal(label_without_cache, label_with_cache)
    assert label_without_cache.index.names == ["datetime", "instrument"]
    assert label_without_cache.isna().equals(label_with_cache.isna())
    assert label_without_cache.isna().any()
