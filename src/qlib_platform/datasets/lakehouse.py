from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Mapping

from qlib_platform.datasets.dataset_manifest import write_dataset_manifest
from qlib_platform.datasets.dataset_registry import DatasetRegistry
from qlib_platform.settings import Settings


def _link_or_copy(source: str, target: str) -> str:
    source_path = Path(source)
    if source_path.suffix.lower() in {".json", ".yaml", ".yml"}:
        return shutil.copy2(source, target)
    try:
        os.link(source, target)
        return target
    except OSError:
        return shutil.copy2(source, target)


def freeze_layer(
    settings: Settings,
    *,
    layer: str,
    sources: Iterable[tuple[str, Path]],
    parents: Iterable[Mapping[str, object]] = (),
    semantic_contract: Mapping[str, object] | None = None,
) -> dict[str, object]:
    versions_root = settings.paths.root / layer / "versions"
    versions_root.mkdir(parents=True, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix=".building.", dir=versions_root))
    try:
        copied = False
        for name, source in sources:
            if not source.is_dir():
                continue
            target = candidate / name
            shutil.copytree(source, target, copy_function=_link_or_copy)
            copied = True
        if not copied:
            raise FileNotFoundError(f"{layer} snapshot has no source trees")
        dataset_name = f"{settings.qlib_dataset_name}_{layer}"
        alias = f"{layer}-current"
        manifest_path, payload = write_dataset_manifest(
            candidate,
            dataset_name=dataset_name,
            layer=layer,
            semantic_contract=dict(semantic_contract or {}),
            parents=parents,
        )
        version_id = str(payload["version_id"])
        final = versions_root / version_id
        payload["data_path"] = str(final.resolve())
        payload["status"] = "PUBLISHED"
        manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(candidate)
        else:
            os.replace(candidate, final)
        final_manifest = final / "dataset_manifest.json"
        registry = DatasetRegistry(settings.registry_path)
        registry.initialize()
        registry.register_dataset(json.loads(final_manifest.read_text(encoding="utf-8")), final_manifest)
        registry.promote(alias, version_id)
        return {
            "version_id": version_id,
            "dataset_name": dataset_name,
            "layer": layer,
            "manifest_path": str(final_manifest),
        }
    except Exception:
        shutil.rmtree(candidate, ignore_errors=True)
        raise


def freeze_pipeline_layers(
    settings: Settings, *, mode: str, gold_sources: Iterable[tuple[str, Path]]
) -> list[dict[str, object]]:
    bronze = freeze_layer(
        settings,
        layer="bronze",
        sources=(("tushare", settings.paths.raw),),
        semantic_contract={"source": "tushare", "immutability": "content_addressed_snapshot"},
    )
    silver = freeze_layer(
        settings,
        layer="silver",
        sources=(
            ("daily", settings.paths.curated),
            ("reference", settings.paths.metadata),
        ),
        parents=({"version_id": bronze["version_id"], "relation": "normalized_from"},),
        semantic_contract={"price_basis": "raw_plus_adj_factor", "pit": "next_trading_day"},
    )
    gold = freeze_layer(
        settings,
        layer="gold",
        sources=(("pit", settings.paths.gold / "pit" / "current"), *tuple(gold_sources)),
        parents=({"version_id": silver["version_id"], "relation": "materialized_from"},),
        semantic_contract={
            "mode": mode,
            "adjustment_policy": "stable_total_return_first_valid_anchor",
            "purpose": "qlib_model_input",
        },
    )
    return [bronze, silver, gold]
