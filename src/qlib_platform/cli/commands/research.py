"""Register CLI commands owned by the research domain."""

from qlib_platform.runtime.runtime_resources import resource_argument


def register(sub) -> None:
    ts = sub.add_parser("train-select")
    ts.add_argument("--train", nargs=2, metavar=("START", "END"))
    ts.add_argument("--valid", nargs=2, metavar=("START", "END"))
    ts.add_argument("--test", nargs=2, metavar=("START", "END"))
    ts.add_argument("--benchmark")
    ts.add_argument("--topn", type=int)
    ts.add_argument("--model-profile")
    ts.add_argument("--stage", choices=["signal", "release"], default="release")
    ts.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    ts.add_argument("--dataset-ref")
    rr = sub.add_parser("research-run")
    rr.add_argument("--mode", choices=["fixed", "walk-forward"], default="fixed")
    rr.add_argument("--start")
    rr.add_argument("--end")
    rr.add_argument("--benchmark", default="SH000300")
    rr.add_argument("--topn", type=int)
    rr.add_argument("--model-profile")
    rr.add_argument("--stage", choices=["signal", "release"], default="release")
    rr.add_argument("--artifact-level", choices=["minimal", "full"], default="full")
    rr.add_argument("--dataset-ref")
    rr.add_argument("--full-acceptance", action="store_true")
    rr.add_argument("--interrupt-after-fold", type=int)
    rr.add_argument("--checkpoint-namespace", default="default")
    rr.add_argument("--feature-set")
    rr.add_argument("--selected-technical", action="append", default=[])
    rr.add_argument("--hypothesis-id")
    rr.add_argument("--hypothesis-role", choices=["candidate", "baseline"], default="candidate")
    rr.add_argument("--contract-lock")
    rp = sub.add_parser("research-report")
    rp.add_argument("run_dir")
    rp.add_argument("--positions-file")
    alpha_diagnose = sub.add_parser("alpha-diagnose")
    alpha_diagnose.add_argument("--acceptance", required=True)
    alpha_diagnose.add_argument("--walk-forward", required=True)
    alpha_diagnose.add_argument("--feature-snapshot", required=True)
    alpha_diagnose.add_argument(
        "--taxonomy", default=resource_argument("configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    )
    alpha_diagnose.add_argument("--output")
    regime_diagnose = sub.add_parser("regime-diagnose")
    regime_diagnose.add_argument("--base-study", required=True)
    regime_diagnose.add_argument("--acceptance", required=True)
    regime_diagnose.add_argument("--walk-forward", required=True)
    regime_diagnose.add_argument("--ridge-predictions", required=True)
    regime_diagnose.add_argument("--lightgbm-predictions", required=True)
    regime_diagnose.add_argument("--feature-snapshot", required=True)
    regime_diagnose.add_argument(
        "--taxonomy", default=resource_argument("configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    )
    regime_diagnose.add_argument(
        "--regimes", default=resource_argument("configs/regimes/ashare_regime_v1.yaml")
    )
    regime_diagnose.add_argument("--output")
    attribution_diagnose = sub.add_parser("attribution-diagnose")
    attribution_diagnose.add_argument("--regime-study", required=True)
    attribution_diagnose.add_argument("--acceptance", required=True)
    attribution_diagnose.add_argument("--walk-forward", required=True)
    attribution_diagnose.add_argument("--ridge-predictions", required=True)
    attribution_diagnose.add_argument("--lightgbm-predictions", required=True)
    attribution_diagnose.add_argument(
        "--portfolio-run",
        action="append",
        default=[],
        metavar="MODEL:VARIANT=PATH",
        help="optional certified baseline or bounded prediction-only portfolio input",
    )
    attribution_diagnose.add_argument(
        "--attribution",
        default=resource_argument("configs/attribution/ashare_failure_attribution_v1.yaml"),
    )
    attribution_diagnose.add_argument("--output")
    explanation_diagnose = sub.add_parser("explanation-diagnose")
    explanation_diagnose.add_argument("--base-study", required=True)
    explanation_diagnose.add_argument("--regime-study", required=True)
    explanation_diagnose.add_argument("--attribution-study", required=True)
    explanation_diagnose.add_argument("--acceptance", required=True)
    explanation_diagnose.add_argument("--ridge-walk-forward", required=True)
    explanation_diagnose.add_argument("--lightgbm-walk-forward", required=True)
    explanation_diagnose.add_argument("--xgboost-walk-forward", required=True)
    explanation_diagnose.add_argument("--feature-snapshot", required=True)
    explanation_diagnose.add_argument(
        "--taxonomy", default=resource_argument("configs/alpha_taxonomy/alpha158_pit_v1.yaml")
    )
    explanation_diagnose.add_argument(
        "--model-artifact-root",
        action="append",
        required=True,
        metavar="PATH",
        help="local MLflow/Qlib recorder root containing RUN_ID/artifacts/params.pkl",
    )
    explanation_diagnose.add_argument(
        "--explanation",
        default=resource_argument("configs/explanation/ashare_model_explanation_v1.yaml"),
    )
    explanation_diagnose.add_argument("--output")
    research_synthesize = sub.add_parser("research-synthesize")
    research_synthesize.add_argument("--feature-study", required=True)
    research_synthesize.add_argument("--regime-study", required=True)
    research_synthesize.add_argument("--attribution-study", required=True)
    research_synthesize.add_argument("--explanation-study", required=True)
    research_synthesize.add_argument(
        "--synthesis",
        default=resource_argument("configs/synthesis/ashare_research_synthesis_v1.yaml"),
    )
    research_synthesize.add_argument("--output")
    candidate_validate = sub.add_parser("candidate-validate")
    candidate_validate.add_argument("--synthesis-manifest", required=True)
    candidate_validate.add_argument(
        "--contract",
        default=resource_argument("configs/research/ashare_candidate_research_v1.yaml"),
    )
    candidate_validate.add_argument("--output", required=True)
    candidate_plan = sub.add_parser("candidate-plan")
    candidate_plan.add_argument("--contract-lock", required=True)
    candidate_plan.add_argument("--output", required=True)
    candidate_data_accept = sub.add_parser("candidate-data-accept")
    candidate_data_accept.add_argument("--evidence", required=True)
    candidate_data_accept.add_argument("--output", required=True)
    candidate_collect = sub.add_parser("candidate-collect")
    candidate_collect.add_argument("--contract-lock", required=True)
    candidate_collect.add_argument("--evidence", required=True)
    candidate_collect.add_argument("--output", required=True)
    candidate_accept = sub.add_parser("candidate-accept")
    candidate_accept.add_argument("--contract-lock", required=True)
    candidate_accept.add_argument("--candidate-metrics", "--candidates", dest="candidate_metrics", required=True)
    candidate_accept.add_argument("--output", required=True)
    candidate_select = sub.add_parser("candidate-select")
    candidate_select.add_argument("--contract-lock", required=True)
    candidate_select.add_argument("--acceptance", required=True)
    candidate_select.add_argument("--design-release", required=True)
    candidate_select.add_argument("--selection-date", required=True)
    candidate_select.add_argument("--output", required=True)
    final_holdout = sub.add_parser("final-holdout-open")
    final_holdout.add_argument("--selection-lock", required=True)
    final_holdout.add_argument("--final-release", required=True)
    final_holdout.add_argument("--calendar", required=True)
    final_holdout.add_argument("--output", required=True)
    stability_validate = sub.add_parser("stability-validate")
    stability_validate.add_argument("--candidate-acceptance", required=True)
    stability_validate.add_argument("--candidate-evidence", required=True)
    stability_validate.add_argument("--candidate-data-acceptance", required=True)
    stability_validate.add_argument(
        "--contract",
        default=resource_argument("configs/research/ashare_stability_diagnostics_v1.yaml"),
    )
    stability_validate.add_argument("--output", required=True)
    stability_plan = sub.add_parser("stability-plan")
    stability_plan.add_argument("--contract-lock", required=True)
    stability_plan.add_argument("--output", required=True)
    stability_diagnose = sub.add_parser("stability-diagnose")
    stability_diagnose.add_argument("--contract-lock", required=True)
    stability_diagnose.add_argument("--plan", required=True)
    stability_diagnose.add_argument("--evidence", required=True)
    stability_diagnose.add_argument(
        "--regimes",
        default=resource_argument("configs/regimes/ashare_regime_v1.yaml"),
    )
    stability_diagnose.add_argument("--output", required=True)
    stability_export = sub.add_parser("stability-portable-export")
    stability_export.add_argument("--contract-lock", required=True)
    stability_export.add_argument("--plan", required=True)
    stability_export.add_argument("--diagnosis", required=True)
    stability_export.add_argument(
        "--contract", default=resource_argument("configs/research/ashare_stability_diagnostics_v1.yaml")
    )
    stability_export.add_argument("--data-root", required=True)
    stability_export.add_argument("--output", required=True)
    stability_verify = sub.add_parser("stability-portable-verify")
    stability_verify.add_argument("--package", required=True)
    rg = sub.add_parser("research-gate")
    rg.add_argument("metrics_json")
    rg.add_argument("--output")
    ra = sub.add_parser("research-audit")
    ra.add_argument("run_dir")
    ra.add_argument("--output")
