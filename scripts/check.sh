#!/usr/bin/env bash
# Run every check that CI's verify job runs, in the same order.
# Usage: scripts/check.sh
# Interpreter: $PYTHON if set, else .venv/bin/python if present, else python3.
# Needs gitleaks on PATH for the secret scan, and Node.js 20+ (npm) for Quint.
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

quint_dir=tools/quint
spec=specs/submission.qnt
# The TypeScript backend runs in Node; the default Rust backend downloads an
# unverified native binary from GitHub on first use.
simulation=(--backend=typescript --max-samples=10000 --max-steps=30 --seed=1)

# Install the Quint pinned by tools/quint/package-lock.json. `npm ci` checks
# every package against its lockfile hash; install scripts never run.
install_quint() {
  local major
  command -v node > /dev/null && command -v npm > /dev/null || {
    echo "node and npm not found; install Node.js 20 or newer" >&2
    exit 1
  }
  major=$(node -p 'process.versions.node.split(".")[0]')
  ((major >= 20)) || {
    echo "Node.js $major found; Quint needs Node.js 20 or newer" >&2
    exit 1
  }
  if ! cmp -s "$quint_dir/package-lock.json" "$quint_dir/node_modules/.installed-lock"; then
    echo "==> npm ci --ignore-scripts (tools/quint)" >&2
    npm ci --prefix "$quint_dir" --ignore-scripts --no-audit --no-fund --loglevel=error || exit 1
    cp "$quint_dir/package-lock.json" "$quint_dir/node_modules/.installed-lock"
  fi
}

quint() {
  echo "==> quint $*" >&2
  "$quint_dir/node_modules/.bin/quint" "$@"
}

# A witness is a property the simulator must break. If it holds, the model
# never reaches the states the safety invariants are about.
witness() {
  local output
  echo "==> witness: the simulator must break $2 in $1" >&2
  if output=$(quint run "$spec" --main="$1" --invariant="$2" "${simulation[@]}" 2>&1); then
    echo "$2 held in $1; the model no longer reaches that state" >&2
    exit 1
  fi
  grep -q '^\[violation\]' <<< "$output" || {
    echo "$output" >&2
    exit 1
  }
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
install_quint
quint typecheck "$spec"
quint run "$spec" --main=deduplicating --invariant=safety "${simulation[@]}"
witness deduplicating retriedRunNeverSucceeds
witness deduplicating noOverlappingCalls
witness deduplicating answersKeepTheirLease
# Without provider deduplication, an overlapping recovery creates a second job.
witness nondeduplicating oneJobPerKey
run media_qc_agent.cli.demo
run media_qc_agent.cli.evaluate --mask-version-labels
run media_qc_agent.cli.compare --expect evals/results/policy-comparison-v1.json > /dev/null
run media_qc_agent.cli.baseline --expect evals/results/baseline-comparison-v1.json > /dev/null
echo "All checks passed." >&2
