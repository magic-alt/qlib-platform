"""Register CLI commands owned by the ops domain."""

from qlib_platform.runtime.runtime_resources import resource_argument


def register(sub) -> None:
    lr = sub.add_parser("lean-register")
    lr.add_argument("manifest")
    lr.add_argument("--base-url")
    v2 = sub.add_parser("artifact-v2-export")
    v2.add_argument("manifest")
    v2.add_argument("--output-dir", required=True)
    v2.add_argument("--git-commit", required=True)
    v2.add_argument("--container-digest", required=True)
    v2.add_argument("--data-release-id")
    pa = sub.add_parser("project-audit")
    pa.add_argument("--root", default=".")
    pa.add_argument("--output", default="docs/project_audit.json")
    wc = sub.add_parser("validate-qrun-contract")
    wc.add_argument("--workflow", default=resource_argument("configs/workflow_lightgbm.yaml"))
    outbox = sub.add_parser("outbox")
    outbox_sub = outbox.add_subparsers(dest="outbox_command", required=True)
    outbox_drain = outbox_sub.add_parser("drain")
    outbox_drain.add_argument("--endpoint")
    outbox_drain.add_argument("--timeout-seconds", type=float, default=30.0)
    outbox_worker = outbox_sub.add_parser("worker")
    outbox_worker.add_argument("--endpoint")
    outbox_worker.add_argument("--timeout-seconds", type=float, default=30.0)
    outbox_worker.add_argument("--poll-seconds", type=float, default=30.0)
    outbox_worker.add_argument("--max-poll-seconds", type=float, default=300.0)
    outbox_worker.add_argument("--once", action="store_true")
    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_bootstrap = auth_sub.add_parser("bootstrap-admin")
    auth_bootstrap.add_argument("--username", default="admin")
    auth_create = auth_sub.add_parser("user-create")
    auth_create.add_argument("--username", required=True)
    auth_create.add_argument(
        "--role",
        action="append",
        choices=["admin", "operator", "researcher", "viewer"],
        default=[],
    )
    auth_sub.add_parser("user-list")
    ops_query = sub.add_parser("ops-query", help="query production state")
    ops_query.add_argument("--entity", choices=["runs", "deliveries"], required=True)
    ops_query.add_argument("--business-date")
    ops_query.add_argument("--status")
    ops_retry = sub.add_parser("ops-retry-delivery")
    ops_retry.add_argument("idempotency_key")
    ops_ack = sub.add_parser("ops-ack")
    ops_ack.add_argument("--entity", choices=["run", "delivery"], required=True)
    ops_ack.add_argument("--id", required=True, dest="entity_id")
    ops_ack.add_argument("--operator", required=True)
    ops_ack.add_argument("--reason", required=True)
    ops_summary = sub.add_parser("ops-summary")
    ops_summary.add_argument("--business-date", required=True)
    ops_summary.add_argument("--output")
