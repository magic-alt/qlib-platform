from __future__ import annotations

import sys
from pathlib import Path

from qlib_platform.research.run_adapters import record_official_parity_run
from qlib_platform.research.workflow import official_parity
from qlib_platform.settings import Settings


def main() -> int:
    argv = sys.argv[1:]
    args = official_parity._parser().parse_args(argv)
    settings = Settings.load(args.config, require_tushare=False, create_dirs=False)
    output = Path(args.output_dir).expanduser().resolve()
    try:
        code = official_parity.main()
    except Exception:
        if (output / "plan.json").is_file():
            record_official_parity_run(settings, output, argv=argv, status="FAILED")
        raise
    record_official_parity_run(settings, output, argv=argv)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
