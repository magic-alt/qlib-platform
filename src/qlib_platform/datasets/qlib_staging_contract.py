from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pyarrow.parquet as pq


class QlibStagingContractError(ValueError):
    """Raised when a qlib_staging-v2 component cannot be materialized safely."""


_REQUIRED_COLUMNS = frozenset({"date", "symbol"})


def validate_qlib_staging_files(files: Iterable[Path], *, role: str = "qlib_staging") -> None:
    """Validate the cheap, structural qlib-staging-v2 contract without reading row data.

    Materialization performs the deeper row-level checks. This preflight exists so an
    invalid immutable release can be rejected before staging is replaced, and so the
    local publisher never freezes a component that the materializer cannot consume.
    """

    seen = False
    for raw_path in files:
        path = Path(raw_path)
        seen = True
        if path.suffix.lower() != ".parquet":
            raise QlibStagingContractError(f"{role} accepts only Parquet files: {path.name}")
        try:
            schema_names = set(pq.read_schema(path).names)
        except Exception as exc:
            raise QlibStagingContractError(f"{role} file is not readable Parquet: {path.name}") from exc
        missing = _REQUIRED_COLUMNS - schema_names
        if missing:
            raise QlibStagingContractError(
                f"{role} file must contain date and symbol columns: {path.name}; missing={sorted(missing)}"
            )
    if not seen:
        raise QlibStagingContractError(f"{role} contains no Parquet files")
