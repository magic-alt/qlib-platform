from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESEARCH_ROOT = _REPO_ROOT / "src" / "qlib_platform" / "research"

_CANONICAL_PHASE_REPLACEMENTS = {
    "phase1_synthesis.py": "reporting/synthesis.py",
    "phase2_collector.py": "evidence/collector.py",
    "phase2_contract.py": "contracts/candidate_program.py",
    "phase2_data_acceptance.py": "evidence/data_acceptance.py",
    "phase2_features.py": "features/candidate_sets.py",
    "phase2_hypotheses.py": "hypotheses/catalog.py",
    "phase2_overlays.py": "portfolio/overlays.py",
    "phase2_program.py": "workflow/candidate_program.py",
    "phase2_selection.py": "evaluation/selection.py",
    "phase2_statistics.py": "evaluation/candidate_statistics.py",
    "phase3_contract.py": "contracts/stability_program.py",
    "phase3_decay.py": "diagnostics/decay.py",
    "phase3_diagnostics.py": "diagnostics/stability.py",
    "phase3_portability.py": "diagnostics/portability.py",
    "phase3_program.py": "workflow/stability_program.py",
}


def test_phase_named_runtime_modules_are_compatibility_only() -> None:
    for legacy_name, canonical_name in _CANONICAL_PHASE_REPLACEMENTS.items():
        legacy = _RESEARCH_ROOT / legacy_name
        canonical = _RESEARCH_ROOT / canonical_name
        assert canonical.is_file(), f"missing canonical research module: {canonical_name}"
        source = legacy.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 4, f"{legacy_name} grew beyond a compatibility shim"
        assert "def " not in source
        assert "class " not in source
        assert "Compatibility shim" in source


def test_programs_share_immutable_artifact_writer() -> None:
    candidate_program = (_RESEARCH_ROOT / "workflow" / "candidate_program.py").read_text(
        encoding="utf-8"
    )
    stability_program = (_RESEARCH_ROOT / "workflow" / "stability_program.py").read_text(
        encoding="utf-8"
    )
    for source in (candidate_program, stability_program):
        assert "from qlib_platform.research.artifact_io import write_immutable_json" in source
        assert "def _write_immutable" not in source


def test_research_architecture_rules_document_canonical_packages() -> None:
    rules = (_RESEARCH_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for package in (
        "contracts/",
        "evidence/",
        "features/",
        "hypotheses/",
        "workflow/",
        "evaluation/",
        "diagnostics/",
        "portfolio/",
        "reporting/",
    ):
        assert package in rules
