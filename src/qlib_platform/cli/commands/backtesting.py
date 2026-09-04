"""Register CLI commands owned by the backtesting domain."""

from qlib_platform.runtime.runtime_resources import resource_argument


def register(sub) -> None:
    pb = sub.add_parser("backtest-predictions")
    pb.add_argument("predictions")
    pb.add_argument("--benchmark")
    pb.add_argument("--topn", type=int)
    pb.add_argument("--n-drop", type=int)
    pb.add_argument("--hold-thresh", type=int)
    pb.add_argument("--artifact-level", choices=["minimal", "full"], default="minimal")
    pb.add_argument("--dataset-ref")
    tp = sub.add_parser("build-target-portfolio")
    tp.add_argument("--portfolio-config", default=resource_argument("configs/target_portfolio.yaml"))
    tp.add_argument("--selection-file")
    tp.add_argument("--selection-date")
    tp.add_argument("--current-portfolio")
    tp.add_argument("--trade-date")
    le = sub.add_parser("lean-export")
    le.add_argument("target_file")
    le.add_argument("--output-dir")
    le.add_argument("--signal-date")
    le.add_argument("--trade-date")
    le.add_argument("--model-id")
    le.add_argument("--dataset-id")
