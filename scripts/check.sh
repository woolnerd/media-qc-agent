#!/usr/bin/env bash
# Run every check that CI's verify job runs, in the same order.
# Usage: scripts/check.sh
# Interpreter: $PYTHON if set, else .venv/bin/python if present, else python3.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

if [[ -n "${PYTHON:-}" ]]; then
  py=$PYTHON
elif [[ -x .venv/bin/python ]]; then
  py=.venv/bin/python
else
  py=python3
fi

run() {
  echo "==> $py -m $*" >&2
  "$py" -m "$@"
}

run compileall -q src tests
run ruff check src tests
run ruff format --check src tests
run mypy src tests
run unittest discover -s tests -t .
run media_qc_agent.cli.demo
run media_qc_agent.cli.evaluate --mask-version-labels
run media_qc_agent.cli.compare --expect evals/results/policy-comparison-v1.json > /dev/null
run media_qc_agent.cli.baseline --expect evals/results/baseline-comparison-v1.json > /dev/null
echo "All checks passed." >&2
