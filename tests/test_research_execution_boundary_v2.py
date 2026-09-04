from __future__ import annotations

from pathlib import Path

from qlib_platform.artifacts.institutional_artifacts import ResearchArtifactType, ResearchPromotionStatus


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_OWNED = (
    "ops/platform_release.py",
    "artifacts/institutional_artifacts.py",
    "research/features/store.py",
    "research/workflow/train_select.py",
    "research/workflow/walk_forward.py",
    "research/evaluation/gates.py",
    "ops/lean_bridge.py",
)


def test_v2_contract_cannot_publish_execution_assets_or_downstream_states():
    assert {item.value for item in ResearchArtifactType}.isdisjoint(
        {"ORDER_INTENT", "BROKER_ORDER", "FILL", "POSITION", "CASH_LEDGER", "RISK_DECISION"}
    )
    assert {item.value for item in ResearchPromotionStatus}.isdisjoint(
        {"LEAN_VALIDATED", "PAPER", "PRODUCTION", "RETIRED"}
    )


def test_new_research_boundary_does_not_import_legacy_execution_domains():
    forbidden = (
        "from .execution import",
        "from .risk_engine import",
        "from .broker_state import",
        "from .holdings_ledger import",
        "from .qmt_gateway",
    )
    package = ROOT / "src" / "qlib_platform"
    for name in RESEARCH_OWNED:
        source = (package / name).read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), name
    assert not (package / "broker").exists()
    assert not (package / "qmt_gateway").exists()
