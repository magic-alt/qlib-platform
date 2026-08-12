from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from .app import create_app
from .config import GatewaySettings
from .nav_store import NavStore
from .xtquant_client import XtQuantReadOnlyClient


def _today() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local, read-only QMT Broker Gateway")
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="run the local HTTP gateway")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    capture = subcommands.add_parser("nav-capture", help="capture the current QMT total asset as closing NAV")
    capture.add_argument("--trade-date", default=_today())
    flow = subcommands.add_parser("nav-cash-flow", help="record an external net cash flow")
    flow.add_argument("--trade-date", default=_today())
    flow.add_argument("--amount", required=True, type=float)
    flow.add_argument("--reference", default="")
    args = parser.parse_args(argv)
    settings = GatewaySettings.from_environment()
    store = NavStore(settings.state_dir)
    if args.command == "serve":
        import uvicorn

        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
        return 0
    if args.command == "nav-capture":
        client = XtQuantReadOnlyClient(settings)
        client.ensure_connected()
        store.capture(args.trade_date, client.query_asset().total_asset)
        return 0
    store.record_cash_flow(args.trade_date, args.amount, args.reference)
    return 0
