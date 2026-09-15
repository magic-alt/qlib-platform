from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qlib_platform.data.semantic_ingestion import (
    SEMANTIC_RAW_DATASETS,
    consume_semantic_partition,
    legacy_projection,
    semantic_request_for_day,
)
from qlib_platform.data.sources.base import DataSourceClient, FetchResult
from qlib_platform.data.sources.semantic import (
    DataSourceContractError,
    PaginationPolicy,
    SemanticDataSource,
)
from qlib_platform.data.store import PartitionStore


class SemanticIngestionClient:
    """Compatibility façade that makes canonical semantics own core ingestion.

    The existing Extractor continues to speak the stable ``DataSourceClient``
    interface, but the three required daily datasets are fetched through the
    provider-neutral semantic contract, durably resumed in canonical storage,
    then projected back into the frozen raw schema consumed by legacy curation.
    Optional/legacy endpoints continue through the transport fallback.
    """

    def __init__(
        self,
        fallback: DataSourceClient,
        semantic_source: SemanticDataSource,
        *,
        canonical_root: Path,
        pagination_policy: PaginationPolicy | None = None,
    ) -> None:
        self._fallback = fallback
        self._semantic_source = semantic_source
        self._canonical_store = PartitionStore(canonical_root)
        self._pagination_policy = pagination_policy or PaginationPolicy()

    @property
    def canonical_store(self) -> PartitionStore:
        return self._canonical_store

    def fetch(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> FetchResult:
        dataset_kind = SEMANTIC_RAW_DATASETS.get(api_name)
        trade_date = str(params.get("trade_date", "")).strip()
        if dataset_kind is None or not trade_date:
            return self._fallback.fetch(
                api_name,
                fields=fields,
                required=required,
                **params,
            )

        request = semantic_request_for_day(dataset_kind, trade_date)
        envelope = consume_semantic_partition(
            self._semantic_source,
            self._canonical_store,
            request,
            trade_date,
            policy=self._pagination_policy,
        )
        if envelope.status == "success" and envelope.batch is not None:
            frame = _select_legacy_fields(legacy_projection(envelope.batch), fields)
            return FetchResult(frame, "success", envelope.attempts)

        detail = f": {envelope.error}" if envelope.error else ""
        message = (
            f"semantic dataset {dataset_kind!r} returned {envelope.status!r} "
            f"for trade_date={trade_date}{detail}"
        )
        if required:
            raise DataSourceContractError(message)
        return FetchResult(pd.DataFrame(), envelope.status, envelope.attempts, message)

    def call(
        self,
        api_name: str,
        *,
        fields: str | None = None,
        required: bool = True,
        **params: Any,
    ) -> pd.DataFrame:
        return self.fetch(api_name, fields=fields, required=required, **params).data


def _select_legacy_fields(frame: pd.DataFrame, fields: str | None) -> pd.DataFrame:
    if not fields:
        return frame.reset_index(drop=True)
    requested = [field.strip() for field in fields.split(",") if field.strip()]
    missing = [field for field in requested if field not in frame.columns]
    if missing:
        raise DataSourceContractError(
            f"canonical legacy projection cannot satisfy requested fields: {missing}"
        )
    return frame.loc[:, requested].reset_index(drop=True)
