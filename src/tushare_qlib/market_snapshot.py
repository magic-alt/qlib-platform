from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Protocol, Sequence, runtime_checkable

import pandas as pd

from .broker.http_readonly import ReadOnlyJsonClient
from .freshness import validate_execution_snapshot
from .settings import Settings


REQUIRED_QUOTE_COLUMNS = {
    "instrument",
    "price",
    "paused",
    "is_limit_up",
    "is_limit_down",
    "adv20_volume",
    "adv20_amount",
    "as_of_trade_date",
    "snapshot_at_utc",
}


@runtime_checkable
class MarketSnapshotProvider(Protocol):
    source_name: str

    def snapshot(self, trade_date: str, instruments: Sequence[str]) -> pd.DataFrame: ...


def validate_market_snapshot(
    quotes: pd.DataFrame,
    *,
    trade_date: str,
    instruments: Sequence[str],
    max_age_seconds: int,
) -> pd.DataFrame:
    missing = REQUIRED_QUOTE_COLUMNS - set(quotes.columns)
    if missing:
        raise ValueError(f"quotes snapshot missing fields: {sorted(missing)}")
    frame = quotes.copy()
    frame["instrument"] = frame["instrument"].astype(str).str.upper().str.strip()
    if frame["instrument"].duplicated().any():
        raise ValueError("quotes snapshot contains duplicate instruments")
    required = {str(value).upper().strip() for value in instruments}
    absent = required - set(frame["instrument"])
    if absent:
        raise ValueError(f"quotes snapshot is partial; missing instruments: {sorted(absent)[:10]}")
    validate_execution_snapshot(
        frame,
        name="quotes",
        trade_date=trade_date,
        max_age_seconds=max_age_seconds,
    )
    numeric = frame[["price", "adv20_volume", "adv20_amount"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if numeric.isna().any().any() or (numeric < 0).any().any() or (numeric["price"] <= 0).any():
        raise ValueError("quotes snapshot contains invalid price or ADV values")
    for column in ("paused", "is_limit_up", "is_limit_down"):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not values.isin([0, 1]).all():
            raise ValueError(f"quotes snapshot has invalid {column} flags")
        frame[column] = values.astype(int)
    return frame


class InboxMarketSnapshotProvider:
    source_name = "inbox"

    def __init__(self, root: Path, *, max_age_seconds: int) -> None:
        self.root = Path(root)
        self.max_age_seconds = int(max_age_seconds)

    def snapshot(self, trade_date: str, instruments: Sequence[str]) -> pd.DataFrame:
        path = self.root / trade_date / "quotes.csv"
        if not path.is_file():
            raise FileNotFoundError(f"pretrade quotes inbox is missing for {trade_date}: {path.name}")
        return validate_market_snapshot(
            pd.read_csv(path),
            trade_date=trade_date,
            instruments=instruments,
            max_age_seconds=self.max_age_seconds,
        )


class HttpMarketSnapshotProvider:
    source_name = "http_readonly"

    def __init__(
        self,
        client: ReadOnlyJsonClient,
        *,
        endpoint: str = "quotes",
        max_age_seconds: int = 120,
    ) -> None:
        self.client = client
        self.endpoint = endpoint
        self.max_age_seconds = int(max_age_seconds)

    def snapshot(self, trade_date: str, instruments: Sequence[str]) -> pd.DataFrame:
        requested = sorted({str(value).upper().strip() for value in instruments})
        payload = self.client.get(
            self.endpoint,
            trade_date=trade_date,
            instruments=",".join(requested),
        )
        if isinstance(payload, Mapping):
            payload = payload.get("quotes", payload.get("items"))
        if not isinstance(payload, list):
            raise ValueError("market quote response must be a JSON list")
        return validate_market_snapshot(
            pd.DataFrame(payload),
            trade_date=trade_date,
            instruments=requested,
            max_age_seconds=self.max_age_seconds,
        )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def market_snapshot_provider_from_settings(settings: Settings) -> MarketSnapshotProvider:
    production = _mapping(settings.data.get("production"))
    config = _mapping(production.get("market"))
    execution = _mapping(settings.data.get("execution"))
    max_age = int(str(execution.get("max_quote_age_seconds", 120)))
    kind = str(config.get("kind", "inbox"))
    if kind == "inbox":
        return InboxMarketSnapshotProvider(
            settings.paths.root / "inbox" / "pretrade", max_age_seconds=max_age
        )
    if kind != "http_readonly":
        raise ValueError(f"unsupported market snapshot provider: {kind}")
    token_env = str(config.get("token_env", "")).strip()
    headers = {"Accept": "application/json"}
    if token_env:
        token = os.environ.get(token_env, "").strip()
        if not token:
            raise RuntimeError(f"market gateway token environment variable is missing: {token_env}")
        header = str(config.get("token_header", "Authorization")).strip() or "Authorization"
        headers[header] = token if header.lower() != "authorization" else f"Bearer {token}"
    return HttpMarketSnapshotProvider(
        ReadOnlyJsonClient(
            str(config.get("base_url", "")),
            headers=headers,
            timeout_seconds=float(str(config.get("timeout_seconds", 10))),
            max_attempts=int(str(config.get("max_attempts", 3))),
            retry_delay_seconds=float(str(config.get("retry_delay_seconds", 0.25))),
        ),
        endpoint=str(config.get("endpoint", "quotes")),
        max_age_seconds=max_age,
    )
