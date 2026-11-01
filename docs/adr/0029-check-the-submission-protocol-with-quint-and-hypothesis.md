# ADR 0029: Check the submission protocol with Quint and Hypothesis

Status: Accepted. Adds checks for the guarantees from
[ADR 0018](0018-reserve-approved-plans-before-provider-submission.md) and
[ADR 0021](0021-lease-durable-provider-submissions.md); changes no behavior.
Amended in #76 after a post-merge review found three checks that passed with
their guarantee broken; the decision is unchanged. Amended in #80: Quint is
installed from a lockfile and runs on its TypeScript backend.

## Context

INV-03, INV-04, INV-05, INV-08, and INV-09 depend on how workers, leases,
provider calls, completions, and retries interleave. The existing tests use
barriers to force the orderings their authors thought of. The README's known
limit, a slow provider call that outlives its lease while a recovery submits
the same key, was argued in prose and covered by one hand-built test.

## Decision

Check the protocol two ways, both in `scripts/check.sh`:

- **Hypothesis state machine** (`tests/workflow/test_submission_properties.py`).
  It drives the real worker, repository, and durable fake provider with random
  sequences of claims, timeouts, crashes, expired leases, overlapping
  recoveries, duplicate and stale completions, retries, and approvals. Retry
  and approval are separate steps, so workers run while a version awaits
  approval. It checks the guarantees after every step and shrinks a failure
  to its shortest sequence.
- **Quint model** (`specs/submission.qnt`). It models one run's protocol, and
  the simulator samples interleavings with a fixed seed. The safety invariants
  must hold when the provider deduplicates keys; they include that a version
  is superseded only by an explicit retry of its recorded job, never by
  recovery from an uncertain outcome. A deduplicating provider makes
  `oneJobPerKey` hold by itself, so it is checked only as a witness against
  a provider that ignores keys. Witness properties must be
  broken: a retried run succeeds, a worker holds a provider answer after its
  lease expired, two workers hold provider answers for one version at once,
  and a provider that ignores keys creates a second job.
  The witnesses show that the model reaches the states the invariants
  are about and that deduplication is what keeps the overlap safe.

Both checks were tested by breaking the code on purpose: applying stale
completions, dropping submitting runs from capacity, counting only jobs a run
still points at, letting expired leases commit, ignoring the attempt token,
ignoring the attempt budget, recording a video without its inputs, dropping
the reservation after a timeout, and claiming runs that await approval. Each
change made the state machine fail. The state machine counts capacity from
the provider's ledger: jobs the provider holds with no event, plus reserved
runs whose key it does not hold yet. It requires `outstanding_capacity`,
which also decides admission, to equal that count. A count taken from local
job rows and run status would lose track of an accepted job exactly when the
code does, and certify the undercount. In the model, removing the lease check
from `record` or the expiry from `leaseHeld` breaks `commitsHoldTheLease`, a
ghost count kept apart from both, and turning recovery into a silent new
version breaks `supersededVersionsWereRecorded`.

## Alternatives considered

- **TLA+ with TLC.** It has the same logic and stronger tools, but its notation
  is harder to read for a Python team, and TLC needs Java. Quint compiles to
  the same logic and can use Apalache or TLC later.
- **Exhaustive model checking now (`quint verify`).** It needs Java and
  Apalache in CI. Random simulation finds the known counterexample in
  milliseconds; exhaustive checking can be added when the model grows.
- **Proof tools (Dafny, Lean) or symbolic execution (CrossHair).** They cost
  too much for a SQLite-backed Python codebase, and LLM output cannot be
  proved anyway; INV-01 and INV-07 stay with policy checks and evals.

## Consequences

`scripts/check.sh` needs Node.js 20 or newer. It installs Quint and its
dependencies from `tools/quint/package-lock.json` with `npm ci
--ignore-scripts`, so every package is checked against its lockfile hash, and
it runs the simulator on Quint's TypeScript backend. The default Rust backend
would download an unchecked native binary from GitHub on first use, and that
download failed CI once on GitHub's API rate limit (#77). The TypeScript
backend makes the safety run take about 12 seconds instead of 2. The state
machine adds about ten seconds. Simulation samples interleavings rather
than proving their absence, and the model can drift from the code: a change to
claims, leases, submission, completion, or retry must update both checks. The
model covers one run, so capacity (INV-09) is checked only by the state
machine. Neither check races an editor against a reservation; that part of
INV-03 stays with the hand-written tests.
