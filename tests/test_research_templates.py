from pathlib import Path

from qlib_platform.data.official_handler import QlibOfficialAlpha158
from qlib_platform.research.workflow.quickstart import _overlay, _selected, parser
from qlib_platform.research.workflow.templates import get_research_template
from qlib_platform.settings import Settings


def test_model_profile_only_replaces_default_lightgbm() -> None:
    args = parser().parse_args(
        ["run", "--model-profile", "configs/model_profiles/custom_experiment.yaml"]
    )

    alphas, profiles = _selected(args)

    assert alphas == ("alpha158_market_v1",)
    assert tuple(name for name, _ in profiles) == ("custom_experiment",)


def test_baseline_command_selects_official_reference_template() -> None:
    args = parser().parse_args(["baseline"])

    alphas, profiles = _selected(args)

    assert args.template == "qlib_alpha158_official_v1"
    assert alphas == ("qlib_alpha158_official_v1",)
    assert tuple(name for name, _ in profiles) == ("lightgbm_qlib_alpha158_official_v1",)
    assert profiles[0][1].name == "lightgbm_qlib_alpha158_official_v1.yaml"


def test_official_template_pins_local_data_and_reference_protocol(tmp_path: Path) -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"
    settings = Settings.load(config, create_dirs=False)

    overlay = _overlay(
        settings,
        tmp_path,
        "qlib_alpha158_official_v1",
        template_id="qlib_alpha158_official_v1",
    )
    child = Settings.load(overlay, create_dirs=False)

    assert child.qlib_data_uri == settings.qlib_data_uri
    assert child.qlib_versions_root == settings.qlib_versions_root
    assert child.registry_path == settings.registry_path
    assert child.data["experiment"]["alpha"]["pack"] == "qlib_alpha158_official_v1"
    assert child.data["experiment"]["label"]["spec"] == "return_1d_t1_v1"
    assert child.data["research"]["label_horizon_days"] == 1
    assert child.data["research"]["deal_price"] == "close"
    assert child.data["research"]["backtest_account"] == 100_000_000
    assert child.data["research"]["open_cost"] == 0.0005
    assert child.data["research"]["close_cost"] == 0.0015
    strategy = child.data["strategy"]["topk_dropout"]
    assert strategy["topk"] == 50
    assert strategy["n_drop"] == 5
    assert strategy["hold_thresh"] == 1
    assert strategy["only_tradable"] is False


def test_official_handler_restores_upstream_alpha158_processors() -> None:
    processors = QlibOfficialAlpha158.processor_config()

    assert processors["shared_processors"] == []
    assert processors["infer_processors"] == []
    assert processors["learn_processors"] == [
        {"class": "DropnaLabel"},
        {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
    ]


def test_official_template_documents_local_data_deviations() -> None:
    template = get_research_template("qlib_alpha158_official_v1")

    assert template is not None
    assert template.alpha_pack == "qlib_alpha158_official_v1"
    assert any("local DatasetVersion" in note for note in template.parity_notes)
    assert any("limit flags" in note for note in template.parity_notes)
