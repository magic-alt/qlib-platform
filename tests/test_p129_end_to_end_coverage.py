from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import qlib_platform.runtime.daily_research_run as daily_run_module
import qlib_platform.runtime.production_daily_run as production_run_module
from qlib_platform.data.corporate_actions import DIVIDEND_FIELDS
from qlib_platform.data.resumable_certified_sync import ResumableCertifiedDailySyncService
from qlib_platform.data.sources import FetchResult
from qlib_platform.runtime.production_daily_run import DailyResearchRun
from qlib_platform.settings import Paths, Settings


def _settings(tmp_path: Path) -> Settings:
    paths = Paths.from_root(tmp_path / "data")
    paths.mkdirs()
    config = tmp_path / "configs" / "pipeline.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("data_source:\n  kind: tushare\n", encoding="utf-8")
    return Settings(
        config_path=config,
        data={
            "start_date": "20260810",
            "end_date": "20260811",
            "data_source": {
                "kind": "tushare",
                "optional_endpoints": {
                    "moneyflow": False,
                    "stk_limit": False,
                    "suspend_d": False,
                    "stock_st": False,
                },
            },
            "qlib": {
                "dataset_dir": "unused",
                "dataset_name": "test",
                "dataset_version": "test",
                "dataset_ref": "test-current",
                "include_fields": [],
            },
            "data_sync": {
                "timezone": "Asia/Shanghai",
                "ready_after": "17:30",
                "market_lookback_trading_days": 2,
                "market_catchup_trading_days": 10,
                "corporate_action_lookback_calendar_days": 2,
            },
            "research": {"benchmark": "SH000300"},
            "universe": {"instruments": "all"},
            "production": {
                "daily_run": {
                    "notify": False,
                    "regression": {"enabled": False},
                    "verification": {"mode": "sampled", "sample_size": 8, "workers": 1},
                    "freshness": {"min_market_coverage_ratio": 0.5},
                }
            },
        },
        paths=paths,
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=paths.root / "qlib" / "test",
    )


def _calendar(settings: Settings, dates: tuple[str, ...] = ("2026-08-10", "2026-08-11")) -> None:
    pd.DataFrame(
        {
            "cal_date": pd.to_datetime(list(dates)),
            "is_open": [1] * len(dates),
            "pretrade_date": [pd.NaT] * len(dates),
        }
    ).to_parquet(settings.paths.metadata / "trade_calendar.parquet", index=False)


def _daily(date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": [date],
            "open": [10.0],
            "high": [10.5],
            "low": [9.8],
            "close": [10.2],
            "vol": [100.0],
            "amount": [1000.0],
        }
    )


def _basic(date: str) -> pd.DataFrame:
    return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [date], "close": [10.2]})


def _factor(date: str, value: float) -> pd.DataFrame:
    return pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": [date], "adj_factor": [value]}
    )


@dataclass(frozen=True)
class _Endpoint:
    name: str
    fields: str
    required: bool = True
    enabled: bool = True


class _ProviderClient:
    def __init__(self) -> None:
        self.fetch_calls = 0
        self.call_calls = 0

    def fetch(self, endpoint: str, **kwargs):
        self.fetch_calls += 1
        trade_date = str(kwargs["trade_date"])
        if endpoint == "daily":
            frame = _daily(trade_date)
        elif endpoint == "daily_basic":
            frame = _basic(trade_date)
        elif endpoint == "adj_factor":
            frame = _factor(trade_date, 1.0 if trade_date == "20260810" else 2.0)
        else:  # pragma: no cover - fixture contract
            raise AssertionError(endpoint)
        return FetchResult(frame, "success", 1)

    def call(self, endpoint: str, **kwargs):
        self.call_calls += 1
        if endpoint == "adj_factor":
            return pd.concat(
                [_factor("20260810", 0.9), _factor("20260811", 2.0)],
                ignore_index=True,
            )
        if endpoint == "dividend":
            return pd.DataFrame(columns=DIVIDEND_FIELDS.split(","))
        raise AssertionError(f"unexpected provider call: {endpoint} {kwargs}")


class _Extractor:
    endpoints = [
        _Endpoint("daily", "daily"),
        _Endpoint("adj_factor", "adj_factor"),
        _Endpoint("daily_basic", "daily_basic"),
    ]

    def __init__(self, client: _ProviderClient) -> None:
        self.client = client


def test_full_resumable_sync_repairs_factor_and_reuses_checkpoints(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    _calendar(settings)
    client = _ProviderClient()
    service = ResumableCertifiedDailySyncService(settings, extractor=_Extractor(client))

    monkeypatch.setattr(service, "_sync_extended", lambda eligible: {"status": "complete", "as_of": str(eligible)})
    monkeypatch.setattr(
        service,
        "_refresh_pit_from_extended",
        lambda: ({"status": "current"}, False, set()),
    )

    def refresh_metadata(dates: list[str]) -> dict[str, object]:
        benchmark = settings.paths.metadata / "benchmarks" / "SH000300.parquet"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"trade_date": pd.to_datetime(["2026-08-11"]), "close": [3000.0]}).to_parquet(
            benchmark, index=False
        )
        return {"dates": dates, "benchmark_rows": 1}

    publish_calls: list[dict[str, object]] = []

    def publish_qlib(
        changed_dates: list[str],
        revised_symbols: set[str],
        *,
        force_full: bool,
        pit_changed: bool,
        sync_context: dict[str, object],
    ) -> dict[str, object]:
        publish_calls.append(
            {
                "changed_dates": list(changed_dates),
                "revised_symbols": sorted(revised_symbols),
                "force_full": force_full,
                "pit_changed": pit_changed,
                "run_id": sync_context["run_id"],
            }
        )
        return {"mode": "update_fix", "data_release_id": "ds_test"}

    monkeypatch.setattr(service, "_refresh_metadata", refresh_metadata)
    monkeypatch.setattr(service, "_publish_qlib", publish_qlib)

    plan_path = service.create_plan(as_of="2026-08-11")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    apply_path = service.apply_plan(str(plan["plan_id"]))
    state = json.loads(apply_path.read_text(encoding="utf-8"))

    assert state["status"] == "SUCCEEDED"
    assert state["steps"]["factor_reconcile"]["output"]["event_symbol_count"] == 1
    assert state["steps"]["factor_reconcile"]["output"]["repair_dates"] == ["20260810"]
    assert float(service.store.read("adj_factor", "20260810")["adj_factor"].iloc[0]) == pytest.approx(0.9)
    assert float(service.store.read("adj_factor", "20260811")["adj_factor"].iloc[0]) == pytest.approx(2.0)
    assert publish_calls[0]["changed_dates"] == ["20260810", "20260811"]
    assert publish_calls[0]["run_id"] == plan["plan_id"]
    assert client.fetch_calls == 6

    calls_after_first_run = (client.fetch_calls, client.call_calls, len(publish_calls))
    resumed = service.apply_plan(str(plan["plan_id"]))
    resumed_state = json.loads(resumed.read_text(encoding="utf-8"))
    assert resumed_state["status"] == "SUCCEEDED"
    assert (client.fetch_calls, client.call_calls, len(publish_calls)) == calls_after_first_run


class _DailyRunSync:
    def __init__(self, root: Path, plan: dict[str, object]) -> None:
        self.root = root
        self.plan = plan
        self.apply_calls = 0
        self.plan_path = root / "plan.json"
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

    def load_plan(self, plan_id: str) -> dict[str, object]:
        assert plan_id == self.plan["plan_id"]
        return dict(self.plan)

    def apply_plan(self, plan_id: str, *, force_full: bool = False) -> Path:
        del force_full
        assert plan_id == self.plan["plan_id"]
        self.apply_calls += 1
        path = self.root / "apply.json"
        path.write_text('{"status":"SUCCEEDED"}', encoding="utf-8")
        return path

    def _plan_path(self, plan_id: str) -> Path:
        assert plan_id == self.plan["plan_id"]
        return self.plan_path


def _fake_dataset(tmp_path: Path, target: str) -> SimpleNamespace:
    root = tmp_path / "qlib-version"
    (root / "calendars").mkdir(parents=True)
    (root / "instruments").mkdir()
    (root / "features" / "sh600000").mkdir(parents=True)
    (root / "calendars" / "day.txt").write_text(
        f"{pd.Timestamp(target).date()}\n", encoding="utf-8"
    )
    (root / "instruments" / "all.txt").write_text(
        f"SH600000\t{pd.Timestamp(target).date()}\t{pd.Timestamp(target).date()}\n",
        encoding="utf-8",
    )
    (root / "features" / "sh600000" / "close.day.bin").write_bytes(b"feature")
    manifest = root / "dataset_manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    return SimpleNamespace(
        reference="test-current",
        version_id="version-1",
        dataset_name="test",
        data_path=root,
        manifest_path=manifest,
        manifest_sha256="manifest-sha",
    )


def test_daily_research_success_reuses_immutable_dataset_and_reports(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    plan = {
        "plan_id": "syncplan-20200102-success",
        "target_session": "20200102",
        "status": "PLANNED",
        "config_sha256": "cfg",
        "watermarks": {"daily": {"last_success": "20200101"}},
        "endpoint_gaps": {"daily": ["20200102"]},
    }
    fake_sync = _DailyRunSync(tmp_path / "daily-fake", plan)
    runner.sync = fake_sync  # type: ignore[assignment]
    resolved = _fake_dataset(tmp_path, "2020-01-02")

    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setattr(daily_run_module, "resolve_dataset", lambda *args, **kwargs: resolved)

    def verify_manifest(*args, evidence=None, **kwargs):
        del args, kwargs
        if evidence is not None:
            evidence["verified"] = True
        return {"data_release_id": "ds_test", "semantic_contract": {}}

    monkeypatch.setattr(daily_run_module, "verify_dataset_manifest", verify_manifest)

    manifest_path = runner.execute_plan(str(plan["plan_id"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "SUCCEEDED"
    assert manifest["immutable_input"]["data_release_id"] == "ds_test"
    assert manifest["immutable_input"]["dataset_version_id"] == "version-1"
    assert manifest["steps"]["feature_materialization"]["status"] == "SUCCEEDED"
    assert manifest["steps"]["regression_backtest"]["status"] == "SKIPPED"
    assert manifest["steps"]["notification"]["status"] == "SKIPPED"
    assert Path(manifest["report"]).is_file()
    assert fake_sync.apply_calls == 1

    second = json.loads(runner.execute_plan(str(plan["plan_id"])).read_text(encoding="utf-8"))
    assert second["status"] == "SUCCEEDED"
    assert fake_sync.apply_calls == 1


def test_regression_and_notification_success_and_failure_paths(tmp_path: Path, monkeypatch):
    settings = _settings(tmp_path)
    runner = DailyResearchRun(settings)
    plan = {"plan_id": "regression-plan", "target_session": "20200102"}
    dataset = {"dataset_version_id": "version-1", "data_release_id": "ds_test"}

    monkeypatch.setattr(
        daily_run_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    success_state: dict[str, object] = {"plan_id": "regression-plan", "steps": {}}
    result = runner._run_regression(plan, success_state, dataset, enabled=True)
    assert result["exit_code"] == 0
    assert success_state["steps"]["regression_backtest"]["status"] == "SUCCEEDED"  # type: ignore[index]

    monkeypatch.setattr(
        daily_run_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="", stderr="failed"),
    )
    failed_state: dict[str, object] = {"plan_id": "regression-fail", "steps": {}}
    with pytest.raises(RuntimeError, match="exited 7"):
        runner._run_regression(
            {"plan_id": "regression-fail", "target_session": "20200102"},
            failed_state,
            dataset,
            enabled=True,
        )
    assert failed_state["steps"]["regression_backtest"]["status"] == "FAILED"  # type: ignore[index]

    settings.data["production"]["daily_run"]["notify"] = True
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "https://example.invalid/hook")
    sent: list[str] = []

    class _Notifier:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def send(self, envelope) -> None:
            sent.append(envelope.message_id)

    monkeypatch.setattr(daily_run_module, "FeishuNotifier", _Notifier)
    notify_state: dict[str, object] = {
        "plan_id": "notify-plan",
        "status": "SUCCEEDED",
        "steps": {
            "dataset_verify": {
                "status": "SUCCEEDED",
                "output": {"dataset_version_id": "version-1", "data_release_id": "ds_test"},
            }
        },
    }
    report = tmp_path / "report.md"
    report.write_text("report", encoding="utf-8")
    notification = runner._notify(
        {"plan_id": "notify-plan", "target_session": "20200102"},
        notify_state,
        report,
    )
    assert notification["sent"] is True
    assert sent == ["daily-research-notify-plan"]


def test_production_cli_main_branches(tmp_path: Path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    plan_path = tmp_path / "cli-plan.json"
    plan_path.write_text('{"plan_id":"cli-plan"}', encoding="utf-8")
    result_path = tmp_path / "cli-result.json"
    result_path.write_text('{"status":"SUCCEEDED"}', encoding="utf-8")

    class _Runner:
        def __init__(self, _settings: Settings) -> None:
            self.settings = _settings

        def plan(self, **kwargs) -> Path:
            del kwargs
            return plan_path

        def execute_plan(self, *args, **kwargs) -> Path:
            del args, kwargs
            return result_path

        def backfill(self, *args, **kwargs) -> list[Path]:
            del args, kwargs
            return [result_path]

    monkeypatch.setattr(production_run_module, "DailyResearchRun", _Runner)
    monkeypatch.setattr(
        production_run_module,
        "Settings",
        SimpleNamespace(load=lambda *args, **kwargs: settings),
    )

    def run_with(namespace: SimpleNamespace) -> str:
        monkeypatch.setattr(
            production_run_module.base,
            "parser",
            lambda: SimpleNamespace(parse_args=lambda: namespace),
        )
        assert production_run_module.main() == 0
        return capsys.readouterr().out

    common = {
        "config": str(settings.config_path),
        "mode": "routine",
        "force_full": False,
        "regression": None,
    }
    assert "cli-plan" in run_with(
        SimpleNamespace(**common, resume=None, plan=True, backfill=None, as_of="2020-01-02")
    )
    assert "SUCCEEDED" in run_with(
        SimpleNamespace(**common, resume="cli-plan", plan=False, backfill=None, as_of=None)
    )
    assert "backfill" in run_with(
        SimpleNamespace(
            **common,
            resume=None,
            plan=False,
            backfill=("2020-01-02", "2020-01-03"),
            as_of=None,
        )
    )
    assert "SUCCEEDED" in run_with(
        SimpleNamespace(**common, resume=None, plan=False, backfill=None, as_of="2020-01-02")
    )
