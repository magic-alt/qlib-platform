from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from qlib_platform.datasets.data_release import (
    DATA_RELEASE_PROFILES,
    PROFILE_COMPONENT_SCHEMAS,
    QLIB_IMPORT_PROFILE,
    DataRelease,
    verify_data_release,
)
from qlib_platform.data.content_store import ContentAddressedStore, clone_tree_copy_on_write
from qlib_platform.datasets.dataset_manifest import write_dataset_manifest
from qlib_platform.datasets.dataset_registry import DatasetRegistry, DatasetVersion
from qlib_platform.datasets.qlib_staging_contract import validate_qlib_staging_files
from qlib_platform.settings import Settings
from qlib_platform.data.store import sha256_file


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ComponentSource:
    role: str
    source: Path
    schema_version: str = "1"
    dataset_key: str | None = None
    parquet_only: bool = True


class LocalReleasePublisher:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.objects = ContentAddressedStore(self.root / "objects")

    @staticmethod
    def _source_files(source: ComponentSource) -> list[Path]:
        root = source.source
        if root.is_symlink():
            raise ValueError(f"DataRelease source must not be a symlink: {root}")
        files = (
            [root]
            if root.is_file()
            else sorted(
                item
                for item in root.rglob("*")
                if item.is_file() and item.name != "dataset_manifest.json" and not item.name.endswith(".tmp")
            )
        )
        if source.parquet_only:
            files = [item for item in files if item.suffix.lower() == ".parquet"]
        if not files:
            raise FileNotFoundError(f"DataRelease component has no files: {source.role} ({root})")
        for path in files:
            if path.is_symlink():
                raise ValueError(f"DataRelease source must not contain symlinks: {path}")
        return files

    def publish(
        self,
        *,
        profile: str,
        components: Iterable[ComponentSource],
        coverage: Mapping[str, str],
        asset_class: str = "equity",
        market: str = "china",
        universe: str = "CSI300",
        benchmark: str = "SH000300",
        policies: Mapping[str, object] | None = None,
        lineage: Mapping[str, object] | None = None,
        as_of_time: str | None = None,
    ) -> DataRelease:
        if profile not in DATA_RELEASE_PROFILES:
            raise ValueError(f"unknown DataRelease profile: {profile}")
        supplied = {item.role: item for item in components}
        required = frozenset(DATA_RELEASE_PROFILES[profile])
        if set(supplied) != set(required):
            raise ValueError(
                f"DataRelease components do not match profile; missing={sorted(required - set(supplied))}, "
                f"extra={sorted(set(supplied) - required)}"
            )
        if not coverage.get("start") or not coverage.get("end"):
            raise ValueError("DataRelease coverage requires start and end")
        self.root.mkdir(parents=True, exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix=".building-release.", dir=self.root))
        try:
            manifest_components: list[dict[str, object]] = []
            for role in sorted(supplied):
                source = supplied[role]
                role_root = candidate / "components" / role
                role_root.mkdir(parents=True)
                entries: list[dict[str, object]] = []
                source_files = self._source_files(source)
                if role == "qlib_staging":
                    validate_qlib_staging_files(source_files, role=role)
                source_root = source.source if source.source.is_dir() else source.source.parent
                for original in source_files:
                    relative_source = original.relative_to(source_root)
                    target = role_root / relative_source
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest = sha256_file(original)
                    stored, digest = self.objects.store(original, digest=digest)
                    self.objects.materialize(stored, target)
                    entry: dict[str, object] = {
                        "path": target.relative_to(candidate).as_posix(),
                        "sha256": digest,
                        "sizeBytes": target.stat().st_size,
                    }
                    if target.suffix.lower() == ".parquet":
                        try:
                            entry["rowCount"] = len(pd.read_parquet(target, columns=[]))
                        except (OSError, ValueError):
                            pass
                    entries.append(entry)
                schema = PROFILE_COMPONENT_SCHEMAS.get(profile, {}).get(role, source.schema_version)
                identity = {
                    "role": role,
                    "componentReleaseId": f"{role}:{hashlib.sha256(_canonical_bytes(entries)).hexdigest()}",
                    "datasetKey": source.dataset_key or role,
                    "schemaVersion": schema,
                    "coverage": dict(coverage),
                    "files": entries,
                }
                manifest_components.append(
                    {
                        **identity,
                        "componentSha256": hashlib.sha256(_canonical_bytes(identity)).hexdigest(),
                    }
                )
            frozen_as_of = as_of_time or f"{coverage['end']}T17:30:00+08:00"
            identity = {
                "schemaVersion": "2.0",
                "profile": profile,
                "assetClass": asset_class,
                "market": market,
                "universe": universe,
                "benchmark": benchmark,
                "coverage": dict(coverage),
                "asOfTime": frozen_as_of,
                "requiredComponents": sorted(required),
                "components": manifest_components,
                "policies": dict(policies or {}),
                "lineage": dict(lineage or {}),
            }
            identity_sha = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
            release_id = f"ds_{identity_sha}"
            manifest: dict[str, Any] = {
                **identity,
                "dataReleaseId": release_id,
                "identitySha256": identity_sha,
                "publishedAt": datetime.now(timezone.utc).isoformat(),
            }
            manifest["manifestSha256"] = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
            manifest_path = candidate / "manifest.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            verify_data_release(self.root, manifest_path, configured_id=release_id)
            final = self.root / release_id
            if final.exists():
                existing = verify_data_release(self.root, final / "manifest.json", configured_id=release_id)
                shutil.rmtree(candidate)
                return existing
            os.replace(candidate, final)
            return verify_data_release(self.root, final / "manifest.json", configured_id=release_id)
        except Exception:
            shutil.rmtree(candidate, ignore_errors=True)
            raise

    def import_qlib(self, source: str | Path) -> DataRelease:
        provider = Path(source).expanduser().resolve()
        calendar = provider / "calendars" / "day.txt"
        if (
            not calendar.is_file()
            or not (provider / "instruments").is_dir()
            or not (provider / "features").is_dir()
        ):
            raise ValueError("Qlib import requires calendars/day.txt, instruments, and features")
        dates = [line.strip() for line in calendar.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not dates:
            raise ValueError("Qlib import calendar is empty")
        return self.publish(
            profile=QLIB_IMPORT_PROFILE,
            components=[
                ComponentSource(
                    "qlib_dataset",
                    provider,
                    schema_version="qlib-provider-v1",
                    parquet_only=False,
                )
            ],
            coverage={"start": dates[0][:10], "end": dates[-1][:10]},
            policies={
                "governanceLevel": "exploratory",
                "certifiedPromotionAllowed": False,
                "phase2Phase3Allowed": False,
                "targetPortfolioAllowed": False,
            },
            lineage={"producer": "qlib-platform", "sourceType": "existing_qlib"},
        )


def release_store_root(settings: Settings) -> Path:
    store_cfg = settings.data.get("release_store", {})
    if isinstance(store_cfg, Mapping) and store_cfg.get("root"):
        root = Path(str(store_cfg["root"])).expanduser()
    elif settings.uses_data_release() and settings.data_release_config.get("data_root"):
        root = Path(str(settings.data_release_config["data_root"])).expanduser() / "releases"
    else:
        root = settings.paths.root / "releases"
    return root.resolve() if root.is_absolute() else (settings.config_path.parent.parent / root).resolve()


def local_research_components(settings: Settings) -> list[ComponentSource]:
    universe_cfg = settings.data.get("universe", {})
    membership = (
        Path(str(universe_cfg.get("membership_file"))).expanduser()
        if isinstance(universe_cfg, Mapping) and universe_cfg.get("membership_file")
        else settings.paths.metadata
        / "universe_membership"
        / f"{str(universe_cfg.get('instruments', 'all')).lower()}.parquet"
    )
    if not membership.is_absolute():
        membership = (settings.config_path.parent.parent / membership).resolve()
    return [
        ComponentSource("bars", settings.paths.raw / "daily"),
        ComponentSource("daily_basic", settings.paths.raw / "daily_basic"),
        ComponentSource("adjustment_factors", settings.paths.raw / "adj_factor"),
        ComponentSource("corporate_actions", settings.paths.raw / "dividend"),
        ComponentSource("trade_status", settings.paths.raw / "suspend_d"),
        ComponentSource("limit_prices", settings.paths.raw / "stk_limit"),
        ComponentSource("st_status", settings.paths.raw / "stock_st"),
        ComponentSource("security_master", settings.paths.metadata / "stock_master.parquet"),
        ComponentSource("trading_calendar", settings.paths.metadata / "trade_calendar.parquet"),
        ComponentSource("pit_universe", membership),
        ComponentSource(
            "pit_fundamentals",
            settings.paths.gold / "pit" / "current" / "fundamentals_daily.parquet",
            schema_version="2",
        ),
        ComponentSource("benchmark", settings.paths.metadata / "benchmarks" / "SH000300.parquet"),
        ComponentSource(
            "qlib_staging",
            settings.paths.staging_full,
            schema_version="qlib-staging-v2",
        ),
        ComponentSource(
            "industry_classification_pit",
            settings.paths.metadata / "industry_classification_pit.parquet",
        ),
    ]


def publish_local_research_release(
    settings: Settings,
    *,
    start: str,
    end: str,
    parent_release_id: str | None = None,
) -> DataRelease:
    from qlib_platform.datasets.data_release import QLIB_RESEARCH_PROFILE_V2

    lineage: dict[str, object] = {
        "producer": "qlib-platform",
        "sourceType": "tushare" if settings.uses_tushare_source() else "local_raw",
    }
    if parent_release_id:
        lineage["parentReleaseId"] = parent_release_id
    return LocalReleasePublisher(release_store_root(settings)).publish(
        profile=QLIB_RESEARCH_PROFILE_V2,
        components=local_research_components(settings),
        coverage={
            "start": str(pd.Timestamp(start).date()),
            "end": str(pd.Timestamp(end).date()),
        },
        policies={
            "governanceLevel": "research",
            "pitAvailability": "next_trading_day",
            "promotionAllowed": True,
        },
        lineage=lineage,
    )


def import_qlib_dataset(settings: Settings, source: str | Path) -> tuple[DataRelease, DatasetVersion]:
    release = LocalReleasePublisher(release_store_root(settings)).import_qlib(source)
    source_dataset = release.manifest_path.parent / "components" / "qlib_dataset"
    versions = settings.qlib_versions_root
    versions.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=".import-qlib.", dir=versions))
    try:
        clone_tree_copy_on_write(source_dataset, candidate)
        manifest_path, payload = write_dataset_manifest(
            candidate,
            dataset_name=settings.qlib_dataset_name,
            layer="qlib",
            semantic_contract={
                "data_release_id": release.data_release_id,
                "data_release_manifest_sha256": release.manifest_sha256,
                "governance_level": "exploratory",
                "source_type": "existing_qlib",
            },
            coverage=dict(release.coverage),
            extra={
                "dataset_id": release.data_release_id,
                "data_release_id": release.data_release_id,
                "data_release_manifest_sha256": release.manifest_sha256,
                "mode": "import",
            },
        )
        version_id = str(payload["version_id"])
        final = versions / version_id
        payload["data_path"] = str(final.resolve())
        payload["status"] = "VALIDATED"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(candidate)
        else:
            os.replace(candidate, final)
        final_manifest = final / "dataset_manifest.json"
        registry = DatasetRegistry(settings.registry_path)
        registry.register_release(release, governance_level="exploratory")
        version = registry.register_dataset(
            json.loads(final_manifest.read_text(encoding="utf-8")), final_manifest
        )
        registry.promote_research_snapshot(
            release_alias="research-release-current",
            data_release_id=release.data_release_id,
            dataset_alias=settings.qlib_dataset_ref,
            dataset_version_id=version.version_id,
        )
        resolved = registry.get_version(version.version_id)
        assert resolved is not None
        return release, resolved
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise
