#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
repo_python="${repo_root}/.venv/bin/python"
if [[ ! -x "${repo_python}" ]]; then
  echo "Repository-local interpreter is missing: ${repo_python}" >&2
  exit 2
fi
exec "${repo_python}" "${repo_root}/examples/local_qlib_backtest/run_backtest.py" "$@"
