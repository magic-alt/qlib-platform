from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

from qlib_platform.runtime.runtime_resources import resource_argument, resource_path


def _absolute_existing(value: str, *, file: bool = False) -> Path:
    path = Path(value).expanduser().resolve()
    ready = path.is_file() if file else path.is_dir()
    if not ready:
        raise FileNotFoundError(path)
    return path


def render(
    kind: str, working_directory: Path, python_exe: Path, config_path: Path, output: Path
) -> list[Path]:
    templates = resource_path("deploy")
    replacements = {
        "@REPO_ROOT@": str(working_directory),
        "@PYTHON_EXE@": str(python_exe),
        "@CONFIG_PATH@": str(config_path),
    }
    sources: tuple[Path, ...]
    if kind == "systemd":
        sources = (
            templates / "systemd" / "qlib-platform-daily-sync.service.in",
            templates / "systemd" / "qlib-platform-daily-sync.timer",
        )
    elif kind == "launchd":
        sources = (templates / "launchd" / "com.qlib-platform.daily-sync.plist.in",)
        replacements = {key: escape(value) for key, value in replacements.items()}
    else:
        raise ValueError(f"unsupported scheduler kind: {kind}")
    output.mkdir(parents=True, exist_ok=True)
    rendered: list[Path] = []
    for source in sources:
        name = source.name.removesuffix(".in")
        target = output / name
        content = source.read_text(encoding="utf-8")
        for marker, value in replacements.items():
            content = content.replace(marker, value)
        if "@" in content:
            raise ValueError(f"unresolved scheduler template marker: {source}")
        target.write_text(content, encoding="utf-8")
        rendered.append(target)
    return rendered


def main() -> None:
    parser = argparse.ArgumentParser(description="Render standalone OS scheduler definitions")
    parser.add_argument("--kind", choices=["systemd", "launchd"], required=True)
    parser.add_argument("--repo-root", required=True, help="Working directory used by the scheduled process")
    parser.add_argument("--python-exe", required=True)
    parser.add_argument("--config", default=resource_argument("configs/pipeline.standalone.yaml"))
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    working_directory = _absolute_existing(args.repo_root)
    python_exe = _absolute_existing(args.python_exe, file=True)
    requested_config = Path(args.config).expanduser()
    if requested_config.is_absolute():
        config = requested_config.resolve()
    else:
        local_config = (working_directory / requested_config).resolve()
        config = local_config if local_config.is_file() else resource_path(requested_config).resolve()
    if not config.is_file():
        raise FileNotFoundError(config)
    paths = render(
        args.kind,
        working_directory,
        python_exe,
        config,
        Path(args.output_dir).expanduser().resolve(),
    )
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
