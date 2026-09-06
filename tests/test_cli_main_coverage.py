from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

cli_main = importlib.import_module("qlib_platform.cli.main")


def _settings(tmp_path: Path) -> SimpleNamespace:
    paths = SimpleNamespace(
        root=tmp_path,
        state=tmp_path / "state",
        metadata=tmp_path / "metadata",
        curated=tmp_path / "curated",
        output=tmp_path / "output",
        raw=tmp_path / "raw",
        staging_full=tmp_path / "staging-full",
    )
    return SimpleNamespace(
        paths=paths,
        data={"start_date": "2026-01-01", "end_date": "2026-09-01", "research": {}},
        registry_path=tmp_path / "registry.sqlite",
        qlib_dataset_ref="current",
        config_path=tmp_path / "config.yaml",
        uses_platform_release=lambda: False,
    )


def _run(monkeypatch, settings: SimpleNamespace, **values: object) -> None:
    payload = {"config": "config.yaml", **values}
    args = SimpleNamespace(**payload)
    monkeypatch.setattr(cli_main, "parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(
        cli_main,
        "Settings",
        SimpleNamespace(load=lambda *args, **kwargs: settings),
    )
    cli_main.main()


def test_first_value_and_report_payload(tmp_path) -> None:
    frame = pd.DataFrame({"signal_date": [None, "2026-09-01"]})
    assert cli_main._first_value(frame, "signal_date", "override") == "override"
    assert cli_main._first_value(frame, "signal_date", None) == "2026-09-01"
    with pytest.raises(ValueError, match="must be supplied"):
        cli_main._first_value(frame, "missing", None)

    run = tmp_path / "run"
    run.mkdir()
    (run / "backtest_report.pdf").write_text("pdf", encoding="utf-8")
    manifest = {
        "externalRunId": "run-1",
        "artifacts": [
            {"name": "timings.json", "localPath": "/tmp/timings.json"},
            {"name": "backtest_report.md", "localPath": "/tmp/report.md"},
        ],
        "runtime": {"modelProfile": "lgb", "resolvedDevice": "cpu"},
        "timings": {"fit": 1.2},
    }
    path = run / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = cli_main._report_payload(path, run / "selection.csv")
    assert payload["runId"] == "run-1"
    assert payload["timingsJson"] == "/tmp/timings.json"
    assert payload["reportMarkdown"] == "/tmp/report.md"
    assert payload["reportPdf"] == str(run / "backtest_report.pdf")
    assert payload["modelProfile"] == "lgb"
    assert payload["latestSelection"] == str(run / "selection.csv")


def test_status_health_project_and_contract_dispatch(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.runtime.standalone_status as status
    import qlib_platform.runtime.health as health
    import qlib_platform.project_audit as project_audit
    import qlib_platform.workflow_contract as workflow_contract

    monkeypatch.setattr(status, "collect_status", lambda _: {"ok": True})
    monkeypatch.setattr(status, "render_status", lambda _: "READY")
    _run(monkeypatch, settings, command="status", as_json=False)
    assert "READY" in capsys.readouterr().out
    _run(monkeypatch, settings, command="status", as_json=True)
    assert json.loads(capsys.readouterr().out)["ok"] is True

    monkeypatch.setattr(health, "live_health", lambda: {"live": True})
    monkeypatch.setattr(health, "ready_health", lambda _: {"ready": True})
    monkeypatch.setattr(health, "dependency_health", lambda _: {"deps": True})
    for kind, key in (("live", "live"), ("ready", "ready"), ("dependencies", "deps")):
        _run(monkeypatch, settings, command="health", kind=kind)
        assert json.loads(capsys.readouterr().out)[key] is True

    monkeypatch.setattr(project_audit, "audit_project", lambda _: {"score": 99, "passed": True})
    monkeypatch.setattr(project_audit, "write_audit", lambda report, output: Path("audit.json"))
    _run(monkeypatch, settings, command="project-audit", root=".", output="audit.json")
    assert json.loads(capsys.readouterr().out)["score"] == 99

    monkeypatch.setattr(workflow_contract, "validate_qrun_contract", lambda *args: {"passed": True})
    _run(monkeypatch, settings, command="validate-qrun-contract", workflow=None)
    assert json.loads(capsys.readouterr().out)["passed"] is True
    monkeypatch.setattr(workflow_contract, "validate_qrun_contract", lambda *args: {"passed": False})
    with pytest.raises(SystemExit, match="2"):
        _run(monkeypatch, settings, command="validate-qrun-contract", workflow=None)


def test_research_audit_gate_and_ingest_dispatch(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.backtesting.backtest_audit as backtest_audit
    import qlib_platform.research.evaluation.gates as gates
    import qlib_platform.data.fundamentals as fundamentals

    monkeypatch.setattr(backtest_audit, "audit_mlflow_run", lambda _: {"passed": True})
    monkeypatch.setattr(backtest_audit, "write_audit", lambda *args: Path("research-audit.json"))
    _run(monkeypatch, settings, command="research-audit", run_dir="run", output=None)
    assert json.loads(capsys.readouterr().out)["passed"] is True
    monkeypatch.setattr(backtest_audit, "audit_mlflow_run", lambda _: {"passed": False})
    with pytest.raises(SystemExit, match="2"):
        _run(monkeypatch, settings, command="research-audit", run_dir="run", output=None)

    metrics = tmp_path / "metrics.json"
    metrics.write_text("{}", encoding="utf-8")
    config = tmp_path / "gate.yaml"
    config.write_text("research: {}\n", encoding="utf-8")
    monkeypatch.setattr(gates, "evaluate_research_metrics", lambda *args: {"passed": True})
    monkeypatch.setattr(gates, "write_gate_report", lambda *args: "gate.json")
    _run(
        monkeypatch,
        settings,
        command="research-gate",
        metrics_json=str(metrics),
        config=str(config),
        output=None,
    )
    assert "gate.json" in capsys.readouterr().out

    monkeypatch.setattr(fundamentals, "ingest_pit_fundamentals", lambda *args: Path("pit.parquet"))
    _run(
        monkeypatch,
        settings,
        command="ingest-pit-fundamentals",
        reports="reports",
        calendar=None,
        output=None,
    )
    assert "pit.parquet" in capsys.readouterr().out


def test_bootstrap_migration_industry_kline_and_report_dispatch(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.bootstrap as bootstrap_module
    import qlib_platform.datasets.migration_acceptance as migration
    import qlib_platform.data.industry as industry
    import qlib_platform.data.kline_export as kline
    import qlib_platform.backtesting.backtest_report as report

    monkeypatch.setattr(bootstrap_module, "bootstrap", lambda *args, **kwargs: {"ok": True})
    _run(monkeypatch, settings, command="bootstrap", source="raw", path=None, start=None, end=None)
    assert json.loads(capsys.readouterr().out)["ok"] is True

    monkeypatch.setattr(migration, "run_migration_acceptance", lambda *args, **kwargs: Path("evidence.json"))
    _run(
        monkeypatch,
        settings,
        command="migration-acceptance",
        source="raw",
        source_root=None,
        acceptance_root=None,
        start=None,
        end=None,
        single_thread=True,
    )
    assert json.loads(capsys.readouterr().out)["evidence"] == "evidence.json"

    monkeypatch.setattr(industry, "sync_sw2021_industry", lambda *args, **kwargs: Path("industry.parquet"))
    _run(monkeypatch, settings, command="sync-industry", end="2026-09-01")
    assert json.loads(capsys.readouterr().out)["industryClassificationPit"] == "industry.parquet"

    monkeypatch.setattr(kline, "export_kline", lambda *args, **kwargs: Path("kline.csv"))
    _run(
        monkeypatch,
        settings,
        command="export-kline",
        symbol="SZ000001",
        output="x.csv",
        start=None,
        end=None,
        adjust="qfq",
    )
    assert "kline.csv" in capsys.readouterr().out

    run = tmp_path / "research-run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"externalRunId": "r"}), encoding="utf-8")
    monkeypatch.setattr(report, "write_backtest_report", lambda *args, **kwargs: None)
    _run(monkeypatch, settings, command="research-report", run_dir=str(run), positions_file=None)
    assert json.loads(capsys.readouterr().out)["runId"] == "r"


def test_model_ops_daily_runtime_and_feature_dispatch(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.models.production_refit as refit
    import qlib_platform.models.model_registry as model_registry_module
    import qlib_platform.runtime.daily_signal_runner as signal_runner
    import qlib_platform.data.daily_sync as daily_sync
    import qlib_platform.models.model_runtime as model_runtime
    import qlib_platform.research.features.store as feature_store

    monkeypatch.setattr(refit, "refit_production_model", lambda *args, **kwargs: Path("model.json"))
    _run(monkeypatch, settings, command="model-refit", research_run="run", as_of="2026-09-01")
    assert json.loads(capsys.readouterr().out)["manifest"] == "model.json"

    class Registry:
        def __init__(self, settings: object):
            pass

        def deploy(self, deployment_id: str, *, device: str) -> dict[str, object]:
            return {"status": "DEPLOYED", "metadata_json": "hidden"}

        def rollback(self, deployment_id: str, *, device: str) -> dict[str, object]:
            return {"status": "ROLLED_BACK"}

        def current(self) -> dict[str, object]:
            return {"status": "CURRENT"}

    monkeypatch.setattr(model_registry_module, "ModelRegistry", Registry)
    for command, status_value in (
        ("model-deploy", "DEPLOYED"),
        ("model-rollback", "ROLLED_BACK"),
        ("model-status", "CURRENT"),
    ):
        _run(monkeypatch, settings, command=command, deployment_id="d", device="cpu")
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == status_value
        assert "metadata_json" not in payload

    monkeypatch.setattr(
        signal_runner,
        "run_daily_signal",
        lambda *args, **kwargs: SimpleNamespace(signal_id="sig", manifest_path=Path("sig.json")),
    )
    _run(
        monkeypatch,
        settings,
        command="daily-signal-run",
        as_of="2026-09-01",
        no_notify=False,
        skip_sync=True,
        supersede=False,
    )
    assert json.loads(capsys.readouterr().out)["signalId"] == "sig"

    sync_path = tmp_path / "sync.json"
    sync_path.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    monkeypatch.setattr(daily_sync, "run_daily_sync", lambda *args, **kwargs: sync_path)
    _run(
        monkeypatch,
        settings,
        command="daily-sync",
        as_of="2026-09-01",
        check_only=True,
        force_full=False,
    )
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"

    monkeypatch.setattr(model_runtime, "load_model_profile", lambda *args: object())
    monkeypatch.setattr(
        model_runtime,
        "resolve_runtime",
        lambda _: SimpleNamespace(to_manifest=lambda: {"device": "cpu"}),
    )
    _run(monkeypatch, settings, command="runtime-probe", model_profile="lightgbm")
    assert json.loads(capsys.readouterr().out)["device"] == "cpu"

    monkeypatch.setattr(feature_store, "prepare_feature_data", lambda *args, **kwargs: (None, {"rows": 10}))
    _run(monkeypatch, settings, command="feature-store", start=None, end=None, force=False)
    assert json.loads(capsys.readouterr().out)["rows"] == 10


def test_normalization_dump_and_ingestion_command_groups(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.data.normalize as normalize
    import qlib_platform.datasets.qlib_export as qlib_export
    import qlib_platform.data.ingestion as ingestion

    calls: list[str] = []
    monkeypatch.setattr(normalize, "build_all_curated", lambda *args: calls.append("curate"))
    monkeypatch.setattr(normalize, "build_curated_day", lambda *args: calls.append("curate-day"))
    monkeypatch.setattr(normalize, "export_full_staging", lambda *args: calls.append("stage-full"))
    monkeypatch.setattr(normalize, "export_incremental_staging", lambda *args: calls.append("stage-update"))
    _run(monkeypatch, settings, command="curate", start=None, end=None)
    _run(monkeypatch, settings, command="curate-day", trade_date="20260901", force=True)
    _run(monkeypatch, settings, command="stage-full", force=True)
    _run(monkeypatch, settings, command="stage-update", trade_dates=["20260901"])
    assert calls == ["curate", "curate-day", "stage-full", "stage-update"]

    monkeypatch.setattr(qlib_export, "dump_full", lambda *args, **kwargs: Path("full"))
    monkeypatch.setattr(qlib_export, "dump_update", lambda *args, **kwargs: Path("update"))
    _run(monkeypatch, settings, command="dump-full", single_thread=True)
    assert "full" in capsys.readouterr().out
    _run(monkeypatch, settings, command="dump-update", single_thread=True)
    assert "update" in capsys.readouterr().out

    class Extractor:
        def __init__(self, settings: object):
            pass

        def fetch_stock_master(self) -> None:
            calls.append("stock-master")

        def fetch_calendar(self, start: str, end: str) -> None:
            calls.append("calendar")

        def backfill(self, start: str, end: str, force: bool) -> None:
            calls.append("backfill")

        def source_preflight(self, start: str, end: str) -> dict[str, object]:
            return {"passed": True}

        def sync_benchmark(self, symbol: str, start: str, end: str) -> pd.DataFrame:
            return pd.DataFrame({"x": [1, 2]})

        def sync_universe_membership(self, start: str, end: str) -> pd.DataFrame:
            return pd.DataFrame({"x": [1]})

    monkeypatch.setattr(ingestion, "Extractor", Extractor)
    _run(monkeypatch, settings, command="init-metadata")
    _run(monkeypatch, settings, command="backfill", start=None, end=None, force=False)
    _run(monkeypatch, settings, command="source-preflight", start=None, end=None)
    assert json.loads(capsys.readouterr().out)["passed"] is True
    _run(monkeypatch, settings, command="sync-benchmark", symbol="SH000300", start=None, end=None)
    assert json.loads(capsys.readouterr().out)["rows"] == 2
    _run(monkeypatch, settings, command="sync-universe", start=None, end=None)
    assert json.loads(capsys.readouterr().out)["intervals"] == 1


def _study_manifest(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / f"study-{len(list(tmp_path.glob('study-*')))}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_research_diagnostic_dispatch(monkeypatch, tmp_path, capsys) -> None:
    settings = _settings(tmp_path)
    import qlib_platform.research.studies.alpha as alpha
    import qlib_platform.research.studies.regime as regime
    import qlib_platform.research.studies.attribution as attribution
    import qlib_platform.research.studies.explanation as explanation
    import qlib_platform.research.studies.synthesis as synthesis

    alpha_path = _study_manifest(tmp_path, {"studyId": "a", "featureCount": 10, "rollingOosSessions": 100})
    monkeypatch.setattr(alpha, "run_alpha_diagnose", lambda *args, **kwargs: alpha_path)
    _run(
        monkeypatch,
        settings,
        command="alpha-diagnose",
        acceptance="a",
        walk_forward="w",
        feature_snapshot="f",
        taxonomy="t",
        output=None,
    )
    assert json.loads(capsys.readouterr().out)["featureCount"] == 10

    regime_path = _study_manifest(
        tmp_path, {"studyId": "r", "status": {"regimeDiagnostics": "PASS"}, "availability": {"x": {}}}
    )
    monkeypatch.setattr(regime, "run_regime_diagnose", lambda *args, **kwargs: regime_path)
    _run(
        monkeypatch,
        settings,
        command="regime-diagnose",
        base_study="b",
        acceptance="a",
        walk_forward="w",
        ridge_predictions="r",
        lightgbm_predictions="l",
        feature_snapshot="f",
        taxonomy="t",
        regimes="g",
        output=None,
    )
    assert json.loads(capsys.readouterr().out)["regimeDiagnostics"] == "PASS"

    attribution_path = _study_manifest(
        tmp_path,
        {"studyId": "t", "status": {"failureAttribution": "PASS"}, "primaryAlphaLossSource": "model"},
    )
    monkeypatch.setattr(attribution, "run_attribution_diagnose", lambda *args, **kwargs: attribution_path)
    _run(
        monkeypatch,
        settings,
        command="attribution-diagnose",
        regime_study="r",
        acceptance="a",
        walk_forward="w",
        ridge_predictions="r",
        lightgbm_predictions="l",
        portfolio_run=[],
        attribution="x",
        output=None,
    )
    assert json.loads(capsys.readouterr().out)["failureAttribution"] == "PASS"

    explanation_path = _study_manifest(
        tmp_path,
        {
            "studyId": "e",
            "status": {"modelExplanation": "PASS", "regimeConditioning": "PASS"},
            "primaryMechanism": "main_effects",
        },
    )
    monkeypatch.setattr(explanation, "run_explanation_diagnose", lambda *args, **kwargs: explanation_path)
    _run(
        monkeypatch,
        settings,
        command="explanation-diagnose",
        base_study="b",
        regime_study="r",
        attribution_study="t",
        acceptance="a",
        ridge_walk_forward="rw",
        lightgbm_walk_forward="lw",
        xgboost_walk_forward="xw",
        feature_snapshot="f",
        taxonomy="t",
        model_artifact_root=["m"],
        explanation="e",
        output=None,
    )
    assert json.loads(capsys.readouterr().out)["modelExplanation"] == "PASS"

    synthesis_path = _study_manifest(
        tmp_path,
        {
            "studyId": "s",
            "status": {"phase1Completion": "PASS", "regimeDiagnostics": "PASS"},
            "primaryRecommendation": "diagnose",
        },
    )
    monkeypatch.setattr(synthesis, "run_research_synthesis", lambda *args, **kwargs: synthesis_path)
    _run(
        monkeypatch,
        settings,
        command="research-synthesize",
        feature_study="f",
        regime_study="r",
        attribution_study="a",
        explanation_study="e",
        synthesis="s",
        output=None,
    )
    assert json.loads(capsys.readouterr().out)["phase1Completion"] == "PASS"
