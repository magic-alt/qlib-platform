from pathlib import Path

path = Path("tests/test_sre_contract.py")
text = path.read_text(encoding="utf-8")

start = text.index("def test_alert_fingerprint_deduplicates_and_emits_resolved")
end = text.index("\ndef test_event_ledger_corruption_fails_closed", start)
chunk = text[start:end]
chunk = chunk.replace("dataset=_dataset(tmp_path),", "dataset=_dataset(tmp_path, valid_hash=True),")
text = text[:start] + chunk + text[end:]

old = '''    settings = _settings(tmp_path)\n    runner = daily.DailyResearchRun(settings)\n    plan = {\n'''
new = '''    settings = _settings(tmp_path)\n\n    class FakeSync:\n        def load_plan(self, plan_id: str):\n            raise AssertionError("load_plan must be patched by the test")\n\n        def _plan_path(self, plan_id: str) -> Path:\n            raise AssertionError("_plan_path must be patched by the test")\n\n        def apply_plan(self, *args, **kwargs):\n            raise AssertionError("apply_plan must be patched by the test")\n\n    monkeypatch.setattr(daily, "PlannedDailySyncService", lambda current: FakeSync())\n    runner = daily.DailyResearchRun(settings)\n    plan = {\n'''
if old not in text:
    raise SystemExit("base runner fixture anchor not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
