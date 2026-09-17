from __future__ import annotations

import sys
from pathlib import Path

from qlib_platform.research.run_adapters import record_quickstart_run
from qlib_platform.research.workflow import governed_quickstart as governed
from qlib_platform.research.workflow import quickstart as legacy
from qlib_platform.settings import Settings


def main() -> int:
    argv = sys.argv[1:]
    args = governed._parser().parse_args(argv)
    settings = Settings.load(args.config, require_tushare=False, create_dirs=True)
    original_write = legacy._write_matrix

    def write_with_manifest(root: Path, payload: dict[str, object]) -> None:
        original_write(root, payload)
        record_quickstart_run(settings, payload, root, argv=argv)

    legacy._write_matrix = write_with_manifest
    try:
        return governed.main()
    finally:
        legacy._write_matrix = original_write


if __name__ == "__main__":
    raise SystemExit(main())
