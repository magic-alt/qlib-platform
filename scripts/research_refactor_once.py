from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "src" / "qlib_platform" / "research"

MODULE_MOVES = {
    "artifact_io.py": "artifacts/io.py",
    "attribution_study.py": "studies/attribution.py",
    "explanation_study.py": "studies/explanation.py",
    "factor_clusters.py": "features/clusters.py",
    "factor_taxonomy.py": "features/taxonomy.py",
    "failure_attribution.py": "diagnostics/failure_attribution.py",
    "feature_diagnostics.py": "diagnostics/features.py",
    "feature_store.py": "features/store.py",
    "full_walk_forward_acceptance.py": "evaluation/full_walk_forward.py",
    "model_explanation.py": "diagnostics/model_explanation.py",
    "p0_baseline.py": "workflow/baseline.py",
    "portfolio_attribution.py": "diagnostics/portfolio_attribution.py",
    "regime.py": "diagnostics/regimes.py",
    "regime_diagnostics.py": "diagnostics/regime_analysis.py",
    "regime_study.py": "studies/regime.py",
    "research_cli_ux.py": "interfaces/cli_ux.py",
    "research_experiment.py": "workflow/experiment.py",
    "research_gate.py": "evaluation/gates.py",
    "research_quickstart.py": "workflow/quickstart.py",
    "research_summary.py": "reporting/summary.py",
    "research_timing.py": "workflow/timing.py",
    "study.py": "studies/alpha.py",
    "synthesis_study.py": "studies/synthesis.py",
    "train_select.py": "workflow/train_select.py",
    "turnover_attribution.py": "diagnostics/turnover_attribution.py",
    "walk_forward.py": "workflow/walk_forward.py",
    "walk_forward_acceptance.py": "evaluation/walk_forward.py",
}

LEGACY_MODULE_REPLACEMENTS = {
    "qlib_platform.research.phase1_synthesis": "qlib_platform.research.reporting.synthesis",
    "qlib_platform.research.phase2_collector": "qlib_platform.research.evidence.collector",
    "qlib_platform.research.phase2_contract": "qlib_platform.research.contracts.candidate_program",
    "qlib_platform.research.phase2_data_acceptance": "qlib_platform.research.evidence.data_acceptance",
    "qlib_platform.research.phase2_features": "qlib_platform.research.features.candidate_sets",
    "qlib_platform.research.phase2_hypotheses": "qlib_platform.research.hypotheses.catalog",
    "qlib_platform.research.phase2_overlays": "qlib_platform.research.portfolio.overlays",
    "qlib_platform.research.phase2_program": "qlib_platform.research.workflow.candidate_program",
    "qlib_platform.research.phase2_selection": "qlib_platform.research.evaluation.selection",
    "qlib_platform.research.phase2_statistics": "qlib_platform.research.evaluation.candidate_statistics",
    "qlib_platform.research.phase3_contract": "qlib_platform.research.contracts.stability_program",
    "qlib_platform.research.phase3_decay": "qlib_platform.research.diagnostics.decay",
    "qlib_platform.research.phase3_diagnostics": "qlib_platform.research.diagnostics.stability",
    "qlib_platform.research.phase3_portability": "qlib_platform.research.diagnostics.portability",
    "qlib_platform.research.phase3_program": "qlib_platform.research.workflow.stability_program",
}

ROOT_MODULE_REPLACEMENTS = {
    "qlib_platform.research.artifact_io": "qlib_platform.research.artifacts.io",
    "qlib_platform.research.attribution_study": "qlib_platform.research.studies.attribution",
    "qlib_platform.research.explanation_study": "qlib_platform.research.studies.explanation",
    "qlib_platform.research.factor_clusters": "qlib_platform.research.features.clusters",
    "qlib_platform.research.factor_taxonomy": "qlib_platform.research.features.taxonomy",
    "qlib_platform.research.failure_attribution": "qlib_platform.research.diagnostics.failure_attribution",
    "qlib_platform.research.feature_diagnostics": "qlib_platform.research.diagnostics.features",
    "qlib_platform.research.feature_store": "qlib_platform.research.features.store",
    "qlib_platform.research.full_walk_forward_acceptance": "qlib_platform.research.evaluation.full_walk_forward",
    "qlib_platform.research.model_explanation": "qlib_platform.research.diagnostics.model_explanation",
    "qlib_platform.research.p0_baseline": "qlib_platform.research.workflow.baseline",
    "qlib_platform.research.portfolio_attribution": "qlib_platform.research.diagnostics.portfolio_attribution",
    "qlib_platform.research.regime_diagnostics": "qlib_platform.research.diagnostics.regime_analysis",
    "qlib_platform.research.regime_study": "qlib_platform.research.studies.regime",
    "qlib_platform.research.regime": "qlib_platform.research.diagnostics.regimes",
    "qlib_platform.research.research_cli_ux": "qlib_platform.research.interfaces.cli_ux",
    "qlib_platform.research.research_experiment": "qlib_platform.research.workflow.experiment",
    "qlib_platform.research.research_gate": "qlib_platform.research.evaluation.gates",
    "qlib_platform.research.research_quickstart": "qlib_platform.research.workflow.quickstart",
    "qlib_platform.research.research_summary": "qlib_platform.research.reporting.summary",
    "qlib_platform.research.research_timing": "qlib_platform.research.workflow.timing",
    "qlib_platform.research.synthesis_study": "qlib_platform.research.studies.synthesis",
    "qlib_platform.research.train_select": "qlib_platform.research.workflow.train_select",
    "qlib_platform.research.turnover_attribution": "qlib_platform.research.diagnostics.turnover_attribution",
    "qlib_platform.research.walk_forward_acceptance": "qlib_platform.research.evaluation.walk_forward",
    "qlib_platform.research.walk_forward": "qlib_platform.research.workflow.walk_forward",
    "qlib_platform.research.study": "qlib_platform.research.studies.alpha",
}

PATH_MOVES = {
    "configs/pipeline_phase2.yaml": "configs/pipeline_candidate_research.yaml",
    "configs/portfolio/rank_buffer_phase2_v1.yaml": "configs/portfolio/rank_buffer_candidate_v1.yaml",
    "configs/regimes/ashare_phase2_overlay_v1.yaml": "configs/regimes/ashare_candidate_overlay_v1.yaml",
    "configs/research/ashare_phase2_v1.yaml": "configs/research/ashare_candidate_research_v1.yaml",
    "configs/research/ashare_phase3_v1.yaml": "configs/research/ashare_stability_diagnostics_v1.yaml",
    "configs/synthesis/ashare_phase1_synthesis_v1.yaml": "configs/synthesis/ashare_research_synthesis_v1.yaml",
    ".agents/skills/research-diagnostics/references/phase3-d.md": ".agents/skills/research-diagnostics/references/stability-diagnostics.md",
    "docs/alpha_research_phase_3.md": "docs/alpha_research_stability.md",
    "docs/history/research/alpha_research_phase_1.md": "docs/history/research/alpha_research_initial_synthesis.md",
    "docs/history/research/alpha_research_phase_2.md": "docs/history/research/alpha_research_candidate_program.md",
}

TEST_MOVES = {
    "tests/_phase3_helpers.py": "tests/_stability_helpers.py",
    "tests/failure_injection/test_phase3_contract_failures.py": "tests/failure_injection/test_stability_contract_failures.py",
    "tests/test_architecture_phase3.py": "tests/test_architecture_stability.py",
    "tests/test_phase1_synthesis.py": "tests/test_research_synthesis.py",
    "tests/test_phase2_collector.py": "tests/test_candidate_evidence_collector.py",
    "tests/test_phase2_contract.py": "tests/test_candidate_program_contract.py",
    "tests/test_phase2_features.py": "tests/test_candidate_feature_sets.py",
    "tests/test_phase2_hypotheses.py": "tests/test_candidate_hypotheses.py",
    "tests/test_phase2_overlays.py": "tests/test_candidate_portfolio_overlays.py",
    "tests/test_phase2_program.py": "tests/test_candidate_program.py",
    "tests/test_phase2_selection.py": "tests/test_candidate_selection.py",
    "tests/test_phase2_statistics.py": "tests/test_candidate_statistics.py",
    "tests/test_phase3_contract.py": "tests/test_stability_program_contract.py",
    "tests/test_phase3_decay.py": "tests/test_stability_decay.py",
    "tests/test_phase3_diagnostics.py": "tests/test_stability_diagnostics.py",
    "tests/test_phase3_diagnostics_integrity.py": "tests/test_stability_diagnostics_integrity.py",
    "tests/test_phase3_portability.py": "tests/test_stability_portability.py",
    "tests/test_phase3_program.py": "tests/test_stability_program.py",
    "tests/test_settings_phase2.py": "tests/test_settings_candidate_research.py",
}

CLI_REPLACEMENTS = {
    "phase1-synthesize": "research-synthesize",
    "phase2-validate": "candidate-validate",
    "phase2-plan": "candidate-plan",
    "phase2-data-accept": "candidate-data-accept",
    "phase2-collect": "candidate-collect",
    "phase2-accept": "candidate-accept",
    "phase2-select": "candidate-select",
    "phase2-final-holdout-open": "final-holdout-open",
    "phase3-validate": "stability-validate",
    "phase3-plan": "stability-plan",
    "phase3-diagnose": "stability-diagnose",
    "phase3-portable-export": "stability-portable-export",
    "phase3-portable-verify": "stability-portable-verify",
    "--phase1-manifest": "--synthesis-manifest",
    "--phase2-acceptance": "--candidate-acceptance",
    "--phase2-evidence": "--candidate-evidence",
    "--phase2-data-acceptance": "--candidate-data-acceptance",
    "args.phase1_manifest": "args.synthesis_manifest",
    "args.phase2_acceptance": "args.candidate_acceptance",
    "args.phase2_evidence": "args.candidate_evidence",
    "args.phase2_data_acceptance": "args.candidate_data_acceptance",
}

API_REPLACEMENTS = {
    "run_phase1_synthesis": "run_research_synthesis",
    "bind_phase2_hypothesis": "bind_candidate_hypothesis",
    "load_phase2_contract": "load_candidate_contract",
    "load_phase2_lock": "load_candidate_lock",
    "write_phase2_contract_lock": "write_candidate_contract_lock",
    "collect_phase2_evidence": "collect_candidate_evidence",
    "write_phase2_experiment_plan": "write_candidate_experiment_plan",
    "write_phase2_selection_lock": "write_candidate_selection_lock",
    "PHASE2_INCREMENTAL_CANDIDATE_FAMILY": "INCREMENTAL_CANDIDATE_FAMILY",
    "load_phase3_contract": "load_stability_contract",
    "load_phase3_lock": "load_stability_lock",
    "write_phase3_contract_lock": "write_stability_contract_lock",
    "load_phase3_plan": "load_stability_plan",
    "write_phase3_experiment_plan": "write_stability_experiment_plan",
    "run_phase3_diagnose": "run_stability_diagnostics",
    "export_phase3_portable_evidence": "export_stability_portable_evidence",
    "verify_phase3_portable_evidence": "verify_stability_portable_evidence",
    "PHASE3_EXECUTION_ORDER": "STABILITY_EXECUTION_ORDER",
    "PHASE3_DIAGNOSTICS_SCHEMA": "STABILITY_DIAGNOSTICS_SCHEMA",
    "PHASE3_EVIDENCE_INDEX_SCHEMA": "STABILITY_EVIDENCE_INDEX_SCHEMA",
    "PHASE3_MANIFEST_NAME": "STABILITY_MANIFEST_NAME",
    "PHASE3_PORTABLE_EVIDENCE_SCHEMA": "STABILITY_PORTABLE_EVIDENCE_SCHEMA",
    "PHASE3_PORTABLE_EVIDENCE_MANIFEST": "STABILITY_PORTABLE_EVIDENCE_MANIFEST",
}

PARSER_VARIABLE_REPLACEMENTS = {
    "phase1_synthesize": "research_synthesize",
    "phase2_validate": "candidate_validate",
    "phase2_plan": "candidate_plan",
    "phase2_data_accept": "candidate_data_accept",
    "phase2_collect": "candidate_collect",
    "phase2_accept": "candidate_accept",
    "phase2_select": "candidate_select",
    "phase2_holdout": "final_holdout",
    "phase3_validate": "stability_validate",
    "phase3_plan": "stability_plan",
    "phase3_diagnose": "stability_diagnose",
    "phase3_export": "stability_export",
    "phase3_verify": "stability_verify",
}

TEXT_SUFFIXES = {
    ".py", ".md", ".yml", ".yaml", ".toml", ".json", ".ps1", ".sh", ".txt", ".ini", ".cfg"
}


def move(relative_source: str, relative_target: str) -> None:
    source = ROOT / relative_source
    target = ROOT / relative_target
    if not source.exists():
        return
    if target.exists():
        raise RuntimeError(f"refusing to overwrite existing target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def replace_text(path: Path, replacements: dict[str, str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    updated = text
    for old, new in replacements.items():
        updated = updated.replace(old, new)
    if updated != text:
        path.write_text(updated, encoding="utf-8")


def text_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Dockerfile", "Makefile"}:
            files.append(path)
    return files


def main() -> None:
    # Move active research modules into stable responsibility packages.
    for source, target in MODULE_MOVES.items():
        move(f"src/qlib_platform/research/{source}", f"src/qlib_platform/research/{target}")

    for package in ("artifacts", "interfaces", "studies"):
        init = RESEARCH / package / "__init__.py"
        init.parent.mkdir(parents=True, exist_ok=True)
        init.touch(exist_ok=True)

    # Rename active configuration, documentation and test files away from research-stage numbering.
    for source, target in PATH_MOVES.items():
        move(source, target)
    for source, target in TEST_MOVES.items():
        move(source, target)

    replacements: dict[str, str] = {}
    replacements.update(LEGACY_MODULE_REPLACEMENTS)
    replacements.update(ROOT_MODULE_REPLACEMENTS)
    replacements.update({old: new for old, new in PATH_MOVES.items()})
    replacements.update({old: new for old, new in TEST_MOVES.items()})
    replacements.update(CLI_REPLACEMENTS)
    replacements.update(API_REPLACEMENTS)
    replacements.update(PARSER_VARIABLE_REPLACEMENTS)
    replacements["_phase3_helpers"] = "_stability_helpers"

    for path in text_files():
        replace_text(path, replacements)

    # Physically remove every former phase-oriented runtime module. There are no compatibility shims.
    for path in RESEARCH.glob("phase[123]_*.py"):
        path.unlink()

    # Convert the capability guard in the CLI from stage-prefix matching to explicit research responsibilities.
    cli_main = ROOT / "src/qlib_platform/cli/main.py"
    text = cli_main.read_text(encoding="utf-8")
    old = '''    if args.command.startswith("candidate-") or (\n        args.command.startswith("stability-") and args.command != "stability-portable-verify"\n    ):\n        from qlib_platform.releases.capabilities import require_release_capability\n\n        require_release_capability(\n            settings,\n            "phase2" if args.command.startswith("candidate-") else "phase3",\n        )\n'''
    new = '''    candidate_commands = {\n        "candidate-validate",\n        "candidate-plan",\n        "candidate-data-accept",\n        "candidate-collect",\n        "candidate-accept",\n        "candidate-select",\n        "final-holdout-open",\n    }\n    stability_commands = {\n        "stability-validate",\n        "stability-plan",\n        "stability-diagnose",\n        "stability-portable-export",\n    }\n    if args.command in candidate_commands or args.command in stability_commands:\n        from qlib_platform.releases.capabilities import require_release_capability\n\n        # Capability identifiers are persisted governance identities and remain backward compatible.\n        require_release_capability(\n            settings,\n            "phase2" if args.command in candidate_commands else "phase3",\n        )\n'''
    if old not in text:
        raise RuntimeError("expected research capability guard was not found after CLI migration")
    cli_main.write_text(text.replace(old, new), encoding="utf-8")

    # Strengthen the architecture contract: no stage-named runtime modules or legacy imports may return.
    architecture_test = ROOT / "tests/research/test_module_architecture.py"
    architecture_test.parent.mkdir(parents=True, exist_ok=True)
    architecture_test.write_text(
        '''from __future__ import annotations\n\nimport re\nfrom pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[2]\nRESEARCH = ROOT / "src" / "qlib_platform" / "research"\nLEGACY_IMPORT = re.compile(r"qlib_platform\\.research\\.phase[123]_")\nLEGACY_COMMAND = re.compile(r"[\\\"']phase[123]-")\n\n\ndef _text_files(root: Path):\n    for path in root.rglob("*"):\n        if path.is_file() and path.suffix.lower() in {".py", ".md", ".yml", ".yaml", ".toml", ".ps1", ".sh"}:\n            yield path\n\n\ndef test_research_runtime_has_no_phase_named_modules() -> None:\n    offenders = sorted(path.relative_to(ROOT).as_posix() for path in RESEARCH.glob("phase[123]_*.py"))\n    assert offenders == []\n\n\ndef test_repository_has_no_legacy_research_module_imports() -> None:\n    offenders = []\n    for path in _text_files(ROOT):\n        text = path.read_text(encoding="utf-8")\n        if LEGACY_IMPORT.search(text):\n            offenders.append(path.relative_to(ROOT).as_posix())\n    assert offenders == []\n\n\ndef test_cli_has_no_phase_numbered_commands() -> None:\n    parser_source = (ROOT / "src/qlib_platform/cli/commands/research.py").read_text(encoding="utf-8")\n    assert LEGACY_COMMAND.search(parser_source) is None\n\n\ndef test_research_root_contains_only_package_boundary_files() -> None:\n    runtime_files = sorted(path.name for path in RESEARCH.glob("*.py"))\n    assert runtime_files == ["__init__.py"]\n''',
        encoding="utf-8",
    )

    (RESEARCH / "README.md").write_text(
        '''# Research package architecture\n\n`qlib_platform.research` is organized by durable research responsibility rather than historical experiment stage.\n\n- `contracts/`: frozen candidate and stability design contracts.\n- `evidence/`: evidence collection and data-release acceptance.\n- `features/`: feature stores, taxonomies, clusters, and candidate feature sets.\n- `hypotheses/`: pre-registered hypothesis bindings.\n- `workflow/`: baseline, candidate, stability, training, timing, and walk-forward orchestration.\n- `evaluation/`: candidate statistics, selection, promotion gates, and walk-forward acceptance.\n- `diagnostics/`: stability, decay, regimes, attribution, explanation, and portability analysis.\n- `studies/`: alpha, regime, attribution, explanation, and synthesis study composition.\n- `portfolio/`: bounded portfolio overlays.\n- `reporting/`: synthesis payloads and research summaries.\n- `artifacts/`: immutable research artifact I/O.\n- `interfaces/`: research-facing interface helpers.\n\nHistorical stage identifiers may remain inside immutable artifact schema values or governance state where changing them would break lineage. They must not be used as Python module boundaries, import paths, filenames, or CLI command names.\n''',
        encoding="utf-8",
    )

    agents = RESEARCH / "AGENTS.md"
    if agents.exists():
        text = agents.read_text(encoding="utf-8")
        marker = "## Responsibility-oriented package boundary"
        if marker not in text:
            text += (
                "\n\n## Responsibility-oriented package boundary\n\n"
                "Runtime research code must live in the responsibility packages documented in `README.md`. "
                "Do not add stage-numbered modules, compatibility shims, or phase-numbered CLI commands. "
                "Historical stage identifiers are permitted only when they are immutable artifact or governance identities.\n"
            )
            agents.write_text(text, encoding="utf-8")

    # Fail the migration if any old Python module path survives. Artifact schema strings are intentionally excluded.
    legacy_paths: list[str] = []
    legacy_import_re = re.compile(r"qlib_platform\.research\.phase[123]_")
    for path in text_files():
        text = path.read_text(encoding="utf-8")
        if legacy_import_re.search(text):
            legacy_paths.append(path.relative_to(ROOT).as_posix())
    if legacy_paths:
        raise RuntimeError(f"legacy phase research imports remain: {legacy_paths}")

    phase_files = [path.relative_to(ROOT).as_posix() for path in RESEARCH.glob("phase[123]_*.py")]
    if phase_files:
        raise RuntimeError(f"legacy phase runtime files remain: {phase_files}")

    # One-shot migration machinery removes itself before the resulting commit.
    (ROOT / ".github/workflows/research-refactor-once.yml").unlink(missing_ok=True)
    Path(__file__).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
