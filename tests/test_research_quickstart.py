from pathlib import Path

import pytest

import qlib_platform.research.workflow.quickstart as quickstart_module
from qlib_platform.research.workflow.quickstart import (
    MATRIX_ALPHA_PACKS,
    MATRIX_MODELS,
    _last_json,
    _overlay,
    _selected,
    build_research_command,
    parser,
)
from qlib_platform.settings import Settings


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


def test_standalone_profile_clears_inherited_data_release() -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"

    settings = Settings.load(config, create_dirs=False)

    assert settings.mode == "standalone"
    assert settings.data["experiment"]["data_release"] is None
    assert settings.qlib_dataset_ref == "standalone-current"
    assert settings.data["qlib"]["dataset_version"] == "local"
    assert settings.data["release_store"]["active_keep"] == 1


def test_standalone_optional_qlib_paths_can_come_only_from_env(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "configs" / "pipeline.yaml"
    config.parent.mkdir(parents=True)
    config.write_text(
        "\n".join(
            [
                "mode: standalone",
                "project_root: ./data",
                "qlib:",
                "  repo_path: ''",
                "  dataset_dir: ''",
                "",
            ]
        ),
        encoding="utf-8",
    )
    qlib_repo = tmp_path / "qlib-source"
    provider = tmp_path / "provider"
    qlib_repo.mkdir()
    provider.mkdir()
    monkeypatch.setenv("QLIB_REPO", str(qlib_repo))
    monkeypatch.setenv("QLIB_DATA_URI", str(provider))

    settings = Settings.load(config, create_dirs=False)

    assert settings.qlib_repo == qlib_repo.resolve()
    assert settings.qlib_data_uri == provider.resolve()


def test_env_example_has_valid_copy_as_is_defaults() -> None:
    env_example = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "QLIB_DATA_ROOT=./data" in env_example
    assert "TUSHARE_TOKEN=" in env_example
    assert "QLIB_REPO=" in env_example
    assert "QLIB_DATA_URI=" in env_example
    assert "/absolute/path/to/qlib-platform-data" not in env_example


def test_diagnostics_default_to_sampled_but_research_stays_deep() -> None:
    command_parser = parser()

    assert command_parser.parse_args(["doctor"]).verify_mode == "sampled"
    assert command_parser.parse_args(["prepare"]).verify_mode == "sampled"
    assert command_parser.parse_args(["run"]).verify_mode == "deep"
    assert command_parser.parse_args(["matrix"]).verify_mode == "deep"
    assert command_parser.parse_args(["doctor", "--verify-mode", "deep"]).verify_mode == "deep"


def test_generated_overlay_preserves_parent_data_paths_from_nested_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    configs = repo / "configs"
    configs.mkdir(parents=True)
    base = configs / "pipeline.standalone.yaml"
    base.write_text(
        "\n".join(
            [
                "mode: standalone",
                "project_root: ./data",
                "storage:",
                "  registry_path: ./data/registry/qlib.sqlite",
                "qlib:",
                "  dataset_dir: ./data/qlib/current",
                "  versions_root: ./data/qlib/versions",
                "  dataset_name: cn_standalone",
                "  dataset_ref: standalone-current",
                "",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings.load(base, create_dirs=False)
    nested_output = settings.paths.output / "quickstart" / "run-id"

    overlay = _overlay(settings, nested_output, "alpha158_market_v1")
    child = Settings.load(overlay, create_dirs=False)

    assert child.paths.root == settings.paths.root
    assert child.registry_path == settings.registry_path
    assert child.qlib_data_uri == settings.qlib_data_uri
    assert child.qlib_versions_root == settings.qlib_versions_root
    assert child.qlib_dataset_ref == "standalone-current"


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


def test_run_plan_auto_prepares_default_dataset(tmp_path: Path, monkeypatch) -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"
    settings = Settings.load(config, create_dirs=False)
    args = parser().parse_args(["run"])
    root = tmp_path / "run"
    plan = {
        "datasetRef": settings.qlib_dataset_ref,
        "mode": args.mode,
        "predictionBacktest": False,
        "jobs": [],
    }
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    calls = 0

    def verify(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise KeyError("unknown dataset reference: standalone-current")
        return {
            "reference": settings.qlib_dataset_ref,
            "versionId": "dv_ready",
            "path": str(dataset),
            "dataReleaseId": "ds_internal",
            "verification": {},
        }

    monkeypatch.setattr(quickstart_module, "_verify", verify)
    monkeypatch.setattr(
        quickstart_module,
        "bootstrap",
        lambda *_args, **_kwargs: {
            "status": "READY",
            "reference": settings.qlib_dataset_ref,
            "datasetVersionId": "dv_ready",
        },
    )

    code = quickstart_module.run_plan(settings, args, plan, root)

    assert code == 0
    assert plan["status"] == "SUCCEEDED"
    assert plan["preparedAutomatically"] is True
    assert calls == 2


def test_run_plan_does_not_expose_release_hash_when_standalone_data_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"
    settings = Settings.load(config, create_dirs=False)
    args = parser().parse_args(["run"])
    root = tmp_path / "run"
    plan = {
        "datasetRef": settings.qlib_dataset_ref,
        "mode": args.mode,
        "predictionBacktest": False,
        "jobs": [],
    }
    monkeypatch.setattr(
        quickstart_module,
        "_verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            KeyError("unknown dataset reference: standalone-current")
        ),
    )
    monkeypatch.setattr(
        quickstart_module,
        "bootstrap",
        lambda *_args, **_kwargs: {
            "status": "DATA_UNAVAILABLE",
            "reference": "ds_should_not_be_user_configuration",
        },
    )

    code = quickstart_module.run_plan(settings, args, plan, root)

    assert code == 2
    assert plan["status"] == "DATA_UNAVAILABLE"
    assert "ds_should_not_be_user_configuration" not in str(plan)
    assert "TUSHARE_TOKEN" in plan["recommendedCommand"]


def test_run_plan_keeps_explicit_dataset_reference_fail_closed(tmp_path: Path, monkeypatch) -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"
    settings = Settings.load(config, create_dirs=False)
    args = parser().parse_args(["run", "--dataset-ref", "explicit-missing"])
    root = tmp_path / "run"
    plan = {
        "datasetRef": "explicit-missing",
        "mode": args.mode,
        "predictionBacktest": False,
        "jobs": [],
    }
    monkeypatch.setattr(
        quickstart_module,
        "_verify",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyError("explicit-missing")),
    )

    with pytest.raises(KeyError, match="explicit-missing"):
        quickstart_module.run_plan(settings, args, plan, root)
