from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from qlib_platform.research.evidence.experiment_store import ExperimentStore
from qlib_platform.research.reporting.research_console_render import render_research_console

__all__ = ["render_research_console", "serve_research_console"]


def _ids(query: dict[str, list[str]], name: str) -> list[str]:
    return [item for value in query.get(name, []) for item in value.split(",") if item]


def _api_payload(store: ExperimentStore, path: str) -> object:
    if path == "/api/experiments":
        return store.list_experiments(limit=500).to_dict(orient="records")
    if path == "/api/models":
        return store.list_models(limit=500).to_dict(orient="records")
    if path == "/api/factors":
        return store.list_factors(limit=1000).to_dict(orient="records")
    if path == "/api/portfolios":
        return store.list_portfolios(limit=500).to_dict(orient="records")
    if path.startswith("/api/experiment/"):
        return store.get_experiment(path.removeprefix("/api/experiment/"))
    return None


def serve_research_console(
    store: ExperimentStore,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/":
                body = render_research_console(
                    store,
                    compare_ids=_ids(query, "compare"),
                    compare_model_ids=_ids(query, "compare_models"),
                    compare_factor_ids=_ids(query, "compare_factors"),
                    compare_portfolio_ids=_ids(query, "compare_portfolios"),
                ).encode("utf-8")
                self._send(200, "text/html; charset=utf-8", body)
                return
            payload = _api_payload(store, parsed.path)
            if payload is None:
                self._send(404, "application/json", b'{"error":"not found"}')
                return
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the qlib-platform research metadata console")
    parser.add_argument(
        "--db", default="research_experiments.duckdb", help="DuckDB path or PostgreSQL DSN"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    with ExperimentStore(args.db) as store:
        serve_research_console(store, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
