from __future__ import annotations

import html

import pandas as pd

from qlib_platform.research.evidence.experiment_store import ExperimentStore


def _table(frame: pd.DataFrame, *, empty: str = "No records") -> str:
    if frame.empty:
        return f'<p class="empty">{html.escape(empty)}</p>'
    columns = list(frame.columns)
    head = "".join(f"<th>{html.escape(str(column))}</th>" for column in columns)
    rows = []
    for _, row in frame.iterrows():
        cells = "".join(f"<td>{html.escape(str(row[column]))}</td>" for column in columns)
        rows.append(f"<tr>{cells}</tr>")
    return (
        "<div class='table-wrap'><table><thead><tr>"
        f"{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def render_research_console(
    store: ExperimentStore,
    *,
    compare_ids: list[str] | None = None,
    compare_model_ids: list[str] | None = None,
    compare_factor_ids: list[str] | None = None,
    compare_portfolio_ids: list[str] | None = None,
) -> str:
    sections: list[str] = []
    comparisons = (
        ("Experiment comparison", store.compare_experiments, compare_ids),
        ("Model comparison", store.compare_models, compare_model_ids),
        ("Factor comparison", store.compare_factors, compare_factor_ids),
        ("Portfolio comparison", store.compare_portfolios, compare_portfolio_ids),
    )
    for title, function, values in comparisons:
        if values:
            frame = function(values)
            if title == "Experiment comparison":
                frame = frame.reset_index()
            sections.append(f"<section><h2>{title}</h2>{_table(frame)}</section>")
    catalogs = (
        ("Experiments", store.list_experiments(limit=250)),
        ("Models", store.list_models(limit=250)),
        ("Factors", store.list_factors(limit=500)),
        ("Portfolios", store.list_portfolios(limit=250)),
    )
    catalog_html = "".join(f"<section><h2>{title}</h2>{_table(frame)}</section>" for title, frame in catalogs)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>qlib-platform Research Console</title>
<style>
:root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
body {{ margin: 0; background: #0b1020; color: #e8edf7; }}
header {{ padding: 32px 5vw 18px; border-bottom: 1px solid #29324a; }}
main {{ padding: 24px 5vw 48px; display: grid; gap: 24px; }}
section {{ background: #11182b; border: 1px solid #29324a; border-radius: 12px; padding: 18px; }}
h1,h2 {{ margin: 0 0 14px; }} p {{ color: #aeb8cc; }}
.table-wrap {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th,td {{ padding: 9px 10px; border-bottom: 1px solid #29324a; text-align: left; white-space: nowrap; }}
th {{ color: #9fb5e8; }} .empty {{ padding: 8px 0; }}
</style></head><body>
<header><h1>Research Console</h1>
<p>Read-only metadata view. Immutable artifacts remain in the governed artifact store.</p></header>
<main>{"".join(sections)}{catalog_html}</main></body></html>"""
