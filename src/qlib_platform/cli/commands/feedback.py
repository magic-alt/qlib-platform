"""Register CLI commands owned by the feedback domain."""


def register(sub) -> None:
    realized_labels = sub.add_parser("feedback-build-labels")
    realized_labels.add_argument("--labels", required=True)
    realized_labels.add_argument("--calendar", required=True)
    realized_labels.add_argument("--observed-through", required=True)
    realized_labels.add_argument("--data-release-id", required=True)
    realized_labels.add_argument("--label-spec-id", required=True)
    realized_labels.add_argument("--horizon-days", required=True, type=int)
    realized_labels.add_argument("--signal-lag-days", required=True, type=int)
    realized_labels.add_argument("--price-field", choices=["open", "close"], default="close")
    realized_labels.add_argument("--source-artifact-id", required=True)
    realized_labels.add_argument("--output", required=True)
    feedback_evaluate = sub.add_parser("feedback-evaluate")
    feedback_evaluate.add_argument("--predictions", required=True)
    feedback_evaluate.add_argument("--realized-labels", required=True)
    feedback_evaluate.add_argument("--output", required=True)
    feedback_evaluate.add_argument("--topk", type=int, default=50)
    feedback_evaluate.add_argument("--min-cross-section", type=int, default=20)
    feedback_evaluate.add_argument("--rolling-window", type=int, default=20)
