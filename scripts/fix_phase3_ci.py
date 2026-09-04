from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    ingestion = ROOT / "src" / "qlib_platform" / "data" / "ingestion.py"
    replace_once(
        ingestion,
        '        return self._operation("preflight")(mysql_cfg, start_date, end_date)\n',
        '        result = self._operation("preflight")(mysql_cfg, start_date, end_date)\n'
        '        if not isinstance(result, dict):\n'
        '            raise TypeError("data source preflight operation must return a dict")\n'
        '        return result\n',
    )

    test_cli = ROOT / "tests" / "test_cli.py"
    replace_once(
        test_cli,
        "from qlib_platform.cli import _report_payload, main, parser\n",
        "from qlib_platform.cli import main, parser\nfrom qlib_platform.cli.main import _report_payload\n",
    )

    architecture = ROOT / "tests" / "test_architecture_phase3.py"
    replace_once(
        architecture,
        "from qlib_platform.settings import Paths\n",
        "from qlib_platform.settings import Paths, Settings\n",
    )
    text = architecture.read_text(encoding="utf-8")
    marker = "\ndef test_cli_is_composed_from_domain_registrars():\n"
    check = (
        "\ndef test_provider_credentials_are_not_a_settings_service():\n"
        "    assert not hasattr(Settings, \"require_token\")\n\n"
    )
    if check.strip() not in text:
        if marker not in text:
            raise RuntimeError("architecture test insertion marker missing")
        text = text.replace(marker, check + marker, 1)
        architecture.write_text(text, encoding="utf-8")

    docs = ROOT / "docs" / "package_architecture.md"
    replace_once(
        docs,
        "applies_to_commit: PHASE3_BRANCH",
        "applies_to_commit: e3d9eeea02b5f6b4b13ea196c00a14aab9aa21b3",
    )

    print("Phase 3 CI fixups applied")


if __name__ == "__main__":
    main()
