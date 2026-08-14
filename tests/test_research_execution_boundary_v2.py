from __future__ import annotations

from pathlib import Path

from tushare_qlib.institutional_artifacts import ResearchArtifactType, ResearchPromotionStatus


ROOT = Path(__file__).resolve().parents[1]
RESEARCH_OWNED = (
    "platform_release.py",
    "institutional_artifacts.py",
    "feature_store.py",
    "train_select.py",
    "walk_forward.py",
    "research_gate.py",
    "lean_bridge.py",
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
    for name in RESEARCH_OWNED:
        source = (ROOT / "src" / "tushare_qlib" / name).read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), name
