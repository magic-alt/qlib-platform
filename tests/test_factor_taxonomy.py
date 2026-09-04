from __future__ import annotations

from pathlib import Path

import pytest

from qlib_platform.custom_handler import TushareAlpha158Fundamental
from qlib_platform.research.factor_taxonomy import load_factor_taxonomy


def _alpha158_names() -> list[str]:
    handler = object.__new__(TushareAlpha158Fundamental)
    _, names = handler.get_feature_config()
    return list(names)


def test_alpha158_pit_taxonomy_exactly_covers_current_182_features():
    names = _alpha158_names()
    taxonomy = load_factor_taxonomy(
        "configs/alpha_taxonomy/alpha158_pit_v1.yaml",
        expected_features=names,
        expected_alpha_pack_id="alpha158_pit_v1",
    )

    assert len(names) == len(taxonomy.entries) == 182
    assert taxonomy.entry("NET_MF_5").family == "Flow"
    assert taxonomy.entry("LOG_CIRC_MV").role == "exposure"
    assert taxonomy.entry("CIRC_MV").role == "support"
    assert taxonomy.entry("MONEY20").role == "support"
    assert taxonomy.entry("PB").direction == "negative"
    assert not taxonomy.entry("PAUSED").ranking_eligible


def test_taxonomy_rejects_duplicate_or_incomplete_features(tmp_path: Path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        """schema: alpha_factor_taxonomy_v1
taxonomyId: test
alphaPackId: pack
features:
  F: {family: Momentum, role: alpha, direction: positive}
  F: {family: Momentum, role: alpha, direction: negative}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate taxonomy key"):
        load_factor_taxonomy(duplicate)

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text(
        """schema: alpha_factor_taxonomy_v1
taxonomyId: test
alphaPackId: pack
features:
  F: {family: Momentum, role: alpha, direction: positive}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="does not exactly cover"):
        load_factor_taxonomy(incomplete, expected_features=["F", "G"])
