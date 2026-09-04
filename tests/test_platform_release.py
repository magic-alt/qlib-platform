from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from qlib_platform.platform_release import (
    REQUIRED_RESEARCH_COMPONENTS,
    QLIB_RESEARCH_PROFILE,
    QLIB_RESEARCH_PROFILE_V2,
    load_platform_release,
    materialize_platform_release,
    platform_release_preflight,
)
from qlib_platform.research.phase2_data_acceptance import (
    REQUIRED_V2_ACCEPTANCE_CHECKS,
    write_data_release_v2_acceptance,
)
from qlib_platform.settings import Paths, Settings


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _write_release(root: Path, *, profile: str = "cn-equity-daily-research-v2") -> tuple[Path, str]:
    source = root / "canonical"
    source.mkdir(parents=True)
    components = []
    profile_required = REQUIRED_RESEARCH_COMPONENTS
    if profile in {QLIB_RESEARCH_PROFILE, QLIB_RESEARCH_PROFILE_V2}:
        profile_required |= {"qlib_staging", "industry_classification_pit"}
    roles = sorted(profile_required | {"qlib_staging"})
    sources: dict[Path, Path] = {}
    for role in roles:
        path = source / f"{role}.parquet"
        if role == "qlib_staging":
            pd.DataFrame(
                [
                    {"date": "2019-12-31", "symbol": "SH600000", "open": 9.0, "close": 9.1},
                    {"date": "2026-08-13", "symbol": "SH600000", "open": 10.0, "close": 10.1},
                ]
            ).to_parquet(path, index=False)
        elif role == "industry_classification_pit":
            pd.DataFrame(
                [
                    {
                        "instrument": "SH600000",
                        "effective_from": "2026-01-01",
                        "effective_to": "2026-12-31",
                        "industry_code": "801010",
                        "industry_name": "Agriculture",
                        "taxonomy": "SW2021",
                        "level_no": 1,
                    }
                ]
            ).to_parquet(path, index=False)
        elif role == "pit_universe":
            pd.DataFrame(
                [
                    {
                        "instrument": "SH600000",
                        "effective_from": "2026-01-01",
                        "effective_to": "2026-12-31",
                    }
                ]
            ).to_parquet(path, index=False)
        else:
            pd.DataFrame([{"role": role}]).to_parquet(path, index=False)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = Path("components") / role / "00000.parquet"
        sources[relative] = path
        files = [
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "sizeBytes": path.stat().st_size,
                "rowCount": 1,
            }
        ]
        schema_version = "1"
        if profile == QLIB_RESEARCH_PROFILE_V2:
            if role == "pit_fundamentals":
                schema_version = "2"
            elif role == "qlib_staging":
                schema_version = "qlib-staging-v2"
        identity = {
            "role": role,
            "componentReleaseId": f"component:{role}:1",
            "datasetKey": role,
            "schemaVersion": schema_version,
            "coverage": {"start": "2020-01-01", "end": "2026-08-13"},
            "files": files,
        }
        components.append(
            {
                **identity,
                "componentSha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
            }
        )
    identity = {
        "schemaVersion": "2.0",
        "profile": profile,
        "assetClass": "equity",
        "market": "china",
        "universe": "CSI300",
        "benchmark": "SH000300",
        "coverage": {"start": "2020-01-01", "end": "2026-08-13"},
        "asOfTime": "2026-08-14T00:00:00+08:00",
        "requiredComponents": sorted(profile_required),
        "components": components,
        "policies": {"pit": "announce_date"},
        "lineage": {"parentIngestionBatches": ["batch-1"]},
    }
    identity_sha = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    release_id = f"ds_{identity_sha}"
    manifest = {
        **identity,
        "dataReleaseId": release_id,
        "identitySha256": identity_sha,
        "publishedAt": "2026-08-14T00:00:00+00:00",
    }
    manifest["manifestSha256"] = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    path = root / "releases" / release_id / "manifest.json"
    release_root = path.parent
    release_root.mkdir(parents=True)
    for relative, original in sources.items():
        frozen = release_root / relative
        frozen.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, frozen)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, release_id


def _settings(root: Path, manifest: Path, release_id: str) -> Settings:
    project = root / "derived" / "qlib" / release_id
    data = {
        "data_source": {
            "kind": "platform_release",
            "platform_release": {
                "id": release_id,
                "data_root": str(root),
                "manifest": str(manifest),
                "qlib_staging_role": "qlib_staging",
            },
        },
        "universe": {
            "instruments": "csi300",
            "membership_file": str(project / "pit_universe.parquet"),
        },
        "qlib": {"dataset_version": release_id},
    }
    return Settings(
        config_path=root / "configs" / "pipeline.yaml",
        data=data,
        paths=Paths.from_root(project),
        tushare_token=None,
        qlib_repo=None,
        qlib_data_uri=project / "current",
    )


def test_release_preflight_and_materialization_are_hash_bound(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    settings = _settings(tmp_path, manifest, release_id)

    release = load_platform_release(settings)
    preflight = platform_release_preflight(settings, "20200101", "20260813")
    materialized = materialize_platform_release(settings)

    assert release.data_release_id == release_id
    assert preflight["passed"] is True
    assert materialized.data_release_id == release_id
    staging = json.loads((settings.paths.staging_full / "staging_manifest.json").read_text())
    assert staging["data_release_id"] == release_id
    assert staging["coverage"] == {"start": "2020-01-01", "end": "2026-08-13"}
    assert list(settings.paths.staging_full.glob("*.parquet"))
    assert (settings.paths.staging_full / "SH600000.parquet").is_file()
    staged = pd.read_parquet(settings.paths.staging_full / "SH600000.parquet")
    assert staged["date"].tolist() == ["2026-08-13"]
    assert Path(settings.data["universe"]["membership_file"]).is_file()


def test_qlib_profile_materializes_pit_industry_codes(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path, profile=QLIB_RESEARCH_PROFILE)
    settings = _settings(tmp_path, manifest, release_id)

    release = materialize_platform_release(settings)
    staged = pd.read_parquet(next(settings.paths.staging_full.glob("*.parquet")))

    assert release.profile == QLIB_RESEARCH_PROFILE
    assert staged.loc[0, "industry_l1_code"] == 801010.0
    assert json.loads((settings.paths.staging_full / "staging_manifest.json").read_text())["files"]


def test_phase2_profile_accepts_only_expanded_component_schemas(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path, profile=QLIB_RESEARCH_PROFILE_V2)
    settings = _settings(tmp_path, manifest, release_id)

    assert load_platform_release(settings).profile == QLIB_RESEARCH_PROFILE_V2

    payload = json.loads(manifest.read_text())
    pit = next(item for item in payload["components"] if item["role"] == "pit_fundamentals")
    pit["schemaVersion"] = "1"
    component_identity = {
        key: pit[key]
        for key in (
            "role",
            "componentReleaseId",
            "datasetKey",
            "schemaVersion",
            "coverage",
            "files",
        )
    }
    pit["componentSha256"] = hashlib.sha256(_canonical_bytes(component_identity)).hexdigest()
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"dataReleaseId", "identitySha256", "manifestSha256", "publishedAt"}
    }
    identity_sha = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    payload["dataReleaseId"] = f"ds_{identity_sha}"
    payload["identitySha256"] = identity_sha
    payload["manifestSha256"] = hashlib.sha256(
        _canonical_bytes({key: value for key, value in payload.items() if key != "manifestSha256"})
    ).hexdigest()
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    settings.data["data_source"]["platform_release"]["id"] = payload["dataReleaseId"]

    with pytest.raises(ValueError, match="component schema mismatch"):
        load_platform_release(settings)


def test_phase2_data_acceptance_is_narrow_and_hash_bound(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path, profile=QLIB_RESEARCH_PROFILE_V2)
    settings = _settings(tmp_path, manifest, release_id)
    evidence = {
        name: {"status": "PASS", "artifactSha256": hashlib.sha256(name.encode()).hexdigest()}
        for name in REQUIRED_V2_ACCEPTANCE_CHECKS
    }

    path = write_data_release_v2_acceptance(
        settings,
        evidence=evidence,
        output=tmp_path / "phase2-data-acceptance.json",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["passed"] is True
    assert payload["fullInfrastructureRecertificationRun"] is False
    assert payload["dataReleaseId"] == release_id

    evidence["PIT_LEAKAGE"]["status"] = "FAIL"
    with pytest.raises(ValueError, match="checks failed"):
        write_data_release_v2_acceptance(
            settings,
            evidence=evidence,
            output=tmp_path / "failed.json",
        )


def test_release_rejects_corrupt_component_and_wrong_configured_id(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    settings = _settings(tmp_path, manifest, release_id)
    payload = json.loads(manifest.read_text())
    component_path = manifest.parent / payload["components"][0]["files"][0]["path"]
    component_path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_platform_release(settings)

    manifest, release_id = _write_release(tmp_path / "second")
    settings = _settings(tmp_path / "second", manifest, "ds_" + "0" * 64)
    with pytest.raises(ValueError, match="Configured DataRelease ID"):
        load_platform_release(settings)


def test_preflight_fails_closed_when_requested_window_is_outside_release(tmp_path: Path):
    manifest, release_id = _write_release(tmp_path)
    result = platform_release_preflight(_settings(tmp_path, manifest, release_id), "20190101", "20260814")
    assert result["passed"] is False
    assert result["failures"] == ["coverage_start:20200101", "coverage_end:20260813"]
