from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"patch anchor not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def main() -> None:
    replace(
        "src/qlib_platform/runtime/sre.py",
        """    active_dataset: dict[str, Any] = {}\n    try:\n        registered = DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)\n    except (OSError, ValueError):\n        registered = None\n""",
        """    active_dataset: dict[str, Any] = {}\n    registered = None\n    if settings.registry_path.is_file():\n        try:\n            registered = DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)\n        except (OSError, ValueError):\n            registered = None\n""",
    )
    replace(
        "src/qlib_platform/runtime/sre.py",
        """def evaluate_daily_slo(\n    settings: Settings,\n    *,\n    plan: Mapping[str, Any],\n    run_state: Mapping[str, Any],\n    dataset: Mapping[str, Any],\n    sync_state: Mapping[str, Any],\n    observed: Mapping[str, Any] | None = None,\n) -> dict[str, Any]:\n    policy = load_slo_policy(settings)\n""",
        """def policy_from_snapshot(settings: Settings, snapshot: Mapping[str, Any] | None) -> SloPolicy:\n    if isinstance(snapshot, Mapping):\n        effective = snapshot.get(\"effective\")\n        version = str(snapshot.get(\"version\") or \"\")\n        profile = str(snapshot.get(\"profile\") or \"\")\n        if isinstance(effective, Mapping) and version.startswith(\"slo-\") and profile:\n            return SloPolicy(\n                profile=profile,\n                version=version,\n                source=str(snapshot.get(\"source\") or \"bound-run-policy\"),\n                source_sha256=str(snapshot.get(\"source_sha256\") or \"\"),\n                effective=dict(effective),\n            )\n    return load_slo_policy(settings)\n\n\ndef evaluate_daily_slo(\n    settings: Settings,\n    *,\n    plan: Mapping[str, Any],\n    run_state: Mapping[str, Any],\n    dataset: Mapping[str, Any],\n    sync_state: Mapping[str, Any],\n    observed: Mapping[str, Any] | None = None,\n    policy: SloPolicy | None = None,\n) -> dict[str, Any]:\n    policy = policy or policy_from_snapshot(settings, _mapping(run_state.get(\"slo_policy\")))\n""",
    )
    replace(
        "src/qlib_platform/runtime/sre.py",
        """def evaluate_run_completion(settings: Settings, manifest: Mapping[str, Any]) -> dict[str, Any]:\n    policy = load_slo_policy(settings)\n    status = str(manifest.get(\"status\") or \"UNKNOWN\")\n""",
        """def evaluate_run_completion(settings: Settings, manifest: Mapping[str, Any]) -> dict[str, Any]:\n    policy = policy_from_snapshot(settings, _mapping(manifest.get(\"slo_policy\")))\n    status = str(manifest.get(\"status\") or \"UNKNOWN\")\n""",
    )
    replace(
        "src/qlib_platform/runtime/sre.py",
        """    notification = _step_status(manifest, \"notification\")\n    slo_step = _mapping(_mapping(manifest.get(\"steps\")).get(\"slo_gate\"))\n    slo_output = _mapping(slo_step.get(\"output\"))\n    policy_bound = bool(slo_output.get(\"policy_version\"))\n""",
        """    notification = _step_status(manifest, \"notification\")\n    notification_ok = notification in {\"SUCCEEDED\", \"SKIPPED\"} or (\n        not successful and notification == \"BLOCKED\"\n    )\n    slo_step = _mapping(_mapping(manifest.get(\"steps\")).get(\"slo_gate\"))\n    slo_output = _mapping(slo_step.get(\"output\"))\n    policy_bound = bool(\n        slo_output.get(\"policy_version\") or _mapping(manifest.get(\"slo_policy\")).get(\"version\")\n    )\n""",
    )
    replace(
        "src/qlib_platform/runtime/sre.py",
        '            status="PASS" if notification in {"SUCCEEDED", "SKIPPED"} else "FAIL",\n',
        '            status="PASS" if notification_ok else "FAIL",\n',
    )
    replace(
        "src/qlib_platform/runtime/sre.py",
        """                if notification in {\"SUCCEEDED\", \"SKIPPED\"}\n                else \"notification delivery did not complete\"\n""",
        """                if notification_ok\n                else \"notification delivery did not complete\"\n""",
    )

    replace(
        "src/qlib_platform/runtime/daily_research_run.py",
        '        order = ["sync_publish", "dataset_verify", "regression_backtest", "report", "notification"]\n',
        """        order = [\n            \"sync_publish\",\n            \"dataset_verify\",\n            \"slo_gate\",\n            \"regression_backtest\",\n            \"report\",\n            \"notification\",\n        ]\n""",
    )
    replace(
        "src/qlib_platform/runtime/daily_research_run.py",
        "    def _regression_enabled(self, override: bool | None) -> bool:\n",
        """    def _post_dataset_gate(\n        self,\n        plan: Mapping[str, Any],\n        state: dict[str, Any],\n        dataset: Mapping[str, Any],\n    ) -> tuple[bool, str | None, dict[str, Any]]:\n        return True, None, {\n            \"status\": \"NOT_CONFIGURED\",\n            \"allow_downstream\": True,\n            \"reason\": \"no post-dataset SLO gate is configured for this runner\",\n        }\n\n    def _regression_enabled(self, override: bool | None) -> bool:\n""",
    )
    replace(
        "src/qlib_platform/runtime/daily_research_run.py",
        """                regression_enabled = False if backfill else self._regression_enabled(regression)\n                self._run_regression(\n""",
        """                slo_input = _identity(\n                    {\n                        \"target_session\": plan[\"target_session\"],\n                        \"dataset_version_id\": dataset.get(\"dataset_version_id\"),\n                        \"dataset_manifest_sha256\": dataset.get(\"dataset_manifest_sha256\"),\n                        \"slo_policy\": state.get(\"slo_policy\"),\n                    },\n                    prefix=\"slo-\",\n                )\n                try:\n                    if self._step_reusable(state, \"slo_gate\", slo_input):\n                        slo_output = dict(self._step(state, \"slo_gate\").get(\"output\", {}))\n                        allow_downstream = bool(slo_output.get(\"allow_downstream\", True))\n                        block_reason = str(slo_output.get(\"block_reason\") or \"\") or None\n                    else:\n                        allow_downstream, block_reason, slo_output = self._post_dataset_gate(\n                            plan, state, dataset\n                        )\n                        slo_output = {\n                            **slo_output,\n                            \"allow_downstream\": allow_downstream,\n                            \"block_reason\": block_reason,\n                        }\n                        self._finish_step(\n                            state,\n                            \"slo_gate\",\n                            status=\"SUCCEEDED\" if allow_downstream else \"BLOCKED\",\n                            input_hash=slo_input,\n                            output=slo_output,\n                            error=block_reason if not allow_downstream else None,\n                        )\n                except Exception as exc:\n                    self._finish_step(\n                        state,\n                        \"slo_gate\",\n                        status=\"FAILED\",\n                        input_hash=slo_input,\n                        error=f\"{type(exc).__name__}: {exc}\",\n                    )\n                    self._block_downstream(state, \"slo_gate\", str(exc))\n                    raise\n\n                if not allow_downstream:\n                    reason = block_reason or \"post-dataset SLO gate blocked downstream research\"\n                    state[\"status\"] = \"BLOCKED\"\n                    state[\"block_reason\"] = reason\n                    state[\"finished_at_utc\"] = datetime.now(timezone.utc).isoformat()\n                    self._block_downstream(state, \"slo_gate\", reason)\n                    report = self._render_report(plan, state)\n                    manifest = {\n                        **state,\n                        \"plan\": str(self.sync._plan_path(plan_id)),\n                        \"report\": str(report),\n                        \"dataset\": dataset,\n                        \"immutable_input\": {\n                            \"data_release_id\": dataset.get(\"data_release_id\"),\n                            \"dataset_version_id\": dataset.get(\"dataset_version_id\"),\n                            \"dataset_manifest_sha256\": dataset.get(\"dataset_manifest_sha256\"),\n                        },\n                        \"backfill\": backfill,\n                    }\n                    return _atomic_json(manifest, self._manifest_path(plan_id))\n\n                regression_enabled = False if backfill else self._regression_enabled(regression)\n                self._run_regression(\n""",
    )

    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        "from qlib_platform.runtime import daily_research_run as base\n",
        """from qlib_platform.runtime import daily_research_run as base\nfrom qlib_platform.runtime.sre import (\n    SreEventStore,\n    evaluate_daily_slo,\n    evaluate_run_completion,\n    load_slo_policy,\n    policy_from_snapshot,\n    write_slo_evaluation,\n)\n""",
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        '        state.setdefault("endpoint_gaps", plan.get("endpoint_gaps", {}))\n',
        '        state.setdefault("endpoint_gaps", plan.get("endpoint_gaps", {}))\n        state.setdefault("slo_policy", load_slo_policy(self.settings).snapshot())\n',
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        "    def _business_run_id(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> str:\n",
        """    def _post_dataset_gate(\n        self,\n        plan: Mapping[str, Any],\n        state: dict[str, Any],\n        dataset: Mapping[str, Any],\n    ) -> tuple[bool, str | None, dict[str, Any]]:\n        policy = policy_from_snapshot(self.settings, state.get(\"slo_policy\"))\n        sync_state = self._sync_apply_state(state)\n        evaluation = evaluate_daily_slo(\n            self.settings,\n            plan=plan,\n            run_state=state,\n            dataset=dataset,\n            sync_state=sync_state,\n            policy=policy,\n        )\n        evaluation_path = write_slo_evaluation(self.settings, evaluation)\n        source = self.settings.data.get(\"data_source\", {})\n        source = source if isinstance(source, Mapping) else {}\n        provider = str(source.get(\"kind\") or source.get(\"provider\") or \"unknown\")\n        context = {\n            \"run_id\": state.get(\"run_id\"),\n            \"session\": plan.get(\"target_session\"),\n            \"release\": dataset.get(\"data_release_id\"),\n            \"provider\": provider,\n        }\n        events = SreEventStore(self.settings.paths.state).sync_evaluation(evaluation, context=context)\n        blocking = [str(value) for value in evaluation.get(\"blocking_reasons\", [])]\n        reason = \", \".join(blocking) if blocking else None\n        return bool(evaluation.get(\"allow_downstream\")), reason, {\n            \"status\": evaluation.get(\"status\"),\n            \"policy_version\": policy.version,\n            \"profile\": policy.profile,\n            \"evaluation\": str(evaluation_path),\n            \"blocking_reasons\": blocking,\n            \"alert_event_ids\": [event.get(\"event_id\") for event in events],\n        }\n\n    def _business_run_id(self, plan: Mapping[str, Any], state: Mapping[str, Any]) -> str:\n""",
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        '                "contract": DAILY_RUN_CONTRACT_VERSION,\n                "target_session": plan.get("target_session"),\n',
        '                "contract": DAILY_RUN_CONTRACT_VERSION,\n                "slo_policy_version": dict(state.get("slo_policy") or {}).get("version"),\n                "target_session": plan.get("target_session"),\n',
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        '            "provider": {\n                "watermarks_at_plan": state.get("provider_watermarks", {}),\n',
        '            "slo_policy": dict(state.get("slo_policy") or {}),\n            "provider": {\n                "watermarks_at_plan": state.get("provider_watermarks", {}),\n',
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        """            handle.write(f\"- Benchmark: `{lineage['benchmark']['symbol'] or 'N/A'}`\\n\")\n            handle.write(\"- Automatic model selection/promotion: `false/false`\\n\")\n""",
        """            handle.write(f\"- Benchmark: `{lineage['benchmark']['symbol'] or 'N/A'}`\\n\")\n            handle.write(f\"- SLO policy: `{lineage['slo_policy'].get('version') or 'N/A'}`\\n\")\n            slo_output = self._step(state, \"slo_gate\").get(\"output\", {})\n            slo_output = slo_output if isinstance(slo_output, Mapping) else {}\n            handle.write(f\"- SLO status: `{slo_output.get('status') or 'N/A'}`\\n\")\n            handle.write(\"- Automatic model selection/promotion: `false/false`\\n\")\n""",
    )
    replace(
        "src/qlib_platform/runtime/production_daily_run.py",
        '        payload["checkpoint_ledger"] = self._checkpoint_ledger(plan, payload)\n        return base._atomic_json(payload, path)\n',
        """        payload[\"checkpoint_ledger\"] = self._checkpoint_ledger(plan, payload)\n        completion = evaluate_run_completion(self.settings, payload)\n        completion_path = write_slo_evaluation(self.settings, completion)\n        source = self.settings.data.get(\"data_source\", {})\n        source = source if isinstance(source, Mapping) else {}\n        dataset = payload.get(\"dataset\", {})\n        dataset = dataset if isinstance(dataset, Mapping) else {}\n        events = SreEventStore(self.settings.paths.state).sync_evaluation(\n            completion,\n            context={\n                \"run_id\": payload.get(\"run_id\"),\n                \"session\": payload.get(\"target_session\"),\n                \"release\": dataset.get(\"data_release_id\"),\n                \"provider\": str(source.get(\"kind\") or source.get(\"provider\") or \"unknown\"),\n            },\n        )\n        slo_step = self._step(payload, \"slo_gate\")\n        pre_research = slo_step.get(\"output\", {})\n        payload[\"slo\"] = {\n            \"policy\": payload.get(\"slo_policy\", {}),\n            \"pre_research\": dict(pre_research) if isinstance(pre_research, Mapping) else {},\n            \"completion\": {\n                \"status\": completion.get(\"status\"),\n                \"policy_version\": completion.get(\"policy_version\"),\n                \"evaluation\": str(completion_path),\n                \"blocking_reasons\": completion.get(\"blocking_reasons\", []),\n                \"alert_event_ids\": [event.get(\"event_id\") for event in events],\n            },\n        }\n        return base._atomic_json(payload, path)\n""",
    )

    replace(
        "src/qlib_platform/research/evidence/run_adapters.py",
        "    artifacts.extend(dataset_artifacts)\n\n    steps = payload.get(\"steps\", {})\n",
        """    artifacts.extend(dataset_artifacts)\n    slo = payload.get(\"slo\", {})\n    slo = slo if isinstance(slo, Mapping) else {}\n    for section in (\"pre_research\", \"completion\"):\n        record = slo.get(section, {})\n        record = record if isinstance(record, Mapping) else {}\n        raw_evaluation = record.get(\"evaluation\")\n        if raw_evaluation and Path(str(raw_evaluation)).is_file():\n            artifacts.append(artifact_record(Path(str(raw_evaluation)), role=\"evidence\"))\n\n    steps = payload.get(\"steps\", {})\n""",
    )
    old_market = """        \"market\": canonical_business_value(\n            settings.data.get(\"market_rules\", settings.data.get(\"backtest\", {}))\n        ),\n"""
    daily_start = Path("src/qlib_platform/research/evidence/run_adapters.py").read_text(encoding="utf-8").index(
        "def record_daily_run("
    )
    target = Path("src/qlib_platform/research/evidence/run_adapters.py")
    text = target.read_text(encoding="utf-8")
    market_index = text.index(old_market, daily_start)
    text = (
        text[:market_index]
        + """        \"market\": {\n            \"market_rules\": canonical_business_value(\n                settings.data.get(\"market_rules\", settings.data.get(\"backtest\", {}))\n            ),\n            \"slo_policy\": lineage.get(\"slo_policy\", slo.get(\"policy\", {})),\n        },\n"""
        + text[market_index + len(old_market) :]
    )
    target.write_text(text, encoding="utf-8")
    replace(
        "src/qlib_platform/research/evidence/run_adapters.py",
        '        gates={"checkpoint_ledger": payload.get("checkpoint_ledger", {})},\n',
        """        gates={\n            \"checkpoint_ledger\": payload.get(\"checkpoint_ledger\", {}),\n            \"slo\": slo,\n        },\n""",
    )

    replace(
        "src/qlib_platform/runtime/standalone_status.py",
        "    registered = DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)\n",
        """    registered = (\n        DatasetRegistry(settings.registry_path).inspect(settings.qlib_dataset_ref)\n        if settings.registry_path.is_file()\n        else None\n    )\n""",
    )
    replace(
        "src/qlib_platform/runtime/standalone_status.py",
        "    return {\n        \"mode\": settings.mode,\n",
        """    from qlib_platform.runtime.sre import collect_sre_status\n\n    return {\n        \"mode\": settings.mode,\n""",
    )
    replace(
        "src/qlib_platform/runtime/standalone_status.py",
        """        \"unavailableCapabilities\": (\n            [\"lean_validation\", \"qmt\", \"oms\", \"broker_execution\"] if platform != \"healthy\" else []\n        ),\n    }\n""",
        """        \"unavailableCapabilities\": (\n            [\"lean_validation\", \"qmt\", \"oms\", \"broker_execution\"] if platform != \"healthy\" else []\n        ),\n        \"sre\": collect_sre_status(settings),\n    }\n""",
    )
    replace(
        "src/qlib_platform/runtime/standalone_status.py",
        '        f"Platform          {payload[\'platform\'].upper()}",\n    ]\n',
        """        f\"Platform          {payload['platform'].upper()}\",\n        f\"SRE               {payload['sre']['status']}\",\n        f\"Recent session    {payload['sre'].get('recent_session') or 'N/A'}\",\n        f\"Active release    {payload['sre'].get('active_release') or 'N/A'}\",\n        f\"SLO version       {payload['sre']['policy'].get('version') or 'N/A'}\",\n        f\"SRE blockers      {', '.join(payload['sre'].get('blocking_reasons', [])) or 'none'}\",\n    ]\n""",
    )

    replace(
        "src/qlib_platform/cli/commands/runtime.py",
        '    status = sub.add_parser("status")\n    status.add_argument("--json", action="store_true", dest="as_json")\n',
        """    status = sub.add_parser(\"status\")\n    status.add_argument(\"--json\", action=\"store_true\", dest=\"as_json\")\n    doctor = sub.add_parser(\"doctor\")\n    doctor.add_argument(\"--json\", action=\"store_true\", dest=\"as_json\")\n    sre = sub.add_parser(\"sre\")\n    sre_sub = sre.add_subparsers(dest=\"sre_command\", required=True)\n    sre_status = sre_sub.add_parser(\"status\")\n    sre_status.add_argument(\"--json\", action=\"store_true\", dest=\"as_json\")\n    sre_override = sre_sub.add_parser(\"override\")\n    sre_override.add_argument(\"--gate\", required=True)\n    sre_override.add_argument(\"--operator\", required=True)\n    sre_override.add_argument(\"--reason\", required=True)\n    sre_override.add_argument(\"--expires-at\", required=True)\n    sre_override.add_argument(\"--session\")\n    sre_override.add_argument(\"--release\")\n    sre_game_day = sre_sub.add_parser(\"game-day\")\n    sre_game_day.add_argument(\n        \"--scenario\",\n        required=True,\n        choices=[\n            \"provider-late\",\n            \"provider-429\",\n            \"schema-drift\",\n            \"endpoint-missing\",\n            \"qlib-corrupt\",\n            \"disk-full\",\n            \"process-kill\",\n            \"pointer-crash\",\n            \"alert-destination-unavailable\",\n            \"long-backfill\",\n        ],\n    )\n    sre_game_day.add_argument(\"--output-dir\", required=True)\n""",
    )
    replace(
        "src/qlib_platform/cli/main.py",
        '    if args.command == "health":\n',
        """    if args.command in {\"doctor\", \"sre\"}:\n        from qlib_platform.runtime.sre import (\n            collect_sre_status,\n            create_override,\n            render_sre_status,\n            run_game_day_fixture,\n        )\n\n        sre_settings = Settings.load(args.config, create_dirs=False)\n        if args.command == \"doctor\" or args.sre_command == \"status\":\n            payload = collect_sre_status(sre_settings)\n            print(json.dumps(payload, ensure_ascii=False) if args.as_json else render_sre_status(payload))\n            return\n        if args.sre_command == \"override\":\n            scope = {\n                key: value\n                for key, value in {\"session\": args.session, \"release\": args.release}.items()\n                if value\n            }\n            override_settings = Settings.load(args.config, create_dirs=True)\n            print(\n                json.dumps(\n                    create_override(\n                        override_settings,\n                        gate=args.gate,\n                        operator=args.operator,\n                        reason=args.reason,\n                        expires_at=args.expires_at,\n                        scope=scope,\n                    ),\n                    ensure_ascii=False,\n                )\n            )\n            return\n        if args.sre_command == \"game-day\":\n            fixture_settings = Settings.load(args.config, create_dirs=True)\n            path = run_game_day_fixture(\n                fixture_settings,\n                scenario=args.scenario,\n                output=Path(args.output_dir).expanduser().resolve(),\n            )\n            print(json.dumps({\"evidence\": str(path)}, ensure_ascii=False))\n            return\n    if args.command == \"health\":\n""",
    )

    replace(
        "tests/test_sre_contract.py",
        '    config = tmp_path / "pipeline.yaml"\n',
        '    tmp_path.mkdir(parents=True, exist_ok=True)\n    config = tmp_path / "pipeline.yaml"\n',
    )
    test_path = Path("tests/test_sre_contract.py")
    test_path.write_text(
        test_path.read_text(encoding="utf-8")
        + r'''


def test_base_daily_runner_blocks_regression_when_consumer_gate_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from qlib_platform.runtime import daily_research_run as daily

    settings = _settings(tmp_path)
    runner = daily.DailyResearchRun(settings)
    plan = {
        "plan_id": "plan-fixture",
        "target_session": "20260916",
        "config_sha256": "cfg",
        "status": "PLANNED",
    }
    monkeypatch.setattr(runner.sync, "load_plan", lambda plan_id: plan)
    monkeypatch.setattr(runner.sync, "_plan_path", lambda plan_id: tmp_path / "plan.json")
    monkeypatch.setattr(runner.sync, "apply_plan", lambda *args, **kwargs: tmp_path / "apply.json")
    monkeypatch.setattr(daily, "_session_ready", lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(runner, "_verify_dataset", lambda *args, **kwargs: _dataset(tmp_path))
    monkeypatch.setattr(
        runner,
        "_post_dataset_gate",
        lambda *args, **kwargs: (
            False,
            "data.required_freshness",
            {"status": "BLOCKED", "policy_version": "slo-fixture"},
        ),
    )
    regression_called = False

    def regression(*args, **kwargs):
        nonlocal regression_called
        regression_called = True
        return {}

    monkeypatch.setattr(runner, "_run_regression", regression)
    path = runner.execute_plan("plan-fixture")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "BLOCKED"
    assert payload["steps"]["slo_gate"]["status"] == "BLOCKED"
    assert payload["steps"]["regression_backtest"]["status"] == "BLOCKED"
    assert regression_called is False
''',
        encoding="utf-8",
    )

    docs = Path("docs/operations/sre-slo-and-game-day.md")
    docs.parent.mkdir(parents=True, exist_ok=True)
    docs.write_text(
        """# Research Platform SRE: SLO, alerting, recovery, and game-day

This runbook is the operating contract for Issue #135. It covers research/data/platform SLO evidence only; it never authorizes model selection, retraining, promotion, deployment, or broker execution.

## SLO policy and versioning

The machine-readable policy lives at `configs/slo_policy.yaml` with schema `qlib-platform.slo-policy.v1`. The runtime hashes the selected profile plus `sre.policy_overrides` into an immutable `slo-*` version and binds the complete snapshot to the first DailyRun attempt. A resumed run therefore keeps the policy that originally governed it even if repository defaults later change.

Profiles are `dev`, `benchmark`, and `prod`. Production freshness minutes are intentionally **unset** in source control. `prod` fails closed until an operator calibrates a deadline from observed provider/update/runtime distributions and pins it through configuration. Do not invent a threshold during an incident.

Example calibrated override:

```yaml
sre:
  profile: prod
  policy_overrides:
    freshness:
      deadline_minutes: <calibrated-value>
```

Changing that value changes the SLO policy version and the DailyRun/RunManifest lineage.

## Read-only status and doctor

```bash
qlib-platform --config configs/pipeline.standalone.yaml doctor
qlib-platform --config configs/pipeline.standalone.yaml doctor --json
qlib-platform --config configs/pipeline.standalone.yaml sre status --json
```

Status is observational. It reports the recent target session, provider watermarks when available, active immutable release/DatasetVersion, latest DailyRun/SLO evidence, missed sessions, disk capacity, orphan temporary artifacts, active alerts/overrides, and blocking reasons. The status path must not create a registry, repair data, move aliases, or mutate checkpoints.

## Fail-closed consumption gate

The production DailyRun sequence is:

```text
plan -> audited/resumable sync -> DatasetVersion verify -> SLO gate -> frozen regression -> report
```

Required target-session freshness, required quality, calibrated production deadline, and required DatasetVersion verification are evaluated before research consumption. A BLOCKING failure writes SLO evidence, opens/deduplicates an incident, marks the DailyRun `BLOCKED`, and leaves regression/report delivery downstream blocked. Recovery resumes the same durable plan and immutable input; do not skip the gate by editing state files.

## Alert semantics

Alert fingerprints aggregate by `provider + session + failed_gate`, so a provider outage does not fan out into symbol-level alerts. Events use `INFO`, `WARN`, or `BLOCKING` and include run/session/release/provider/gate/reason/first action. Repeated evaluation of the same incident is deduplicated. When the gate passes again, a `RESOLVED` event is appended with the same incident correlation ID.

The append-only event ledger is `state/sre/events.jsonl`. Corruption is itself a blocking status condition; do not silently recreate it.

## Manual override

There is no invisible force bypass. An override requires operator identity, reason, and future expiry and creates an audit event:

```bash
qlib-platform --config configs/pipeline.standalone.yaml sre override \
  --gate platform.capacity \
  --operator <operator> \
  --reason "temporary storage migration" \
  --expires-at 2026-09-17T16:00:00+08:00 \
  --session 20260917
```

Overrides are auditable evidence; they do not rewrite immutable artifacts or automatically convert a failing data/research gate into a passing result.

## Recovery playbooks

### Provider late / HTTP 429

1. Confirm `data.required_freshness` and provider/session correlation in `sre status`.
2. Preserve the SyncPlan and checkpoint ledger; do not create a replacement release from partial data.
3. Restore provider access/rate budget, then resume the same plan.
4. Require freshness/quality and DatasetVersion verification to pass before research consumes it.
5. Confirm a correlated `RESOLVED` event.

### Bad release or missing endpoint

1. Inspect `raw_validate`, `freshness_gate`, and endpoint-gap evidence.
2. Repair the source/staged input and resume; never hand-edit an immutable DataRelease/DatasetVersion manifest.
3. If the active release is known-good, keep it active until the replacement fully verifies.

### Corrupt Qlib/DatasetVersion artifact

1. Stop downstream research consumption.
2. Run immutable DatasetVersion verification and compare the manifest checksum.
3. Re-materialize from the certified parent release rather than patching binaries/features in place.
4. Replay and require artifact verification PASS plus `RESOLVED` evidence.

### Missed schedule / process kill / reboot

1. `doctor --json` identifies missed trading sessions from the local calendar.
2. Resume the durable plan/checkpoints for each missing session in order.
3. Use backfill only to advance beyond active immutable coverage; historical revisions use historical-audit mode.
4. Verify no duplicate alert fan-out and no regression against partial sessions.

### Capacity / disk full

1. Treat `platform.capacity` as blocking when a calibrated minimum-free policy is configured.
2. Free/expand storage without deleting immutable evidence needed by an active run.
3. Check orphan `*.tmp` artifacts and checkpoint integrity.
4. Resume the same plan and verify the incident resolves.

### Manual backfill

```bash
python -m qlib_platform.runtime.production_daily_run --backfill <START> <END>
```

Backfill remains fail-closed and never rolls the active DatasetVersion backward. Long backfills are an SRE schedule scenario, not permission to bypass freshness/quality checks.

## Game-day / fault injection

Supported deterministic fixtures:

- `provider-late`
- `provider-429`
- `schema-drift`
- `endpoint-missing`
- `qlib-corrupt`
- `disk-full`
- `process-kill`
- `pointer-crash`
- `alert-destination-unavailable`
- `long-backfill`

Example:

```bash
qlib-platform --config configs/pipeline.standalone.yaml sre game-day \
  --scenario provider-late \
  --output-dir ./data/output/sre-game-day
```

Each fixture emits evidence for failure -> alert -> repeated-evaluation dedupe -> recovery -> RESOLVED, and verifies incident correlation. The alert-destination scenario is WARN/degraded; data/integrity/capacity/recovery scenarios are blocking.

## Baseline metric drift

Baseline drift evidence can classify a metric as `PASS`, `INVESTIGATE`, or `REJECT`. The contract always records `model_action=NONE` and an empty automatic-action list. SRE monitoring must not trigger automatic model selection, retraining, promotion, or deployment.
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
