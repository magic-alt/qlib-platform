from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from qlib_platform.data.production_daily_sync import ProductionDailySyncService
from qlib_platform.data.quality import assert_quality, validate_raw_day, write_report
from qlib_platform.data.universe import configured_universe
from qlib_platform.production_policy import required_endpoint_coverage


def _daily_run_freshness_config(settings_data: Mapping[str, Any]) -> Mapping[str, Any]:
    production = settings_data.get("production", {})
    production = production if isinstance(production, Mapping) else {}
    daily_run = production.get("daily_run", {})
    daily_run = daily_run if isinstance(daily_run, Mapping) else {}
    freshness = daily_run.get("freshness", {})
    return freshness if isinstance(freshness, Mapping) else {}


class CertifiedDailySyncService(ProductionDailySyncService):
    """Production sync with the complete #129 release freshness contract."""

    def create_plan(self, *, as_of: str | None = None, mode: str = "routine"):
        # Resolve the implicit business date in the configured exchange timezone and
        # provider-ready window, not in the host/UTC calendar.
        resolved = as_of
        if resolved is None:
            resolved = self._eligible_date(None).strftime("%Y-%m-%d")
        path = super().create_plan(as_of=resolved, mode=mode)
        if self.settings.environment == "prod":
            plan = self.load_plan(path.parent.name)
            plan["production_policy"] = self.settings.production_policy_report()
            self._save_plan(plan)
        return path

    def _validate_staged_overlay(self, plan: Mapping[str, Any]) -> None:
        """Validate candidate required rows before mutating canonical Bronze."""

        stage = self._market_stage(str(plan["plan_id"]))
        deep_dates = sorted({trade_date for _, trade_date in self._stage_targets(plan)})
        for trade_date in deep_dates:
            frames: dict[str, pd.DataFrame] = {}
            for dataset in ("daily", "adj_factor", "daily_basic"):
                frames[dataset] = (
                    stage.read(dataset, trade_date)
                    if stage.exists(dataset, trade_date)
                    else self.store.read(dataset, trade_date)
                )
            report = validate_raw_day(frames, trade_date)
            write_report(
                report,
                self.plan_root / str(plan["plan_id"]) / "quality" / f"pre_promote_{trade_date}.json",
            )
            assert_quality(report)

    def _promote_staged_raw(
        self,
        plan: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        if self._step_done(state, "raw_promote"):
            return
        self._validate_staged_overlay(plan)
        self._validate_stage_bases(plan)
        stage = self._market_stage(str(plan["plan_id"]))
        staged: dict[tuple[str, str], pd.DataFrame] = {}
        metadata: dict[tuple[str, str], dict[str, Any]] = {}
        for target in self._stage_targets(plan):
            staged[target] = stage.read(*target)
            metadata[target] = self._canonical_metadata(stage.read_manifest(*target))

        planned_dates, planned_revised, planned_changes = self._planned_raw_change_summary(plan)
        actual_dates, actual_revised, _ = self._promote_raw(staged, metadata, check_only=False)
        changed_dates = sorted(set(planned_dates) | set(actual_dates))
        revised_symbols = set(planned_revised) | set(actual_revised)

        incoming_path = self._stage_root(str(plan["plan_id"])) / "dividend" / "incoming.parquet"
        incoming = pd.read_parquet(incoming_path) if incoming_path.is_file() else pd.DataFrame()
        dividend = self.actions.upsert(incoming, check_only=False)

        pending = self._load_pending_publish()
        if pending:
            changed_dates = sorted(
                set(changed_dates) | {str(value) for value in pending.get("changed_trade_dates", [])}
            )
            revised_symbols.update(str(value) for value in pending.get("revised_symbols", []))
        pit_changed = bool(pending.get("pit_changed", False)) if pending else False
        self._write_pending_publish(
            run_id=str(plan["plan_id"]),
            changed_dates=changed_dates,
            revised_symbols=revised_symbols,
            pit_changed=pit_changed,
        )
        state["context"] = {
            "changed_trade_dates": changed_dates,
            "revised_symbols": sorted(revised_symbols),
            "pit_changed": pit_changed,
        }

        # Advance the factor cache before checkpointing raw_promote. If this write is
        # interrupted, the canonical promotion is idempotent and resume retries only
        # the missing cache/checkpoint work rather than refetching provider history.
        for symbol in set(plan.get("factor_event_symbols", [])):
            path = self._factor_history_path(str(plan["plan_id"]), str(symbol))
            if path.is_file():
                self._write_factor_index(
                    str(symbol),
                    pd.read_parquet(path),
                    str(plan["target_session"]),
                )

        self._finish_step(
            state,
            "raw_promote",
            {
                "changed_trade_dates": changed_dates,
                "revised_symbols": sorted(revised_symbols),
                "raw_changes": planned_changes,
                "dividend": dividend,
            },
        )

    def _market_cross_section_gate(self, target_session: str) -> dict[str, Any]:
        cfg = _daily_run_freshness_config(self.settings.data)
        minimum_ratio = float(cfg.get("min_market_coverage_ratio", 0.95))
        if not 0 < minimum_ratio <= 1:
            raise ValueError("production.daily_run.freshness.min_market_coverage_ratio must be in (0, 1]")

        dates = [date for date in self.store.list_dates("daily") if date <= target_session]
        previous = next((date for date in reversed(dates) if date < target_session), None)
        if previous is None:
            return {
                "fresh": True,
                "target_session": target_session,
                "previous_session": None,
                "coverage_ratio": None,
                "minimum_ratio": minimum_ratio,
                "reason": "no_previous_session_for_ratio_gate",
            }

        current = self.store.read("daily", target_session)
        prior = self.store.read("daily", previous)
        current_count = int(current["ts_code"].astype(str).nunique()) if "ts_code" in current else 0
        previous_count = int(prior["ts_code"].astype(str).nunique()) if "ts_code" in prior else 0
        ratio = current_count / max(1, previous_count)
        return {
            "fresh": current_count > 0 and ratio >= minimum_ratio,
            "target_session": target_session,
            "previous_session": previous,
            "target_symbols": current_count,
            "previous_symbols": previous_count,
            "coverage_ratio": ratio,
            "minimum_ratio": minimum_ratio,
        }

    def _required_endpoint_policy_gate(self, target_session: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for endpoint in ("daily", "adj_factor", "daily_basic"):
            policy = required_endpoint_coverage(self.settings.data, endpoint)
            if not policy:
                continue
            dates = [date for date in self.store.list_dates(endpoint) if date <= target_session]
            previous = next((date for date in reversed(dates) if date < target_session), None)
            current = self.store.read(endpoint, target_session)
            current_rows = len(current)
            previous_rows = len(self.store.read(endpoint, previous)) if previous is not None else 0
            coverage_ratio = current_rows / max(1, previous_rows) if previous is not None else None
            minimum_rows = int(policy["min_rows"])
            minimum_ratio = float(policy["min_previous_session_ratio"])
            fresh = current_rows >= minimum_rows and (
                coverage_ratio is None or coverage_ratio >= minimum_ratio
            )
            result[endpoint] = {
                "fresh": fresh,
                "target_session": target_session,
                "previous_session": previous,
                "rows": current_rows,
                "previous_rows": previous_rows if previous is not None else None,
                "coverage_ratio": coverage_ratio,
                "policy": dict(policy),
            }
        return result

    def _freshness_gate(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        result = super()._freshness_gate(plan)
        failures = [str(value) for value in result.get("failures", [])]
        target_session = str(plan["target_session"])
        target = pd.Timestamp(target_session).normalize()

        required_endpoint_policy = self._required_endpoint_policy_gate(target_session)
        result["required_endpoint_policy"] = required_endpoint_policy
        for endpoint, status in required_endpoint_policy.items():
            if not bool(status.get("fresh")):
                failures.append(f"required_endpoint_policy:{endpoint}:{target_session}")

        market_cross_section = self._market_cross_section_gate(target_session)
        result["market_cross_section"] = market_cross_section
        if not market_cross_section["fresh"]:
            failures.append(f"market_cross_section:{target_session}")

        configured = configured_universe(self.settings)
        if configured is None:
            result["universe"] = {
                "fresh": True,
                "kind": "all",
                "active_members": None,
            }
        else:
            cfg = _daily_run_freshness_config(self.settings.data)
            max_snapshot_age = int(cfg.get("max_universe_snapshot_age_calendar_days", 45))
            if max_snapshot_age < 1:
                raise ValueError(
                    "production.daily_run.freshness.max_universe_snapshot_age_calendar_days must be positive"
                )
            name, index_code, path = configured
            active_members = 0
            snapshot_date: str | None = None
            snapshot_age_days: int | None = None
            error: str | None = None
            if not path.is_file():
                error = "membership file missing"
            else:
                try:
                    frame = pd.read_parquet(path)
                    required = {"instrument", "snapshot_date", "effective_from", "effective_to"}
                    missing = sorted(required - set(frame.columns))
                    if missing:
                        error = f"missing columns: {missing}"
                    else:
                        effective_from = pd.to_datetime(
                            frame["effective_from"], errors="coerce"
                        ).dt.normalize()
                        effective_to = pd.to_datetime(frame["effective_to"], errors="coerce").dt.normalize()
                        active = effective_from.le(target) & effective_to.ge(target)
                        active_members = int(frame.loc[active, "instrument"].nunique())
                        snapshots = pd.to_datetime(
                            frame.loc[active, "snapshot_date"], errors="coerce"
                        ).dropna()
                        if active_members <= 0:
                            error = "no point-in-time members cover target session"
                        elif snapshots.empty:
                            error = "active membership has no snapshot_date"
                        else:
                            latest_snapshot = pd.Timestamp(snapshots.max()).normalize()
                            snapshot_date = latest_snapshot.strftime("%Y%m%d")
                            snapshot_age_days = int((target - latest_snapshot).days)
                            if snapshot_age_days < 0:
                                error = "active membership snapshot is from the future"
                            elif snapshot_age_days > max_snapshot_age:
                                error = (
                                    f"active membership snapshot age {snapshot_age_days}d exceeds "
                                    f"{max_snapshot_age}d"
                                )
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
            fresh = error is None
            result["universe"] = {
                "fresh": fresh,
                "kind": name,
                "index_code": index_code,
                "path": str(path),
                "active_members": active_members,
                "snapshot_date": snapshot_date,
                "snapshot_age_calendar_days": snapshot_age_days,
                "max_snapshot_age_calendar_days": max_snapshot_age,
                "error": error,
            }
            if not fresh:
                failures.append(f"universe:{name}:{target_session}")

        result["failures"] = list(dict.fromkeys(failures))
        result["passed"] = not failures
        return result
