from __future__ import annotations

import argparse
from pathlib import Path

from qlib_platform.research.full_walk_forward_acceptance import build_full_walk_forward_acceptance


def main() -> None:
    parser = argparse.ArgumentParser(description="Certify Full Walk-forward Acceptance evidence")
    parser.add_argument("--ridge", type=Path, nargs=2, required=True, metavar=("BASELINE", "RESUMED"))
    parser.add_argument("--lightgbm", type=Path, nargs=2, required=True, metavar=("BASELINE", "RESUMED"))
    parser.add_argument("--xgboost", type=Path, nargs=2, required=True, metavar=("BASELINE", "RESUMED"))
    parser.add_argument("--corruption-rebuild", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = build_full_walk_forward_acceptance(
        {
            "ridge": tuple(args.ridge),
            "lightgbm": tuple(args.lightgbm),
            "xgboost": tuple(args.xgboost),
        },
        corruption_rebuild=args.corruption_rebuild,
        output=args.output,
    )
    print(path)


if __name__ == "__main__":
    main()
