from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from ..full_walk_forward_acceptance import RunEvidence
from ..lineage import git_revision, sha256_json
from ..settings import Settings
from ..store import sha256_file
from .failure_attribution import (
    FailureAttributionSpec,
    PortfolioVariant,
    derive_daily_model_topk_overlap,
    derive_daily_signal_conversion,
    derive_failure_summary,
    load_failure_attribution_spec,
    summarize_model_topk_overlap,
    summarize_signal_conversion,
)
from .portfolio_attribution import (
    build_daily_holdings_conversion,
    build_daily_portfolio_bridge,
    derive_benchmark_diagnostics,
    derive_cost_sensitivity,
    derive_rolling_benchmark_diagnostics,
    summarize_portfolio_bridge,
)
from .regime_study import REGIME_STUDY_SCHEMA, _load_model_predictions
from .study import _fold_assignments, _mapping, _validate_acceptance_and_run
from .turnover_attribution import derive_turnover_attribution


ATTRIBUTION_STUDY_SCHEMA = "alpha_failure_attribution_study_v1"
ATTRIBUTION_MANIFEST_NAME = "attribution_diagnostics_manifest.json"


@dataclass(frozen=True)
class PortfolioRunInput:
    name: str
    model: str
    variant: str
    manifest_path: Path
    report_path: Path
    holdings_path: Path
    audit_path: Path
    strategy: PortfolioVariant
    prediction_sha256: str


def _load_json(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{name} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return payload


def _artifact_path(root: Path, manifest: Mapping[str, Any], name: str) -> Path:
    matches = [item for item in manifest.get("artifacts", []) if item.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"portfolio run must contain exactly one {name}")
    raw = str(matches[0].get("localPath") or matches[0].get("path") or "")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"portfolio artifact is missing: {resolved}")
    return resolved


def _parse_portfolio_reference(value: str) -> tuple[str, str, Path]:
    key, separator, raw_path = value.partition("=")
    if not separator or not raw_path.strip():
        raise ValueError("portfolio run must use MODEL:VARIANT=MANIFEST_OR_DIRECTORY")
    model, separator, variant = key.partition(":")
    if not separator or model not in {"ridge", "lightgbm", "xgboost"}:
        raise ValueError("portfolio run model must be ridge, lightgbm, or xgboost")
    return model, variant, Path(raw_path).expanduser().resolve()


def _manifest_path(reference: Path) -> Path:
    return reference / "manifest.json" if reference.is_dir() else reference


def _strategy_from_manifest(manifest: Mapping[str, Any], variant: str) -> PortfolioVariant:
    raw = manifest.get("strategy")
    if not isinstance(raw, Mapping):
        execution = _mapping(manifest.get("execution"), "portfolio execution")
        raw = _mapping(execution.get("topkDropout"), "TopKDropout execution")
    return PortfolioVariant(
        name=variant,
        topk=int(str(raw.get("topk"))),
        n_drop=int(str(raw.get("n_drop"))),
        hold_threshold=int(str(raw.get("hold_thresh"))),
    )


def _validate_strategy(actual: PortfolioVariant, expected: PortfolioVariant) -> None:
    if (
        actual.topk,
        actual.n_drop,
        actual.hold_threshold,
    ) != (expected.topk, expected.n_drop, expected.hold_threshold):
        raise ValueError(
            f"portfolio variant {expected.name} differs from its predeclared TopK/n_drop/hold contract"
        )


def _accepted_hash(acceptance: Mapping[str, Any], model: str, artifact: str) -> str:
    accepted_model = _mapping(_mapping(acceptance.get("models"), "acceptance models").get(model), model)
    exact = _mapping(accepted_model.get("resumedExact"), f"{model} exact replay")
    value = str(exact.get(artifact) or "")
    if not value:
        raise ValueError(f"acceptance does not certify {model} {artifact}")
    return value


def _load_walk_forward_portfolio(
    run: RunEvidence,
    acceptance: Mapping[str, Any],
    *,
    model: str,
    variant: str,
    expected_strategy: PortfolioVariant,
) -> PortfolioRunInput:
    family = str(_mapping(run.evidence.get("model"), "walk-forward model").get("family") or "")
    if family != model:
        raise ValueError(f"walk-forward portfolio model differs: {family!r} != {model!r}")
    report = run.artifact("portfolio_report.parquet")
    holdings = run.artifact("holdings.parquet")
    audit = run.artifact("strategy_audit.parquet")
    if sha256_file(report) != _accepted_hash(acceptance, model, report.name):
        raise ValueError(f"{model} portfolio report does not match Full Walk-forward Acceptance")
    if sha256_file(holdings) != _accepted_hash(acceptance, model, holdings.name):
        raise ValueError(f"{model} holdings do not match Full Walk-forward Acceptance")
    actual_strategy = _strategy_from_manifest(run.manifest, variant)
    _validate_strategy(actual_strategy, expected_strategy)
    prediction_sha = sha256_file(run.artifact("oos_predictions.parquet"))
    accepted_prediction = _mapping(
        _mapping(acceptance.get("models"), "acceptance models").get(model), model
    ).get("predictionSha256")
    if prediction_sha != accepted_prediction:
        raise ValueError(f"{model} portfolio predictions do not match acceptance")
    return PortfolioRunInput(
        name=f"{model}_{variant}",
        model=model,
        variant=variant,
        manifest_path=run.root / "manifest.json",
        report_path=report,
        holdings_path=holdings,
        audit_path=audit,
        strategy=actual_strategy,
        prediction_sha256=prediction_sha,
    )


def _load_prediction_only_portfolio(
    reference: Path,
    acceptance: Mapping[str, Any],
    *,
    model: str,
    variant: str,
    expected_strategy: PortfolioVariant,
) -> PortfolioRunInput:
    manifest_path = _manifest_path(reference)
    manifest = _load_json(manifest_path, "prediction-only portfolio manifest")
    if manifest.get("runKind") != "predictions_only_backtest":
        raise ValueError("non-baseline portfolio inputs must be predictions-only backtests")
    promotion = _mapping(manifest.get("promotion"), "portfolio promotion")
    isolation = _mapping(manifest.get("executionIsolation"), "portfolio execution isolation")
    if promotion.get("promotionAuthorized") is not False or any(
        int(isolation.get(name, -1)) != 0
        for name in (
            "featureComputeCalls",
            "rawMaterializationCalls",
            "modelTrainCalls",
            "modelPredictCalls",
        )
    ):
        raise ValueError("portfolio input does not prove prediction-only, non-publishing isolation")
    source = _mapping(manifest.get("sourcePrediction"), "portfolio source prediction")
    prediction_sha = str(source.get("sha256") or "")
    expected_sha = str(
        _mapping(_mapping(acceptance.get("models"), "acceptance models").get(model), model).get(
            "predictionSha256"
        )
        or ""
    )
    if prediction_sha != expected_sha:
        raise ValueError(f"{model} portfolio input does not use the accepted PredictionSnapshot")
    actual_strategy = _strategy_from_manifest(manifest, variant)
    _validate_strategy(actual_strategy, expected_strategy)
    root = manifest_path.parent.resolve()
    return PortfolioRunInput(
        name=f"{model}_{variant}",
        model=model,
        variant=variant,
        manifest_path=manifest_path,
        report_path=_artifact_path(root, manifest, "portfolio_report.parquet"),
        holdings_path=_artifact_path(root, manifest, "holdings.parquet"),
        audit_path=_artifact_path(root, manifest, "strategy_audit.parquet"),
        strategy=actual_strategy,
        prediction_sha256=prediction_sha,
    )


def _validate_regime_study(path: Path, acceptance_path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    manifest = _load_json(path, "regime study manifest")
    if manifest.get("schemaVersion") != REGIME_STUDY_SCHEMA:
        raise ValueError("unsupported regime study schema")
    status = _mapping(manifest.get("status"), "regime study status")
    if status.get("regimeDiagnostics") not in {"PASS", "PARTIAL"}:
        raise ValueError("regime diagnostics must be PASS or PARTIAL")
    if manifest.get("selectionUsesFinalHoldout") is not False:
        raise ValueError("regime study does not prove final-holdout isolation")
    if manifest.get("publishingAuthorized") is not False:
        raise ValueError("regime study unexpectedly authorizes publishing")
    contract = _mapping(manifest.get("contract"), "regime study contract")
    if contract.get("fullWalkForwardAcceptanceSha256") != sha256_file(acceptance_path):
        raise ValueError("regime study and attribution use different acceptance evidence")
    root = path.parent.resolve()
    labels_path: Path | None = None
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, "regime artifact")
        target = (root / str(artifact.get("path") or "")).resolve()
        if target.parent != root or not target.is_file() or sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"regime artifact checksum mismatch: {target}")
        if artifact.get("name") == "regime_labels.parquet":
            labels_path = target
    if labels_path is None:
        raise ValueError("regime study does not contain regime_labels.parquet")
    return manifest, pd.read_parquet(labels_path)


def _portfolio_inputs(
    references: Sequence[str],
    *,
    xgboost_run: RunEvidence,
    acceptance: Mapping[str, Any],
    spec: FailureAttributionSpec,
) -> list[PortfolioRunInput]:
    inputs = [
        _load_walk_forward_portfolio(
            xgboost_run,
            acceptance,
            model="xgboost",
            variant="baseline",
            expected_strategy=spec.baseline,
        )
    ]
    seen = {("xgboost", "baseline")}
    for value in references:
        model, variant, reference = _parse_portfolio_reference(value)
        key = (model, variant)
        if key in seen:
            raise ValueError(f"duplicate portfolio input: {model}:{variant}")
        if variant not in spec.portfolio_variants:
            raise ValueError(f"portfolio variant is not predeclared: {variant}")
        expected = spec.portfolio_variants[variant]
        manifest = _load_json(_manifest_path(reference), "portfolio manifest")
        if manifest.get("walkForwardEvidence") is not None:
            if variant != "baseline":
                raise ValueError("walk-forward portfolio bundles are allowed only for baseline")
            loaded = _load_walk_forward_portfolio(
                RunEvidence.load(_manifest_path(reference).parent),
                acceptance,
                model=model,
                variant=variant,
                expected_strategy=expected,
            )
        else:
            loaded = _load_prediction_only_portfolio(
                reference,
                acceptance,
                model=model,
                variant=variant,
                expected_strategy=expected,
            )
        inputs.append(loaded)
        seen.add(key)
    return inputs


def _implementation_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    names = (
        "failure_attribution.py",
        "portfolio_attribution.py",
        "turnover_attribution.py",
        "attribution_study.py",
    )
    return {name: sha256_file(root / name) for name in names}


def _artifact_entry(path: Path, *, rows: int | None = None) -> dict[str, object]:
    result: dict[str, object] = {"name": path.name, "path": path.name, "sha256": sha256_file(path)}
    if rows is not None:
        result["rows"] = rows
    return result


def _summary_frame(summary: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for layer in ("signalLayer", "modelLayer", "rankingLayer", "portfolioLayer", "costLayer"):
        values = _mapping(summary.get(layer), layer)
        status = values.get("status")
        for metric, value in values.items():
            if metric == "status":
                continue
            rows.append({"layer": layer, "status": status, "metric": metric, "value": value})
    failure = _mapping(summary.get("regimeFailure"), "regimeFailure")
    for fold, value in failure.items():
        rows.append({"layer": "regimeFailure", "status": value, "metric": fold, "value": None})
    rows.append(
        {
            "layer": "primary",
            "status": summary.get("primaryAlphaLossSource"),
            "metric": "PRIMARY_ALPHA_LOSS_SOURCE",
            "value": None,
        }
    )
    return pd.DataFrame(rows)


def _write_report(
    path: Path,
    *,
    study_id: str,
    summary: Mapping[str, Any],
    signal: pd.DataFrame,
    portfolio: pd.DataFrame,
    spec: FailureAttributionSpec,
) -> None:
    all_signal = signal.loc[signal["scope_type"].eq("ALL_OOS")]
    failure_signal = signal.loc[signal["scope_type"].eq("FOLD") & signal["scope"].eq(spec.failure_fold)]
    xgb_portfolio = portfolio.loc[portfolio["model"].eq("xgboost") & portfolio["variant"].eq("baseline")]
    selected_portfolio = xgb_portfolio.loc[
        xgb_portfolio["scope_type"].eq("ALL_OOS")
        | (xgb_portfolio["scope_type"].eq("FOLD") & xgb_portfolio["scope"].eq(spec.failure_fold))
    ]
    lines = [
        "# Alpha Research Phase 1 — Prediction-to-Portfolio Failure Attribution",
        "",
        f"- Study ID: `{study_id}`",
        "- Failure Attribution: PASS",
        f"- Primary Alpha Loss Source: **{summary['primaryAlphaLossSource']}**",
        "- Model Train Calls: 0",
        "- Model Predict Calls: 0",
        "- Feature Materialization Calls: 0",
        "- Selection Uses Final Holdout: false",
        "- Publishing Authorized: false",
        "",
        "Signal conversion and realized daily P&L are separate chains because the governed label is a forward five-session return; they are not presented as one additive waterfall.",
        "",
        "## Failure attribution summary",
        "",
        "```json",
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2),
        "```",
        "",
        "## Signal conversion",
        "",
        "| Scope | Model | RankIC | TopK - universe label | TopK - BottomK | Dispersion | Rank turnover |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in pd.concat([all_signal, failure_signal]).itertuples(index=False):
        lines.append(
            f"| {row.scope} | {row.model} | {row.rank_ic:.6f} | {row.topk_minus_universe:.6f} | "
            f"{row.topk_minus_bottomk:.6f} | {row.prediction_dispersion:.6f} | {row.rank_turnover:.6f} |"
        )
    lines.extend(
        [
            "",
            "## Realized XGBoost portfolio",
            "",
            "| Scope | Gross excess | Explicit cost | Net excess | Excess IR | Max drawdown | Annual turnover |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in selected_portfolio.itertuples(index=False):
        lines.append(
            f"| {row.scope} | {row.gross_excess_return:.6f} | {row.explicit_transaction_cost:.6f} | "
            f"{row.net_excess_return:.6f} | {row.excess_ir:.6f} | {row.max_drawdown:.6f} | "
            f"{row.annual_turnover:.6f} |"
        )
    lines.extend(
        [
            "",
            "Cost counterfactuals reuse the same accepted PredictionSnapshot and realized gross-return path. Portfolio variants are descriptive bounded sensitivity checks and are not selected as a winner.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _validate_existing(path: Path, contract: Mapping[str, Any]) -> Path:
    manifest_path = path / ATTRIBUTION_MANIFEST_NAME
    manifest = _load_json(manifest_path, "existing attribution manifest")
    if manifest.get("contract") != dict(contract):
        raise ValueError(f"existing attribution study contract differs: {path}")
    for raw in manifest.get("artifacts", []):
        artifact = _mapping(raw, "attribution artifact")
        target = path / str(artifact.get("path") or "")
        if target.parent != path or not target.is_file() or sha256_file(target) != artifact.get("sha256"):
            raise ValueError(f"existing attribution artifact checksum mismatch: {target}")
    return manifest_path


def _publish(
    output_root: Path,
    *,
    contract: dict[str, Any],
    frames: Mapping[str, pd.DataFrame],
    summary: Mapping[str, Any],
    spec: FailureAttributionSpec,
) -> Path:
    study_id = "afa_" + sha256_json(contract)
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / study_id
    if target.exists():
        return _validate_existing(target, contract)
    building = Path(tempfile.mkdtemp(prefix=f".{study_id}.", dir=output_root))
    try:
        artifacts: list[dict[str, object]] = []
        for name, frame in frames.items():
            artifact_path = building / name
            frame.to_parquet(artifact_path, index=False)
            artifacts.append(_artifact_entry(artifact_path, rows=len(frame)))
        summary_path = building / "failure_attribution_summary.json"
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        artifacts.append(_artifact_entry(summary_path))
        report_path = building / "failure_attribution_report.md"
        _write_report(
            report_path,
            study_id=study_id,
            summary=summary,
            signal=frames["signal_conversion_attribution.parquet"],
            portfolio=frames["portfolio_attribution.parquet"],
            spec=spec,
        )
        artifacts.append(_artifact_entry(report_path))
        manifest = {
            "schemaVersion": ATTRIBUTION_STUDY_SCHEMA,
            "studyId": study_id,
            "studyType": "ALPHA_RESEARCH_PHASE1_PREDICTION_TO_PORTFOLIO_FAILURE_ATTRIBUTION",
            "contract": contract,
            "status": {
                "systemIntegrity": "PASS",
                "regimeDiagnostics": contract["regimeDiagnosticsStatus"],
                "failureAttribution": "PASS",
            },
            "primaryAlphaLossSource": summary["primaryAlphaLossSource"],
            "executionIsolation": {
                "modelTrainCalls": 0,
                "modelPredictCalls": 0,
                "featureMaterializationCalls": 0,
                "portfolioBacktestCalls": 0,
            },
            "selectionUsesFinalHoldout": False,
            "publishingAuthorized": False,
            "artifacts": artifacts,
        }
        manifest_path = building / ATTRIBUTION_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8"
        )
        try:
            os.replace(building, target)
        except OSError:
            if target.exists():
                return _validate_existing(target, contract)
            raise
        return target / ATTRIBUTION_MANIFEST_NAME
    finally:
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)


def run_attribution_diagnose(
    settings: Settings,
    *,
    regime_study: str | Path,
    acceptance: str | Path,
    walk_forward: str | Path,
    ridge_predictions: str | Path,
    lightgbm_predictions: str | Path,
    portfolio_runs: Sequence[str] = (),
    attribution_path: str | Path,
    output_root: str | Path | None = None,
) -> Path:
    regime_path = Path(regime_study).expanduser().resolve()
    acceptance_path = Path(acceptance).expanduser().resolve()
    walk_forward_root = Path(walk_forward).expanduser().resolve()
    ridge_path = Path(ridge_predictions).expanduser().resolve()
    lightgbm_path = Path(lightgbm_predictions).expanduser().resolve()
    regime_manifest, regime_labels = _validate_regime_study(regime_path, acceptance_path)
    accepted, run, selection_lock, labels, _ = _validate_acceptance_and_run(
        acceptance_path, walk_forward_root
    )
    prediction_paths = {
        "ridge": ridge_path,
        "lightgbm": lightgbm_path,
        "xgboost": run.artifact("oos_predictions.parquet"),
    }
    predictions = _load_model_predictions(
        accepted,
        ridge_path=ridge_path,
        lightgbm_path=lightgbm_path,
        xgboost_path=prediction_paths["xgboost"],
    )
    regime_prediction_hashes = _mapping(
        _mapping(regime_manifest.get("contract"), "regime contract").get("modelPredictionSha256"),
        "regime model prediction hashes",
    )
    current_hashes = {name: sha256_file(path) for name, path in sorted(prediction_paths.items())}
    if dict(regime_prediction_hashes) != current_hashes:
        raise ValueError("attribution and regime study model predictions differ")
    spec = load_failure_attribution_spec(attribution_path)
    dates = pd.DatetimeIndex(labels.index.get_level_values("datetime").unique()).normalize().sort_values()
    fold_assignments = _fold_assignments(selection_lock, dates)
    daily_signal = derive_daily_signal_conversion(
        predictions,
        labels,
        topk=spec.baseline.topk,
        minimum_cross_section=spec.minimum_cross_section,
        fold_assignments=fold_assignments,
    )
    signal = summarize_signal_conversion(daily_signal, regime_labels)
    daily_overlap = derive_daily_model_topk_overlap(
        predictions,
        labels,
        topk=spec.baseline.topk,
        minimum_cross_section=spec.minimum_cross_section,
        fold_assignments=fold_assignments,
    )
    overlap = summarize_model_topk_overlap(daily_overlap, regime_labels)
    run_inputs = _portfolio_inputs(
        portfolio_runs,
        xgboost_run=run,
        acceptance=accepted,
        spec=spec,
    )
    portfolio_frames: list[pd.DataFrame] = []
    cost_frames: list[pd.DataFrame] = []
    turnover_frames: list[pd.DataFrame] = []
    benchmark_frames: list[pd.DataFrame] = []
    rolling_benchmark_frames: list[pd.DataFrame] = []
    for portfolio_run in run_inputs:
        report = pd.read_parquet(portfolio_run.report_path)
        holdings = pd.read_parquet(portfolio_run.holdings_path)
        audit = pd.read_parquet(portfolio_run.audit_path)
        model_predictions = predictions[portfolio_run.model]
        daily_portfolio = build_daily_portfolio_bridge(
            report,
            model_predictions,
            audit,
            fold_assignments=fold_assignments,
        )
        daily_holdings = build_daily_holdings_conversion(
            holdings,
            model_predictions,
            audit,
            topk=portfolio_run.strategy.topk,
            fold_assignments=fold_assignments,
        )
        portfolio_frames.append(
            summarize_portfolio_bridge(
                daily_portfolio,
                daily_holdings,
                regime_labels,
                run_name=portfolio_run.name,
                model=portfolio_run.model,
                variant=portfolio_run.variant,
                spec=spec,
            )
        )
        benchmark_frames.append(
            derive_benchmark_diagnostics(
                daily_portfolio,
                regime_labels,
                run_name=portfolio_run.name,
                model=portfolio_run.model,
                variant=portfolio_run.variant,
                spec=spec,
            )
        )
        rolling_benchmark_frames.append(
            derive_rolling_benchmark_diagnostics(
                daily_portfolio,
                run_name=portfolio_run.name,
                model=portfolio_run.model,
                variant=portfolio_run.variant,
            )
        )
        if portfolio_run.model == "xgboost" and portfolio_run.variant == "baseline":
            cost_frames.append(
                derive_cost_sensitivity(
                    daily_portfolio,
                    regime_labels,
                    run_name=portfolio_run.name,
                    model=portfolio_run.model,
                    variant=portfolio_run.variant,
                    spec=spec,
                )
            )
        turnover_frames.append(
            derive_turnover_attribution(
                audit,
                regime_labels,
                fold_assignments=fold_assignments,
                run_name=portfolio_run.name,
                model=portfolio_run.model,
                variant=portfolio_run.variant,
            )
        )
    portfolio = pd.concat(portfolio_frames, ignore_index=True)
    cost = pd.concat(cost_frames, ignore_index=True)
    turnover = pd.concat(turnover_frames, ignore_index=True)
    benchmark = pd.concat(benchmark_frames, ignore_index=True)
    rolling_benchmark = pd.concat(rolling_benchmark_frames, ignore_index=True)
    summary = derive_failure_summary(signal, overlap, portfolio, cost, spec=spec)
    summary_frame = _summary_frame(summary)
    frames = {
        "signal_conversion_daily.parquet": daily_signal,
        "signal_conversion_attribution.parquet": signal,
        "model_topk_overlap.parquet": overlap,
        "portfolio_attribution.parquet": portfolio,
        "turnover_attribution.parquet": turnover,
        "cost_sensitivity.parquet": cost,
        "benchmark_diagnostics.parquet": benchmark,
        "rolling_benchmark_diagnostics.parquet": rolling_benchmark,
        "portfolio_sensitivity.parquet": portfolio.loc[
            portfolio["scope_type"].isin(["ALL_OOS", "FOLD"])
        ].reset_index(drop=True),
        "prediction_portfolio_attribution.parquet": summary_frame,
    }
    revision = git_revision(Path(__file__).resolve().parents[3])
    contract = {
        "schemaVersion": ATTRIBUTION_STUDY_SCHEMA,
        "dataReleaseId": selection_lock.get("dataRelease"),
        "featureSnapshotId": _mapping(accepted.get("featureSnapshot"), "FeatureSnapshot").get(
            "featureSnapshotId"
        ),
        "labelSpec": selection_lock.get("labelSpec"),
        "splitSpecSha256": _mapping(selection_lock.get("splitSpec"), "splitSpec").get("sha256"),
        "fullWalkForwardAcceptanceSha256": sha256_file(acceptance_path),
        "regimeStudyId": regime_manifest.get("studyId"),
        "regimeStudyManifestSha256": sha256_file(regime_path),
        "regimeDiagnosticsStatus": _mapping(regime_manifest.get("status"), "regime status").get(
            "regimeDiagnostics"
        ),
        "modelPredictionSha256": current_hashes,
        "portfolioInputs": {
            value.name: {
                "model": value.model,
                "variant": value.variant,
                "manifestSha256": sha256_file(value.manifest_path),
                "portfolioReportSha256": sha256_file(value.report_path),
                "holdingsSha256": sha256_file(value.holdings_path),
                "strategyAuditSha256": sha256_file(value.audit_path),
                "predictionSha256": value.prediction_sha256,
                "strategy": value.strategy.to_manifest(),
            }
            for value in sorted(run_inputs, key=lambda item: item.name)
        },
        "attributionSpec": spec.to_manifest(),
        "studyImplementationSha256": _implementation_hashes(),
        "studyCodeCommit": revision.get("commit"),
        "studyCodeDirty": revision.get("dirty"),
        "modelTrainCalls": 0,
        "modelPredictCalls": 0,
        "featureMaterializationCalls": 0,
        "portfolioBacktestCalls": 0,
        "selectionUsesFinalHoldout": False,
        "publishingAuthorized": False,
    }
    destination = (
        Path(output_root).expanduser().resolve()
        if output_root is not None
        else settings.paths.output / "research" / "alpha_phase1" / "attribution"
    )
    return _publish(destination, contract=contract, frames=frames, summary=summary, spec=spec)
