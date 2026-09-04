from pathlib import Path

import pytest

from tushare_qlib.research_quickstart import (
    MATRIX_ALPHA_PACKS,
    MATRIX_MODELS,
    _last_json,
    _selected,
    build_research_command,
    parser,
)


def test_matrix_defaults_cover_three_alpha158_levels_and_core_models() -> None:
    args = parser().parse_args(["matrix"])
    alphas, profiles = _selected(args)
    assert alphas == MATRIX_ALPHA_PACKS
    assert tuple(name for name, _ in profiles) == MATRIX_MODELS
    assert args.stage == "signal"
    assert args.prediction_backtest is True


def test_run_defaults_to_market_alpha158_lightgbm() -> None:
    args = parser().parse_args(["run"])
    alphas, profiles = _selected(args)
    assert alphas == ("alpha158_market_v1",)
    assert tuple(name for name, _ in profiles) == ("lightgbm",)


def test_diagnostics_default_to_sampled_but_research_stays_deep() -> None:
    command_parser = parser()

    assert command_parser.parse_args(["doctor"]).verify_mode == "sampled"
    assert command_parser.parse_args(["prepare"]).verify_mode == "sampled"
    assert command_parser.parse_args(["run"]).verify_mode == "deep"
    assert command_parser.parse_args(["matrix"]).verify_mode == "deep"
    assert command_parser.parse_args(["doctor", "--verify-mode", "deep"]).verify_mode == "deep"


def test_fixed_command_uses_explicit_windows_and_safe_signal_stage(tmp_path: Path) -> None:
    command = build_research_command(
        config=tmp_path / "config.yaml",
        mode="fixed",
        dataset_ref="standalone-current",
        model_profile=tmp_path / "model.yaml",
        benchmark="SH000300",
        topn=30,
        artifact_level="full",
        train=("2020-01-01", "2022-12-30"),
        valid=("2023-01-09", "2023-06-30"),
        test=("2023-07-10", "2024-06-28"),
        start=None,
        end=None,
        checkpoint_namespace="unused",
        stage="signal",
    )
    assert "train-select" in command
    assert command[command.index("--stage") + 1] == "signal"
    assert command[command.index("--dataset-ref") + 1] == "standalone-current"
    assert command[command.index("--train") + 1 : command.index("--train") + 3] == [
        "2020-01-01",
        "2022-12-30",
    ]


def test_fixed_command_rejects_partial_explicit_split(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires --train, --valid and --test together"):
        build_research_command(
            config=tmp_path / "config.yaml",
            mode="fixed",
            dataset_ref="standalone-current",
            model_profile=tmp_path / "model.yaml",
            benchmark="SH000300",
            topn=None,
            artifact_level="minimal",
            train=("2020-01-01", "2022-12-30"),
            valid=None,
            test=None,
            start=None,
            end=None,
            checkpoint_namespace="unused",
        )


def test_walk_forward_command_uses_release_stage_and_checkpoint_namespace(tmp_path: Path) -> None:
    command = build_research_command(
        config=tmp_path / "config.yaml",
        mode="walk-forward",
        dataset_ref="standalone-current",
        model_profile=tmp_path / "model.yaml",
        benchmark="SH000300",
        topn=None,
        artifact_level="full",
        train=None,
        valid=None,
        test=None,
        start="2019-01-01",
        end="2026-08-10",
        checkpoint_namespace="quickstart-alpha158-lightgbm",
        stage="signal",
    )
    assert "research-run" in command
    assert command[command.index("--mode") + 1] == "walk-forward"
    assert command[command.index("--stage") + 1] == "release"
    assert command[command.index("--checkpoint-namespace") + 1] == "quickstart-alpha158-lightgbm"


def test_last_json_ignores_logs() -> None:
    assert _last_json('training...\n{"manifest":"/tmp/run/manifest.json"}\n') == {
        "manifest": "/tmp/run/manifest.json"
    }
