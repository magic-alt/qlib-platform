from __future__ import annotations

import argparse
from collections.abc import Sequence

from qlib_platform.datasets.qlib_dump_compat import dump_qlib_bin


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="qlib-platform packaged Qlib 0.9.7 dump compatibility CLI")
    command.add_argument("mode", choices=("dump_all", "dump_update", "dump_fix"))
    command.add_argument("--data_path", required=True)
    command.add_argument("--qlib_dir", required=True)
    command.add_argument("--freq", default="day")
    command.add_argument("--file_suffix", default=".parquet")
    command.add_argument("--date_field_name", default="date")
    command.add_argument("--symbol_field_name", default="symbol")
    command.add_argument("--include_fields", default="")
    command.add_argument("--max_workers", type=int, default=1)
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.freq != "day":
        raise ValueError("the packaged Qlib exporter currently supports freq=day only")
    dump_qlib_bin(
        args.mode,
        data_path=args.data_path,
        qlib_dir=args.qlib_dir,
        file_suffix=args.file_suffix,
        date_field_name=args.date_field_name,
        symbol_field_name=args.symbol_field_name,
        include_fields=tuple(field.strip() for field in args.include_fields.split(",") if field.strip()),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
