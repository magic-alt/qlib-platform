from __future__ import annotations

from pathlib import Path

from tushare_qlib.research_timing import label_spec_from_settings
from tushare_qlib.settings import Paths, Settings


def test_open_label_spec_uses_open_to_open_expression_and_identity(tmp_path: Path) -> None:
    settings = Settings(
        config_path=tmp_path / "pipeline.yaml",
        data={"experiment": {"label": {"spec": "return_5d_t1_open_v1"}}},
        paths=Paths.from_root(tmp_path / "data"),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=tmp_path / "qlib",
    )

    label = label_spec_from_settings(settings)

    assert label.spec_id == "return_5d_t1_open_v1"
    assert label.expression == "Ref($open, -6)/Ref($open, -1) - 1"
