from __future__ import annotations

import argparse
from pathlib import Path

from tushare_qlib.trade_plan import build_trade_plan


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a T+1 trade plan from a Qlib selection artifact")
    parser.add_argument("--config", default="configs/trading_execution_template.yaml")
    parser.add_argument("--selection-file")
    parser.add_argument("--selection-date")
    parser.add_argument("--current-portfolio")
    parser.add_argument("--trade-date")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path, plan = build_trade_plan(
        config_path=args.config,
        selection_file=args.selection_file,
        selection_date=args.selection_date,
        prev_selection_file=args.current_portfolio,
        trade_date=args.trade_date,
    )
    print(f"Build plan complete: rows={len(plan)}, file={Path(path)}")


if __name__ == "__main__":
    main()
