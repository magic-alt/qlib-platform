from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1) Bootstrap: after the operator explicitly selects a release alias, safely recover
# a missing dataset alias only when exactly one DatasetVersion is bound to that release.
replace_once(
    "src/qlib_platform/bootstrap.py",
    "\n\ndef bootstrap(\n",
    '''\n\ndef _recover_selected_dataset_alias(settings: Settings, release_id: str) -> dict[str, Any] | None:\n    \"\"\"Recover a missing DatasetVersion alias after an explicit release selection.\n\n    This deliberately does not guess between historical DataReleases. Recovery is only\n    allowed when ``research-release-current`` already points at ``release_id`` and one\n    usable DatasetVersion is bound to that release for the configured dataset name.\n    \"\"\"\n\n    from qlib_platform.datasets.dataset_registry import DatasetRegistry\n\n    registry = DatasetRegistry(settings.registry_path)\n    if registry.resolve_release_alias(\"research-release-current\") != release_id:\n        return None\n    candidates = [\n        item\n        for item in registry.list_versions(settings.qlib_dataset_name)\n        if item.data_release_id == release_id\n        and item.status in {\"VALIDATED\", \"PUBLISHED\"}\n        and item.manifest_path.is_file()\n        and item.data_path.is_dir()\n    ]\n    if len(candidates) != 1:\n        return None\n    dataset = candidates[0]\n    registry.promote_research_snapshot(\n        release_alias=\"research-release-current\",\n        data_release_id=release_id,\n        dataset_alias=settings.qlib_dataset_ref,\n        dataset_version_id=dataset.version_id,\n    )\n    return {\n        \"status\": \"READY\",\n        \"source\": \"dataset_version\",\n        \"reference\": settings.qlib_dataset_ref,\n        \"dataReleaseId\": release_id,\n        \"datasetVersionId\": dataset.version_id,\n        \"aliasRecovered\": True,\n    }\n\n\ndef bootstrap(\n''',
)
replace_once(
    "src/qlib_platform/bootstrap.py",
    '''        if resolved.status == "IMPORT_REQUIRED":\n''',
    '''        if resolved.status == "MATERIALIZE_REQUIRED" and resolved.reference:\n            recovered = _recover_selected_dataset_alias(settings, resolved.reference)\n            if recovered is not None:\n                return recovered\n        if resolved.status == "IMPORT_REQUIRED":\n''',
)
replace_once(
    "src/qlib_platform/bootstrap.py",
    '''                "selectionCommand": ("tq release promote <DATA_RELEASE_ID> --alias research-release-current"),\n                "retryCommand": "tq-research prepare --source auto",\n''',
    '''                "selectionCommand": ("tq release promote <DATA_RELEASE_ID> --alias research-release-current"),\n                "datasetRecoveryCommand": f"tq registry-rebuild --root {settings.paths.root}",\n                "retryCommand": "tq-research prepare --source auto",\n''',
)

# 2) Quickstart: make doctor/prepare/run share actionable state instead of allowing
# run to leak an unknown standalone-current KeyError traceback.
replace_once(
    "src/qlib_platform/research/workflow/quickstart.py",
    '''def _dataset_ref(settings: Settings, requested: str | None) -> str:\n    return str(requested or settings.qlib_dataset_ref)\n\n\ndef _verify''',
    '''def _dataset_ref(settings: Settings, requested: str | None) -> str:\n    return str(requested or settings.qlib_dataset_ref)\n\n\ndef _release_selection_payload(settings: Settings, error: Exception) -> dict[str, Any]:\n    return {\n        "status": "RELEASE_SELECTION_REQUIRED",\n        "error": str(error),\n        "recommendedCommand": "tq release list",\n        "selectionCommand": "tq release promote <DATA_RELEASE_ID> --alias research-release-current",\n        "datasetRecoveryCommand": f"tq registry-rebuild --root {settings.paths.root}",\n        "retryCommand": "tq-research prepare --source auto",\n    }\n\n\ndef _verify''',
)
replace_once(
    "src/qlib_platform/research/workflow/quickstart.py",
    '''    except ReleaseSelectionRequired as exc:\n        return {\n            "status": "RELEASE_SELECTION_REQUIRED",\n            "error": str(exc),\n            "recommendedCommand": "tq release list",\n        }\n''',
    '''    except ReleaseSelectionRequired as exc:\n        return _release_selection_payload(settings, exc)\n''',
)
replace_once(
    "src/qlib_platform/research/workflow/quickstart.py",
    '''    if str(result.get("status")) != "READY":\n        return {"status": result.get("status", "NOT_READY"), "bootstrap": result}\n''',
    '''    if str(result.get("status")) != "READY":\n        payload: dict[str, Any] = {"status": result.get("status", "NOT_READY"), "bootstrap": result}\n        for key in (\n            "error",\n            "recommendedCommand",\n            "selectionCommand",\n            "datasetRecoveryCommand",\n            "retryCommand",\n        ):\n            if key in result:\n                payload[key] = result[key]\n        return payload\n''',
)
replace_once(
    "src/qlib_platform/research/workflow/quickstart.py",
    '''def run_plan(settings: Settings, args: argparse.Namespace, plan: dict[str, Any], root: Path) -> int:\n    dataset = _verify(settings, str(plan["datasetRef"]), args)\n''',
    '''def run_plan(settings: Settings, args: argparse.Namespace, plan: dict[str, Any], root: Path) -> int:\n    try:\n        dataset = _verify(settings, str(plan["datasetRef"]), args)\n    except KeyError as exc:\n        if str(plan["datasetRef"]) != settings.qlib_dataset_ref:\n            raise\n        try:\n            source = resolve_source(settings)\n        except ReleaseSelectionRequired as selection_error:\n            plan.update(_release_selection_payload(settings, selection_error))\n        else:\n            plan.update(\n                status="DATASET_PREPARATION_REQUIRED",\n                error=str(exc),\n                recommendedCommand="tq-research prepare --source auto",\n            )\n            if source.reference:\n                plan["sourceReference"] = source.reference\n            if source.action:\n                plan["sourceAction"] = source.action\n        plan["failureCount"] = 1\n        _write_matrix(root, plan)\n        return 2\n''',
)
replace_once(
    "src/qlib_platform/research/workflow/quickstart.py",
    '''    print(\n        json.dumps(\n            {\n                "status": plan["status"],\n                "output": str(root),\n                "matrix": str(root / "research_matrix.json"),\n                "summary": str(root / "research_matrix.md"),\n            },\n            ensure_ascii=False,\n        )\n    )\n''',
    '''    result_payload: dict[str, Any] = {\n        "status": plan["status"],\n        "output": str(root),\n        "matrix": str(root / "research_matrix.json"),\n        "summary": str(root / "research_matrix.md"),\n    }\n    for key in (\n        "error",\n        "recommendedCommand",\n        "selectionCommand",\n        "datasetRecoveryCommand",\n        "retryCommand",\n        "sourceReference",\n        "sourceAction",\n    ):\n        if key in plan:\n            result_payload[key] = plan[key]\n    print(json.dumps(result_payload, ensure_ascii=False))\n''',
)

# 3) Regression tests for the stateful upgrade case.
replace_once(
    "tests/test_bootstrap.py",
    '''from pathlib import Path\n\nfrom qlib_platform.bootstrap import bootstrap\nfrom qlib_platform.datasets.data_source_resolver import ReleaseSelectionRequired\n''',
    '''import json\nfrom pathlib import Path\nfrom types import SimpleNamespace\n\nfrom qlib_platform.bootstrap import bootstrap\nfrom qlib_platform.datasets.data_source_resolver import ReleaseSelectionRequired, SourceResolution\nfrom qlib_platform.datasets.dataset_registry import DatasetRegistry\n''',
)
bootstrap_test = r'''


def test_auto_bootstrap_recovers_dataset_alias_after_explicit_release_selection(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    registry = DatasetRegistry(settings.registry_path)
    release_id = "ds_selected"
    release_manifest = tmp_path / "release.json"
    release_manifest.write_text("{}", encoding="utf-8")
    registry.register_release(
        SimpleNamespace(
            data_release_id=release_id,
            profile="ashare_qlib_import_v1",
            manifest_path=release_manifest,
            manifest_sha256="0" * 64,
            manifest={},
            coverage={},
        ),
        governance_level="exploratory",
    )
    data_path = tmp_path / "dataset"
    data_path.mkdir()
    manifest_path = data_path / "dataset_manifest.json"
    manifest = {
        "schema_version": "3.0",
        "version_id": "dv_selected",
        "dataset_name": settings.qlib_dataset_name,
        "layer": "qlib",
        "status": "VALIDATED",
        "data_path": str(data_path),
        "coverage": {},
        "partitions": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry.register_dataset(manifest, manifest_path)
    registry.promote_release("research-release-current", release_id)
    monkeypatch.setattr(
        "qlib_platform.bootstrap.resolve_source",
        lambda _settings: SourceResolution(
            "MATERIALIZE_REQUIRED",
            "data_release",
            release_id,
            action="dataset-build",
        ),
    )

    result = bootstrap(settings, source="auto")

    assert result["status"] == "READY"
    assert result["aliasRecovered"] is True
    assert result["dataReleaseId"] == release_id
    assert result["datasetVersionId"] == "dv_selected"
    assert registry.resolve(settings.qlib_dataset_ref).version_id == "dv_selected"
'''
Path("tests/test_bootstrap.py").write_text(
    Path("tests/test_bootstrap.py").read_text(encoding="utf-8") + bootstrap_test,
    encoding="utf-8",
)

replace_once(
    "tests/test_research_quickstart.py",
    '''import pytest\n\nfrom qlib_platform.research.workflow.quickstart import (\n''',
    '''import pytest\n\nimport qlib_platform.research.workflow.quickstart as quickstart_module\nfrom qlib_platform.datasets.data_source_resolver import ReleaseSelectionRequired\nfrom qlib_platform.research.workflow.quickstart import (\n''',
)
quickstart_test = r'''


def test_run_plan_surfaces_release_selection_instead_of_unknown_dataset_traceback(
    tmp_path: Path, monkeypatch
) -> None:
    config = Path(__file__).parents[1] / "configs" / "pipeline.standalone.yaml"
    settings = Settings.load(config, create_dirs=False)
    args = parser().parse_args(["run"])
    root = tmp_path / "run"
    plan = {"datasetRef": settings.qlib_dataset_ref, "jobs": []}

    def missing_dataset(*_args, **_kwargs):
        raise KeyError("unknown dataset reference: standalone-current")

    def ambiguous_release(_settings):
        raise ReleaseSelectionRequired(
            "RELEASE_SELECTION_REQUIRED: multiple DataReleases exist without an active alias"
        )

    monkeypatch.setattr(quickstart_module, "_verify", missing_dataset)
    monkeypatch.setattr(quickstart_module, "resolve_source", ambiguous_release)

    code = quickstart_module.run_plan(settings, args, plan, root)

    assert code == 2
    assert plan["status"] == "RELEASE_SELECTION_REQUIRED"
    assert plan["recommendedCommand"] == "tq release list"
    assert plan["selectionCommand"].endswith("--alias research-release-current")
    assert plan["retryCommand"] == "tq-research prepare --source auto"
    assert (root / "research_matrix.json").is_file()
'''
Path("tests/test_research_quickstart.py").write_text(
    Path("tests/test_research_quickstart.py").read_text(encoding="utf-8") + quickstart_test,
    encoding="utf-8",
)

# 4) macOS is a real CI platform, not inferred from Linux/Windows success.
replace_once(
    ".github/workflows/ci.yml",
    '''        os: [ubuntu-latest, windows-latest]\n        python: ['3.10', '3.11', '3.12']\n        exclude:\n          - os: ubuntu-latest\n            python: '3.11'\n          - os: windows-latest\n            python: '3.11'\n''',
    '''        os: [ubuntu-latest, windows-latest, macos-latest]\n        python: ['3.10', '3.11', '3.12']\n        exclude:\n          - os: ubuntu-latest\n            python: '3.11'\n          - os: windows-latest\n            python: '3.11'\n          - os: macos-latest\n            python: '3.10'\n          - os: macos-latest\n            python: '3.12'\n''',
)
replace_once(
    ".github/workflows/ci.yml",
    '''      - name: Configure isolated Qlib paths\n        shell: pwsh\n        run: |\n          "QLIB_REPO=$env:RUNNER_TEMP/qlib-repo" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "QLIB_DATA_URI=$env:RUNNER_TEMP/qlib-data" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "QUANT_DATA_ROOT=$env:RUNNER_TEMP/quant-data" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "DATASET_RELEASE_ID=ds_0000000000000000000000000000000000000000000000000000000000000000" | Out-File -FilePath $env:GITHUB_ENV -Append\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/qlib-repo" | Out-Null\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/qlib-data" | Out-Null\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/quant-data" | Out-Null\n''',
    '''      - name: Configure isolated Qlib paths (POSIX)\n        if: runner.os != 'Windows'\n        shell: bash\n        run: |\n          echo "QLIB_REPO=$RUNNER_TEMP/qlib-repo" >> "$GITHUB_ENV"\n          echo "QLIB_DATA_URI=$RUNNER_TEMP/qlib-data" >> "$GITHUB_ENV"\n          echo "QUANT_DATA_ROOT=$RUNNER_TEMP/quant-data" >> "$GITHUB_ENV"\n          echo "DATASET_RELEASE_ID=ds_0000000000000000000000000000000000000000000000000000000000000000" >> "$GITHUB_ENV"\n          mkdir -p "$RUNNER_TEMP/qlib-repo" "$RUNNER_TEMP/qlib-data" "$RUNNER_TEMP/quant-data"\n      - name: Configure isolated Qlib paths (Windows)\n        if: runner.os == 'Windows'\n        shell: pwsh\n        run: |\n          "QLIB_REPO=$env:RUNNER_TEMP/qlib-repo" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "QLIB_DATA_URI=$env:RUNNER_TEMP/qlib-data" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "QUANT_DATA_ROOT=$env:RUNNER_TEMP/quant-data" | Out-File -FilePath $env:GITHUB_ENV -Append\n          "DATASET_RELEASE_ID=ds_0000000000000000000000000000000000000000000000000000000000000000" | Out-File -FilePath $env:GITHUB_ENV -Append\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/qlib-repo" | Out-Null\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/qlib-data" | Out-Null\n          New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP/quant-data" | Out-Null\n''',
)

# 5) README: document stateful-upgrade recovery and avoid framing it as an OS issue.
replace_once(
    "README.md",
    '''```bash\nbash scripts/run_local_research.sh prepare --source auto\n```\n\nFor an existing Qlib binary provider, import it explicitly:\n''',
    '''```bash\nbash scripts/run_local_research.sh prepare --source auto\n```\n\nIf an upgraded checkout reports `RELEASE_SELECTION_REQUIRED`, the repository has multiple immutable\nDataReleases but no active release alias. This is intentionally fail-closed and is independent of the\noperating system. Select the intended release explicitly, then rerun `prepare`:\n\n```bash\n.venv/bin/tq --config configs/pipeline.standalone.yaml release list\n.venv/bin/tq --config configs/pipeline.standalone.yaml release promote <DATA_RELEASE_ID> --alias research-release-current\nbash scripts/run_local_research.sh prepare --source auto\n```\n\n`prepare` now repairs `standalone-current` automatically when exactly one registered DatasetVersion is\nbound to that explicitly selected DataRelease. If an older checkout left DatasetVersion manifests on\ndisk but the registry lost them, rebuild the registry first and retry:\n\n```bash\n.venv/bin/tq --config configs/pipeline.standalone.yaml registry-rebuild --root data\n.venv/bin/tq --config configs/pipeline.standalone.yaml dataset-list --name cn_standalone\n```\n\nThe tool never chooses between multiple historical DataReleases or multiple candidate DatasetVersions.\nThose cases remain explicit operator decisions so research lineage cannot silently drift.\n\nFor an existing Qlib binary provider, import it explicitly:\n''',
)
