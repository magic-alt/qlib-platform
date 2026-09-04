from pathlib import Path

path = Path(__file__).with_name("research_refactor_once.py")
source = path.read_text(encoding="utf-8")
start = source.index("    old = '''    if args.command")
end = source.index("\n\n    # Strengthen the architecture contract", start)
replacement = '''    new = \'\'\'    candidate_commands = {\\n        "candidate-validate",\\n        "candidate-plan",\\n        "candidate-data-accept",\\n        "candidate-collect",\\n        "candidate-accept",\\n        "candidate-select",\\n        "final-holdout-open",\\n    }\\n    stability_commands = {\\n        "stability-validate",\\n        "stability-plan",\\n        "stability-diagnose",\\n        "stability-portable-export",\\n    }\\n    if args.command in candidate_commands or args.command in stability_commands:\\n        from qlib_platform.releases.capabilities import require_release_capability\\n\\n        # Capability identifiers are persisted governance identities and remain backward compatible.\\n        require_release_capability(\\n            settings,\\n            "phase2" if args.command in candidate_commands else "phase3",\\n        )\\n\'\'\'\n    guard_start = text.index(\'    if args.command.startswith("candidate-")\')\n    dispatch_start = text.index(\'\\n    if args.command == "candidate-validate":\', guard_start)\n    cli_main.write_text(text[:guard_start] + new + text[dispatch_start:], encoding="utf-8")'''
path.write_text(source[:start] + replacement + source[end:], encoding="utf-8")
Path(__file__).unlink()
