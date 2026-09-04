"""Packaged Qlib binary exporter compatible with the pinned pyqlib 0.9.7 layout.

This module removes the runtime requirement for a separate Microsoft Qlib Git
checkout. When a real checkout is available, qlib_export still prefers the
upstream ``scripts/dump_bin.py`` implementation. The fallback implements the
same day-frequency provider layout for the three modes used by qlib-platform:
``dump_all``, ``dump_update`` and ``dump_fix``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


def _source_files(data_path: Path, suffix: str) -> list[Path]:
    files = sorted(data_path.glob(f"*{suffix}")) if data_path.is_dir() else [data_path]
    if not files:
        raise FileNotFoundError(f"Qlib dump source contains no {suffix} files: {data_path}")
    return files


def _read_frame(path: Path, *, date_field: str) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        frame = pd.read_parquet(path)
    elif path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, low_memory=False)
    else:
        raise ValueError(f"unsupported Qlib dump source format: {path.suffix}")
    if date_field not in frame.columns:
        raise ValueError(f"Qlib dump source is missing {date_field!r}: {path}")
    frame = frame.copy()
    frame[date_field] = pd.to_datetime(frame[date_field], errors="raise").dt.normalize()
    return frame.drop_duplicates(date_field, keep="last").sort_values(date_field)


def _code(path: Path) -> str:
    from qlib.utils import fname_to_code

    return str(fname_to_code(path.stem.strip().lower())).upper()


def _feature_dir(root: Path, code: str) -> Path:
    from qlib.utils import code_to_fname

    target = root / "features" / str(code_to_fname(code)).lower()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _dump_fields(
    frame: pd.DataFrame,
    *,
    include_fields: Iterable[str],
    date_field: str,
    symbol_field: str,
) -> tuple[str, ...]:
    explicit = tuple(dict.fromkeys(str(value).strip() for value in include_fields if str(value).strip()))
    if explicit:
        missing = [field for field in explicit if field not in frame.columns]
        if missing:
            raise ValueError(f"Qlib dump source is missing configured fields: {missing}")
        return explicit
    return tuple(field for field in frame.columns if field not in {date_field, symbol_field})


def _write_fields(
    frame: pd.DataFrame,
    *,
    code: str,
    qlib_dir: Path,
    calendar: list[pd.Timestamp],
    include_fields: Iterable[str],
    date_field: str,
    symbol_field: str,
    append: bool = False,
) -> None:
    if frame.empty:
        return
    calendar_index = pd.DatetimeIndex(calendar)
    first = pd.Timestamp(frame[date_field].min()).normalize()
    last = pd.Timestamp(frame[date_field].max()).normalize()
    selected_calendar = calendar_index[(calendar_index >= first) & (calendar_index <= last)]
    if selected_calendar.empty:
        return
    indexed = frame.set_index(date_field).reindex(selected_calendar)
    start_index = int(calendar_index.get_indexer([selected_calendar[0]])[0])
    if start_index < 0:
        raise ValueError(f"Qlib dump date is outside the target calendar: {selected_calendar[0]}")
    target = _feature_dir(qlib_dir, code)
    for field in _dump_fields(
        frame,
        include_fields=include_fields,
        date_field=date_field,
        symbol_field=symbol_field,
    ):
        values = pd.to_numeric(indexed[field], errors="coerce").to_numpy(dtype="<f4", copy=False)
        path = target / f"{field.lower()}.day.bin"
        if append:
            if not path.is_file():
                raise FileNotFoundError(
                    f"incremental Qlib dump cannot append a field missing from the base provider: {path}"
                )
            with path.open("ab") as handle:
                values.tofile(handle)
        else:
            np.concatenate((np.asarray([start_index], dtype="<f4"), values)).astype(
                "<f4", copy=False
            ).tofile(path)


def _write_calendar(qlib_dir: Path, calendar: list[pd.Timestamp]) -> None:
    target = qlib_dir / "calendars" / "day.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(f"{value:%Y-%m-%d}\n" for value in calendar), encoding="utf-8")


def _read_calendar(qlib_dir: Path) -> list[pd.Timestamp]:
    path = qlib_dir / "calendars" / "day.txt"
    if not path.is_file():
        raise FileNotFoundError(f"base Qlib calendar is missing: {path}")
    values = [
        pd.Timestamp(line.strip()).normalize()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not values:
        raise ValueError("base Qlib calendar is empty")
    return values


def _write_instruments(
    qlib_dir: Path, instruments: dict[str, tuple[pd.Timestamp, pd.Timestamp]]
) -> None:
    path = qlib_dir / "instruments" / "all.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"{code}\t{start:%Y-%m-%d}\t{end:%Y-%m-%d}\n"
        for code, (start, end) in sorted(instruments.items())
    ]
    path.write_text("".join(lines), encoding="utf-8")


def _read_instruments(qlib_dir: Path) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    path = qlib_dir / "instruments" / "all.txt"
    if not path.is_file():
        raise FileNotFoundError(f"base Qlib instruments are missing: {path}")
    result: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        code, start, end = line.split("\t")[:3]
        result[code.upper()] = (pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize())
    if not result:
        raise ValueError("base Qlib instruments are empty")
    return result


def _dump_all(
    files: list[Path],
    *,
    qlib_dir: Path,
    include_fields: tuple[str, ...],
    date_field: str,
    symbol_field: str,
) -> None:
    frames: list[tuple[Path, pd.DataFrame]] = []
    calendar_values: set[pd.Timestamp] = set()
    instruments: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    for path in files:
        frame = _read_frame(path, date_field=date_field)
        if frame.empty:
            continue
        code = _code(path)
        frames.append((path, frame))
        calendar_values.update(pd.Timestamp(value).normalize() for value in frame[date_field].tolist())
        instruments[code] = (
            pd.Timestamp(frame[date_field].min()).normalize(),
            pd.Timestamp(frame[date_field].max()).normalize(),
        )
    if not frames or not calendar_values:
        raise ValueError("Qlib dump source contains no usable rows")
    calendar = sorted(calendar_values)
    _write_calendar(qlib_dir, calendar)
    _write_instruments(qlib_dir, instruments)
    for path, frame in frames:
        _write_fields(
            frame,
            code=_code(path),
            qlib_dir=qlib_dir,
            calendar=calendar,
            include_fields=include_fields,
            date_field=date_field,
            symbol_field=symbol_field,
        )


def _dump_update(
    files: list[Path],
    *,
    qlib_dir: Path,
    include_fields: tuple[str, ...],
    date_field: str,
    symbol_field: str,
) -> None:
    old_calendar = _read_calendar(qlib_dir)
    instruments = _read_instruments(qlib_dir)
    loaded: list[tuple[Path, pd.DataFrame]] = []
    new_dates: set[pd.Timestamp] = set()
    for path in files:
        frame = _read_frame(path, date_field=date_field)
        if frame.empty:
            continue
        loaded.append((path, frame))
        new_dates.update(
            pd.Timestamp(value).normalize()
            for value in frame[date_field].tolist()
            if pd.Timestamp(value).normalize() > old_calendar[-1]
        )
    calendar = [*old_calendar, *sorted(new_dates)]
    for path, frame in loaded:
        code = _code(path)
        existing = instruments.get(code)
        if existing is None:
            _write_fields(
                frame,
                code=code,
                qlib_dir=qlib_dir,
                calendar=calendar,
                include_fields=include_fields,
                date_field=date_field,
                symbol_field=symbol_field,
            )
            instruments[code] = (
                pd.Timestamp(frame[date_field].min()).normalize(),
                pd.Timestamp(frame[date_field].max()).normalize(),
            )
            continue
        incremental = frame.loc[frame[date_field] > existing[1]]
        if incremental.empty:
            continue
        segment_calendar = [
            value for value in calendar if existing[1] < value <= pd.Timestamp(incremental[date_field].max())
        ]
        _write_fields(
            incremental,
            code=code,
            qlib_dir=qlib_dir,
            calendar=segment_calendar,
            include_fields=include_fields,
            date_field=date_field,
            symbol_field=symbol_field,
            append=True,
        )
        instruments[code] = (existing[0], pd.Timestamp(incremental[date_field].max()).normalize())
    _write_calendar(qlib_dir, calendar)
    _write_instruments(qlib_dir, instruments)


def _dump_fix(
    files: list[Path],
    *,
    qlib_dir: Path,
    include_fields: tuple[str, ...],
    date_field: str,
    symbol_field: str,
) -> None:
    calendar = _read_calendar(qlib_dir)
    instruments = _read_instruments(qlib_dir)
    for path in files:
        frame = _read_frame(path, date_field=date_field)
        if frame.empty:
            continue
        code = _code(path)
        _write_fields(
            frame,
            code=code,
            qlib_dir=qlib_dir,
            calendar=calendar,
            include_fields=include_fields,
            date_field=date_field,
            symbol_field=symbol_field,
        )
        if code not in instruments:
            instruments[code] = (
                pd.Timestamp(frame[date_field].min()).normalize(),
                pd.Timestamp(frame[date_field].max()).normalize(),
            )
    _write_instruments(qlib_dir, instruments)


def dump_qlib_bin(
    mode: str,
    *,
    data_path: str | Path,
    qlib_dir: str | Path,
    file_suffix: str = ".parquet",
    date_field_name: str = "date",
    symbol_field_name: str = "symbol",
    include_fields: Iterable[str] = (),
) -> None:
    """Write a Qlib day-frequency provider without a separate Qlib source checkout."""

    source = Path(data_path).expanduser().resolve()
    target = Path(qlib_dir).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    files = _source_files(source, file_suffix)
    fields = tuple(include_fields)
    if mode == "dump_all":
        _dump_all(
            files,
            qlib_dir=target,
            include_fields=fields,
            date_field=date_field_name,
            symbol_field=symbol_field_name,
        )
        return
    if mode == "dump_update":
        _dump_update(
            files,
            qlib_dir=target,
            include_fields=fields,
            date_field=date_field_name,
            symbol_field=symbol_field_name,
        )
        return
    if mode == "dump_fix":
        _dump_fix(
            files,
            qlib_dir=target,
            include_fields=fields,
            date_field=date_field_name,
            symbol_field=symbol_field_name,
        )
        return
    raise ValueError(f"unsupported Qlib dump mode: {mode}")
