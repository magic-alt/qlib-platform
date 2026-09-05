from __future__ import annotations

from pathlib import Path

from qlib_platform.research.evidence.experiment_db import (
    SCHEMA_VERSION,
    Connection,
    initialize_schema,
    open_connection,
)
from qlib_platform.research.evidence.experiment_store_compare import ExperimentCompareMixin
from qlib_platform.research.evidence.experiment_store_query import ExperimentQueryMixin
from qlib_platform.research.evidence.experiment_store_write import ExperimentWriteMixin
from qlib_platform.research.evidence.experiment_store_write_artifacts import ExperimentArtifactWriteMixin

__all__ = ["ExperimentStore", "SCHEMA_VERSION"]


class ExperimentStore(
    ExperimentWriteMixin,
    ExperimentArtifactWriteMixin,
    ExperimentQueryMixin,
    ExperimentCompareMixin,
):
    """Searchable metadata index over immutable research artifacts."""

    def __init__(self, uri: str | Path = "research_experiments.duckdb") -> None:
        self.backend, self._db = open_connection(uri)
        self._db: Connection
        initialize_schema(self._db)

    def __enter__(self) -> "ExperimentStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()
