"""Read-only, non-publishing alpha research studies."""

from qlib_platform.research.feature_diagnostics import FeatureDiagnosticsSpec
from qlib_platform.research.failure_attribution import FailureAttributionSpec
from qlib_platform.research.model_explanation import ModelExplanationSpec
from qlib_platform.research.phase1_synthesis import Phase1SynthesisSpec
from qlib_platform.research.regime import RegimeSpec
from qlib_platform.research.attribution_study import run_attribution_diagnose
from qlib_platform.research.regime_study import run_regime_diagnose
from qlib_platform.research.explanation_study import run_explanation_diagnose
from qlib_platform.research.study import run_alpha_diagnose
from qlib_platform.research.synthesis_study import run_phase1_synthesis
from qlib_platform.research.phase2_contract import Phase2Contract, load_phase2_contract, write_phase2_contract_lock
from qlib_platform.research.phase3_contract import Phase3Contract, load_phase3_contract, write_phase3_contract_lock

__all__ = [
    "FailureAttributionSpec",
    "FeatureDiagnosticsSpec",
    "ModelExplanationSpec",
    "Phase1SynthesisSpec",
    "RegimeSpec",
    "run_alpha_diagnose",
    "run_attribution_diagnose",
    "run_explanation_diagnose",
    "run_regime_diagnose",
    "run_phase1_synthesis",
    "Phase2Contract",
    "load_phase2_contract",
    "write_phase2_contract_lock",
    "Phase3Contract",
    "load_phase3_contract",
    "write_phase3_contract_lock",
]
