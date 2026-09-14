"""Provider-neutral market-data adapters."""

from qlib_platform.data.sources.base import DataSourceClient, FetchResult, RetryPolicy
from qlib_platform.data.sources.registry import (
    DataSourceBinding,
    DataSourceFactory,
    EndpointOverride,
    available_data_sources,
    create_data_source,
    register_data_source,
    resolve_data_source_name,
)
from qlib_platform.data.sources.semantic import (
    CanonicalBatch,
    Coverage,
    DataSourceContractError,
    DatasetCapability,
    DatasetRequest,
    FetchEnvelope,
    LocalDatasetSpec,
    LocalFileDataSource,
    RecordedDataSource,
    SemanticDataSource,
    SourceCapabilities,
    require_usable,
)
from qlib_platform.data.sources.tushare_semantic import TushareSemanticDataSource

__all__ = [
    "CanonicalBatch",
    "Coverage",
    "DataSourceBinding",
    "DataSourceClient",
    "DataSourceContractError",
    "DataSourceFactory",
    "DatasetCapability",
    "DatasetRequest",
    "EndpointOverride",
    "FetchEnvelope",
    "FetchResult",
    "LocalDatasetSpec",
    "LocalFileDataSource",
    "RecordedDataSource",
    "RetryPolicy",
    "SemanticDataSource",
    "SourceCapabilities",
    "TushareSemanticDataSource",
    "available_data_sources",
    "create_data_source",
    "register_data_source",
    "require_usable",
    "resolve_data_source_name",
]
