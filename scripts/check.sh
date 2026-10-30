#!/usr/bin/env bash
# Run every check that CI's verify job runs, in the same order.
# Usage: scripts/check.sh
# Interpreter: $PYTHON if set, else .venv/bin/python if present, else python3.
# Needs gitleaks on PATH for the secret scan.
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

scan() {
  echo "==> gitleaks git $*" >&2
  command -v gitleaks > /dev/null || {
    echo "gitleaks not found; install it (brew install gitleaks)" >&2
    exit 1
  }
  gitleaks git --config .gitleaks.toml --redact --verbose --no-banner --log-level warn "$@" .
}

# Committed history, then staged and unstaged edits, so a secret fails here
# before it is committed or pushed. Untracked files such as .env are skipped.
scan
scan --pre-commit --staged
scan --pre-commit
run compileall -q src tests scripts
run ruff check src tests scripts
run ruff format --check src tests scripts
run mypy src tests scripts
run unittest discover -s tests -t .
run media_qc_agent.cli.demo
run media_qc_agent.cli.evaluate --mask-version-labels
run media_qc_agent.cli.compare --expect evals/results/policy-comparison-v1.json > /dev/null
run media_qc_agent.cli.baseline --expect evals/results/baseline-comparison-v1.json > /dev/null
echo "All checks passed." >&2
