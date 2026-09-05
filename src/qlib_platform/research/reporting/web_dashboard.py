from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from qlib_platform.research.evaluation.gates import ResearchThresholds
from qlib_platform.research.reporting.summary import summarize_job


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Render a self-contained HTML dashboard from a qlib-platform research matrix"
    )
    command.add_argument("matrix", nargs="?", help="research_matrix.json; omit with --latest")
    command.add_argument(
        "--latest",
        action="store_true",
        help="use the newest data/output/quickstart/*/research_matrix.json",
    )
    command.add_argument("--output", help="HTML output path; defaults beside the matrix")
    return command


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _fmt(value: object, digits: int = 3) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}"


def _pct(value: object, digits: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}%}"


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else "—"))


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _latest_matrix(root: Path) -> Path:
    candidates = sorted(
        (root / "data" / "output" / "quickstart").glob("*/research_matrix.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("no research_matrix.json found under data/output/quickstart")
    return candidates[0].resolve()


def resolve_matrix(
    matrix: str | Path | None,
    *,
    latest: bool = False,
    root: Path | None = None,
) -> Path:
    if matrix:
        path = Path(matrix).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    if not latest:
        raise ValueError("provide research_matrix.json or pass --latest")
    return _latest_matrix((root or Path.cwd()).resolve())


def _profile(job: Mapping[str, Any]) -> dict[str, Any]:
    raw = job.get("modelProfile")
    if not raw:
        return {}
    path = Path(str(raw)).expanduser()
    if not path.is_file():
        return {"path": str(path)}
    return {"path": str(path.resolve()), **_read_yaml(path)}


def _job_metrics(job: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _mapping(job.get("summary"))
    signal = dict(_mapping(summary.get("metrics")))
    backtest = _mapping(job.get("predictionBacktest"))
    portfolio = dict(_mapping(_mapping(backtest.get("summary")).get("metrics")))

    try:
        normalized = summarize_job(job)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        normalized = {}

    signal_fallbacks = {
        "ic_mean": normalized.get("icMean"),
        "rank_ic_mean": normalized.get("rankIcMean"),
        "icir": normalized.get("icir"),
        "rank_icir": normalized.get("rankIcir"),
        "long_short_annualized": normalized.get("longShortAnnualized"),
    }
    portfolio_fallbacks = {
        "excess_ir": normalized.get("excessIr"),
        "max_drawdown": normalized.get("maxDrawdown"),
        "turnover_mean": normalized.get("turnoverMean"),
        "turnover_total": normalized.get("turnoverTotal"),
        "cost_total": normalized.get("costTotal"),
        "returnTotal": normalized.get("returnTotal"),
        "benchTotal": normalized.get("benchmarkTotal"),
    }
    for target, fallbacks in (
        (signal, signal_fallbacks),
        (portfolio, portfolio_fallbacks),
    ):
        for key, value in fallbacks.items():
            if key not in target and value is not None:
                target[key] = value
    return signal, portfolio


def _analysis(
    signal: Mapping[str, Any],
    portfolio: Mapping[str, Any],
    decision: object,
) -> list[str]:
    notes: list[str] = []
    ic = _number(signal.get("ic_mean"))
    icir = _number(signal.get("icir"))
    rank_ic = _number(signal.get("rank_ic_mean"))
    rank_icir = _number(signal.get("rank_icir"))
    excess_ir = _number(portfolio.get("excess_ir"))
    cost = _number(portfolio.get("cost_total"))

    if rank_ic is not None and rank_ic >= 0.02:
        if rank_icir is not None and rank_icir < 0.50:
            notes.append(
                "The cross-sectional ranking contains useful average information, but its day-to-day "
                "stability is below the promotion threshold. Treat this as weak alpha, not a "
                "deployable signal."
            )
        else:
            notes.append(
                "RankIC clears the mean threshold; verify the same behavior across rolling OOS folds."
            )
    elif rank_ic is not None:
        notes.append(
            "RankIC is below the mean threshold, so feature and label quality matter more than model tuning."
        )

    if ic is not None and rank_ic is not None and rank_ic > ic * 1.5:
        notes.append(
            "RankIC is materially stronger than Pearson IC. Relative stock ordering is more reliable "
            "than predicted return magnitude, so portfolio construction should preserve rank information."
        )
    if icir is not None and rank_icir is not None and max(icir, rank_icir) < 0.50:
        notes.append(
            "Neither ICIR nor RankICIR clears the 0.50 stability gate; walk-forward stability "
            "is the next priority."
        )
    if excess_ir is not None and excess_ir < 0:
        notes.append(
            "Portfolio excess IR is negative: signal diagnostics do not translate into benchmark-relative "
            "economic value after portfolio constraints and costs."
        )
    if cost is not None and cost < 0:
        notes.append(
            "Transaction costs are a measurable drag; evaluate holding horizon and turnover "
            "before parameter search."
        )
    if str(decision).upper() == "REJECT":
        notes.append(
            "REJECT is a research result, not a runtime failure. Do not lower the gate to make "
            "the experiment pass."
        )
    return notes


def _gate_rows(
    signal: Mapping[str, Any],
    portfolio: Mapping[str, Any],
) -> list[dict[str, Any]]:
    thresholds = ResearchThresholds()
    rows = [
        ("IC mean", signal.get("ic_mean"), thresholds.min_ic_mean, "higher"),
        ("RankIC mean", signal.get("rank_ic_mean"), thresholds.min_rank_ic_mean, "higher"),
        ("ICIR", signal.get("icir"), thresholds.min_icir, "higher"),
        ("RankICIR", signal.get("rank_icir"), thresholds.min_rank_icir, "higher"),
        (
            "Long-short annualized",
            signal.get("long_short_annualized"),
            thresholds.min_long_short_annualized,
            "higher",
        ),
        ("Excess IR", portfolio.get("excess_ir"), thresholds.min_excess_ir, "higher"),
        (
            "Max drawdown",
            portfolio.get("max_drawdown"),
            -thresholds.max_drawdown,
            "drawdown",
        ),
    ]
    result: list[dict[str, Any]] = []
    for label, value, threshold, mode in rows:
        current = _number(value)
        if current is None:
            passed = None
        elif mode == "drawdown":
            passed = abs(current) <= thresholds.max_drawdown
        else:
            passed = current >= float(threshold)
        result.append(
            {
                "label": label,
                "value": current,
                "threshold": threshold,
                "passed": passed,
            }
        )
    return result


def build_dashboard_data(
    payload: Mapping[str, Any],
    *,
    matrix_path: Path | None = None,
) -> dict[str, Any]:
    jobs: list[dict[str, Any]] = []
    global_warnings = [str(item) for item in _list(payload.get("observedWarnings"))]

    for raw_job in _list(payload.get("jobs")):
        if not isinstance(raw_job, Mapping):
            continue
        job = dict(raw_job)
        summary = _mapping(job.get("summary"))
        signal, portfolio = _job_metrics(job)
        decision = (
            summary.get("decision")
            or summary.get("promotionStatus")
            or job.get("status")
        )
        warnings = [str(item) for item in _list(job.get("warnings"))]
        warnings.extend(item for item in global_warnings if item not in warnings)
        jobs.append(
            {
                "alphaPack": job.get("alphaPack"),
                "model": job.get("model"),
                "status": job.get("status"),
                "decision": decision,
                "command": job.get("command"),
                "profile": _profile(job),
                "runtime": dict(_mapping(job.get("runtime"))),
                "summary": dict(summary),
                "signal": signal,
                "portfolio": portfolio,
                "gates": _gate_rows(signal, portfolio),
                "analysis": _analysis(signal, portfolio, decision),
                "warnings": warnings,
            }
        )

    return {
        "schemaVersion": "1.0",
        "sourceMatrix": str(matrix_path) if matrix_path else None,
        "status": payload.get("status"),
        "createdAtUtc": payload.get("createdAtUtc"),
        "datasetRef": payload.get("datasetRef"),
        "dataset": dict(_mapping(payload.get("dataset"))),
        "mode": payload.get("mode"),
        "stage": payload.get("stage"),
        "jobs": jobs,
    }


_STYLE = """
:root{--bg:#07111f;--panel:#0d1b2a;--panel2:#10243a;--line:#213b56;--text:#e7eef7;--muted:#91a6bd;--green:#48d597;--red:#ff6b7a;--amber:#ffc857;--blue:#64b5f6}*{box-sizing:border-box}body{margin:0;font:14px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:linear-gradient(180deg,#06101d,#091827 45%,#07111f);color:var(--text)}main{max-width:1240px;margin:auto;padding:40px 24px 80px}h1{font-size:38px;line-height:1.08;margin:0 0 10px}h2{font-size:21px;margin:0 0 18px}h3{font-size:15px;margin:0 0 8px}.eyebrow{letter-spacing:.13em;text-transform:uppercase;color:var(--blue);font-size:12px;font-weight:700}.muted{color:var(--muted)}.hero{padding:30px;border:1px solid var(--line);border-radius:20px;background:linear-gradient(135deg,#0f2740,#0b1726);box-shadow:0 18px 50px #0005}.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.badge{padding:6px 10px;border-radius:999px;background:#112943;border:1px solid #244665;color:#bcd6ef;font-size:12px}.badge.reject{color:#ffd3d8;border-color:#773745;background:#331a24}.section{margin-top:28px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}.card{grid-column:span 4;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}.wide{grid-column:span 6}.full{grid-column:1/-1}.metric{font-size:27px;font-weight:750}.label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em}.flow{display:grid;grid-template-columns:repeat(6,1fr);gap:8px}.step{padding:14px 10px;border-radius:12px;background:var(--panel2);border:1px solid var(--line);text-align:center}.step strong{display:block;margin-bottom:5px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{color:#afc4d8;font-weight:600;font-size:12px}code,pre{font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}pre{white-space:pre-wrap;word-break:break-word;background:#081522;border:1px solid var(--line);border-radius:10px;padding:14px;color:#bdd2e8}.pass{color:var(--green)}.fail{color:var(--red)}.unknown{color:var(--amber)}ul,ol{padding-left:20px;margin:8px 0}li{margin:6px 0}.footer{margin-top:36px;color:var(--muted);font-size:12px}@media(max-width:900px){.card,.wide{grid-column:1/-1}.flow{grid-template-columns:repeat(2,1fr)}h1{font-size:30px}}@media(max-width:520px){main{padding:20px 14px 50px}.flow{grid-template-columns:1fr}.hero{padding:20px}}
"""


def _kv_table(values: Mapping[str, Any]) -> str:
    if not values:
        return '<p class="muted">No evidence recorded.</p>'
    rows: list[str] = []
    for key, value in values.items():
        if isinstance(value, (dict, list)):
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            rendered = f"<code>{_escape(encoded)}</code>"
        else:
            rendered = f"<code>{_escape(value)}</code>"
        rows.append(f"<tr><th>{_escape(key)}</th><td>{rendered}</td></tr>")
    return '<div class="table-wrap"><table>' + "".join(rows) + "</table></div>"


def _metric_cards(job: Mapping[str, Any]) -> str:
    signal = _mapping(job.get("signal"))
    portfolio = _mapping(job.get("portfolio"))
    metrics = [
        ("IC", _fmt(signal.get("ic_mean"), 4)),
        ("RankIC", _fmt(signal.get("rank_ic_mean"), 4)),
        ("ICIR", _fmt(signal.get("icir"), 4)),
        ("RankICIR", _fmt(signal.get("rank_icir"), 4)),
        ("Long-short ann.", _pct(signal.get("long_short_annualized"))),
        ("Excess IR", _fmt(portfolio.get("excess_ir"), 3)),
    ]
    cards: list[str] = []
    for label, value in metrics:
        cards.append(
            f'<div class="card"><div class="label">{_escape(label)}</div>'
            f'<div class="metric">{value}</div></div>'
        )
    return "".join(cards)


def _gate_table(job: Mapping[str, Any]) -> str:
    rows: list[str] = []
    for raw_row in _list(job.get("gates")):
        if not isinstance(raw_row, Mapping):
            continue
        state = raw_row.get("passed")
        css = "pass" if state is True else "fail" if state is False else "unknown"
        text = "PASS" if state is True else "FAIL" if state is False else "N/A"
        rows.append(
            "<tr>"
            f"<td>{_escape(raw_row.get('label'))}</td>"
            f"<td><code>{_escape(_fmt(raw_row.get('value'), 4))}</code></td>"
            f"<td><code>{_escape(_fmt(raw_row.get('threshold'), 4))}</code></td>"
            f'<td class="{css}">{text}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>Gate</th><th>Observed</th><th>Threshold</th><th>Status</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _command(job: Mapping[str, Any]) -> str:
    command = job.get("command")
    if isinstance(command, list):
        return " ".join(str(item) for item in command)
    return str(command or "—")


def _list_items(items: object, empty_message: str) -> str:
    values = [str(item) for item in _list(items)]
    if not values:
        return f'<li class="muted">{_escape(empty_message)}</li>'
    return "".join(f"<li>{_escape(item)}</li>" for item in values)


def render_dashboard(data: Mapping[str, Any]) -> str:
    jobs = [item for item in _list(data.get("jobs")) if isinstance(item, Mapping)]
    job: Mapping[str, Any] = jobs[0] if jobs else {}
    summary = _mapping(job.get("summary"))
    runtime = dict(_mapping(job.get("runtime")))
    if "resolvedDevice" not in runtime and summary.get("resolvedDevice") is not None:
        runtime["resolvedDevice"] = summary.get("resolvedDevice")

    decision = job.get("decision") or data.get("status") or "UNKNOWN"
    profile = dict(_mapping(job.get("profile")))
    model_kwargs = dict(_mapping(profile.pop("model_kwargs", {})))
    profile["effectiveProfileOverrides"] = model_kwargs
    portfolio = _mapping(job.get("portfolio"))
    portfolio_view = {
        "returnTotal": portfolio.get("returnTotal"),
        "benchTotal": portfolio.get("benchTotal"),
        "excessIr": portfolio.get("excess_ir"),
        "maxDrawdown": portfolio.get("max_drawdown"),
        "turnoverMean": portfolio.get("turnover_mean"),
        "turnoverTotal": portfolio.get("turnover_total"),
        "costTotal": portfolio.get("cost_total"),
    }
    evidence = {
        "datasetRef": data.get("datasetRef"),
        **dict(_mapping(data.get("dataset"))),
    }
    analysis = _list_items(job.get("analysis"), "No automated interpretation available.")
    warnings = _list_items(job.get("warnings"), "No persisted warnings were supplied in the matrix.")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>qlib-platform Research Dashboard</title>
<style>{_STYLE}</style>
</head>
<body><main>
<section class="hero">
<div class="eyebrow">qlib-platform · research evidence</div>
<h1>Research Dashboard</h1>
<p class="muted">Environment → immutable data → model → signal → portfolio → research decision.</p>
<div class="badges">
<span class="badge">{_escape(data.get('mode'))}</span>
<span class="badge">stage: {_escape(data.get('stage'))}</span>
<span class="badge">{_escape(job.get('alphaPack'))} × {_escape(job.get('model'))}</span>
<span class="badge reject">{_escape(decision)}</span>
</div>
</section>

<section class="section"><h2>Research flow</h2><div class="flow">
<div class="step"><strong>1 · Environment</strong><span class="muted">runtime · config</span></div>
<div class="step"><strong>2 · DatasetVersion</strong><span class="muted">verify · lineage</span></div>
<div class="step"><strong>3 · AlphaPack</strong><span class="muted">features · label</span></div>
<div class="step"><strong>4 · Model</strong><span class="muted">fit · predict</span></div>
<div class="step"><strong>5 · Signal</strong><span class="muted">IC · RankIC</span></div>
<div class="step"><strong>6 · Portfolio</strong><span class="muted">cost · gate</span></div>
</div></section>

<section class="section"><h2>Pre-research environment & lineage</h2><div class="grid">
<div class="card wide"><h3>Dataset</h3>{_kv_table(evidence)}</div>
<div class="card wide"><h3>Runtime</h3>{_kv_table(runtime)}</div>
</div></section>

<section class="section"><h2>Model configuration</h2><div class="grid">
<div class="card wide"><h3>Profile and overrides</h3>{_kv_table(profile)}</div>
<div class="card wide"><h3>Reproducible command</h3><pre>{_escape(_command(job))}</pre>
<p class="muted">Adapter defaults remain authoritative when they are not persisted in run evidence.</p></div>
</div></section>

<section class="section"><h2>Signal quality</h2><div class="grid">{_metric_cards(job)}</div>
<div class="grid" style="margin-top:14px"><div class="card full">
<h3>Promotion gate checks</h3>{_gate_table(job)}</div></div></section>

<section class="section"><h2>Portfolio & backtest evidence</h2><div class="grid">
<div class="card wide"><h3>Portfolio metrics</h3>{_kv_table(portfolio_view)}</div>
<div class="card wide"><h3>Run summary</h3>{_kv_table(dict(summary))}</div>
</div></section>

<section class="section"><h2>Interpretation</h2><div class="grid">
<div class="card wide"><h3>What the result means</h3><ul>{analysis}</ul></div>
<div class="card wide"><h3>Data-quality watchlist</h3><ul>{warnings}</ul></div>
</div></section>

<section class="section"><h2>Recommended next research loop</h2><div class="card full"><ol>
<li>Resolve material data-quality warnings before model tuning.</li>
<li>Compare Ridge, LightGBM and XGBoost on the same AlphaPack and DatasetVersion.</li>
<li>Ablate Market → Daily → PIT → multifactor_core.</li>
<li>Move shortlisted recipes to rolling walk-forward OOS and inspect fold stability.</li>
<li>Compare 1D / 5D / 10D / 20D labels before optimizing turnover.</li>
<li>Test pre-registered Value, Profitability, Momentum, LowVol, Liquidity and Fundamental Momentum factors.</li>
<li>Tune TopK / holding policy only after stable alpha exists, then stress transaction costs.</li>
</ol></div></section>

<div class="footer">Source matrix: {_escape(data.get('sourceMatrix'))} · Read-only research evidence.</div>
</main></body></html>"""


def write_dashboard(
    matrix: str | Path,
    output: str | Path | None = None,
) -> Path:
    matrix_path = Path(matrix).expanduser().resolve()
    data = build_dashboard_data(_read_json(matrix_path), matrix_path=matrix_path)
    target = (
        Path(output).expanduser().resolve()
        if output
        else matrix_path.parent / "research_dashboard.html"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_dashboard(data), encoding="utf-8")
    return target


def main() -> int:
    args = parser().parse_args()
    matrix = resolve_matrix(args.matrix, latest=args.latest)
    target = write_dashboard(matrix, args.output)
    print(json.dumps({"matrix": str(matrix), "dashboard": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
