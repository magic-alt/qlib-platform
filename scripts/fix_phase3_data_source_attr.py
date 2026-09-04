from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "src" / "qlib_platform" / "data" / "ingestion.py"
text = path.read_text(encoding="utf-8")
old = "        self.binding = binding\n        self.client: DataSourceClient = binding.client\n"
new = "        self.binding = binding\n        self.data_source = binding\n        self.client: DataSourceClient = binding.client\n"
if text.count(old) != 1:
    raise RuntimeError(f"expected exactly one Extractor binding assignment, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
