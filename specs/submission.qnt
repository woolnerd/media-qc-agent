// -*- mode: Bluespec; -*-
/// The submission, lease, and completion protocol of one workflow run.
///
/// Workers claim a lease on the approved plan version, call the provider
/// outside any transaction, and record the job only while their lease holds.
/// Workers may crash, time out, or outlive their lease; completions may be
/// stale or duplicated; a retry supersedes the job with a new plan version.
///
/// Mirrors `workflow/worker_queue.py`, `workflow/worker.py`, `workflow/executor.py`,
/// and the submission and completion methods of `workflow/repository.py`.
/// Idempotency keys are plan versions; job ids are `version * 10 + n`.
module submission {
  /// The provider returns the existing job for a repeated idempotency key.
  const PROVIDER_DEDUPES: bool
  const WORKERS: Set[str]
  const MAX_ATTEMPTS: int
  const MAX_VERSIONS: int
  const LEASE_TICKS: int

  /// `idle`, `claimed` (lease held, provider not yet answered), or
  /// `accepted` (provider returned `job`, not yet recorded locally).
  type Claim = { phase: str, version: int, attempt: int, job: int }

  type State = {
    now: int,
    status: str,
    version: int,
    approved: Set[int],
    attempts: int -> int,
    stopped: Set[int],
    leaseOwner: int -> str,
    leaseUntil: int -> int,
    workers: str -> Claim,
    jobsCreated: int -> int,
    recorded: Set[int],
    runJob: int,
    activeVideo: int,
    promoted: Set[int],
    promotions: int,
    // Ghost: commits made by a worker whose lease had expired or moved on.
    // Written inline in `record`, apart from `leaseHeld`, so a weakened
    // guard or a weakened `leaseHeld` still shows up here.
    unleasedCommits: int,
  }

  val IDLE: Claim = { phase: "idle", version: 0, attempt: 0, job: 0 }
  val VERSIONS: Set[int] = 1.to(MAX_VERSIONS)

  def versionOf(job: int): int = job / 10

  // Guards and transitions; each transition is a pure function of the state.

  def leaseFree(s: State, v: int): bool =
    s.leaseOwner.get(v) == "" or s.leaseUntil.get(v) <= s.now

  def leaseHeld(s: State, w: str): bool = {
    val c = s.workers.get(w)
    and {
      s.leaseOwner.get(c.version) == w,
      s.attempts.get(c.version) == c.attempt,
      s.leaseUntil.get(c.version) > s.now,
    }
  }

  def release(s: State, v: int): State =
    { ...s, leaseOwner: s.leaseOwner.set(v, ""), leaseUntil: s.leaseUntil.set(v, 0) }

  def idle(s: State, w: str): State = { ...s, workers: s.workers.set(w, IDLE) }

  def canClaim(s: State, w: str): bool = and {
    s.workers.get(w).phase == "idle",
    Set("ready", "submitting").contains(s.status),
    s.approved.contains(s.version),
    not(s.stopped.contains(s.version)),
    s.attempts.get(s.version) < MAX_ATTEMPTS,
    leaseFree(s, s.version),
  }

  def claim(s: State, w: str): State = {
    val v = s.version
    val attempt = s.attempts.get(v) + 1
    { ...s,
      status: "submitting",
      attempts: s.attempts.set(v, attempt),
      leaseOwner: s.leaseOwner.set(v, w),
      leaseUntil: s.leaseUntil.set(v, s.now + LEASE_TICKS),
      workers: s.workers.set(w, { phase: "claimed", version: v, attempt: attempt, job: 0 }),
    }
  }

  /// The provider call starts only after the lease and current version are rechecked.
  def canCallProvider(s: State, w: str): bool = and {
    s.workers.get(w).phase == "claimed",
    leaseHeld(s, w),
    s.workers.get(w).version == s.version,
    s.status == "submitting",
  }

  def providerAccepts(s: State, v: int): (State, int) = {
    val count = s.jobsCreated.get(v)
    if (PROVIDER_DEDUPES and count > 0) (s, v * 10 + 1)
    else ({ ...s, jobsCreated: s.jobsCreated.set(v, count + 1) }, v * 10 + count + 1)
  }

  def callProvider(s: State, w: str): State = {
    val c = s.workers.get(w)
    val result = providerAccepts(s, c.version)
    { ...result._1, workers: result._1.workers.set(w, { ...c, phase: "accepted", job: result._2 }) }
  }

  /// A timeout leaves acceptance unknown, so the reservation and key are kept.
  def timeout(s: State, w: str, accepted: bool): State = {
    val c = s.workers.get(w)
    val afterCall = if (accepted) providerAccepts(s, c.version)._1 else s
    val failed = release(afterCall, c.version)
    val stopped = if (c.attempt >= MAX_ATTEMPTS) failed.stopped.union(Set(c.version)) else failed.stopped
    idle({ ...failed, stopped: stopped }, w)
  }

  def record(s: State, w: str): State = {
    val c = s.workers.get(w)
    val unleased = or {
      s.leaseOwner.get(c.version) != w,
      s.attempts.get(c.version) != c.attempt,
      s.now >= s.leaseUntil.get(c.version),
    }
    val committed = { ...s,
      status: "submitted",
      runJob: c.job,
      recorded: s.recorded.union(Set(c.job)),
      unleasedCommits: s.unleasedCommits + (if (unleased) 1 else 0),
    }
    if (not(leaseHeld(s, w)) or c.version != s.version) idle(s, w)
    else if (s.status == "submitting") idle(release(committed, c.version), w)
    else if (s.status == "submitted" and s.runJob == c.job) idle(release(s, c.version), w)
    // Another job is already recorded for this version: a non-retryable failure.
    else idle({ ...release(s, c.version), stopped: s.stopped.union(Set(c.version)) }, w)
  }

  def complete(s: State, job: int): State =
    if (s.runJob == job and s.status == "submitted")
      { ...s,
        status: "succeeded",
        activeVideo: job,
        promoted: s.promoted.union(Set(job)),
        promotions: s.promotions + 1,
      }
    else s

  def canRetry(s: State): bool = and {
    Set("submitted", "succeeded").contains(s.status),
    s.runJob != 0,
    s.version < MAX_VERSIONS,
  }

  def retry(s: State): State =
    { ...s, status: "awaiting_approval", version: s.version + 1, runJob: 0 }

  def approve(s: State): State =
    { ...s, status: "ready", approved: s.approved.union(Set(s.version)) }

  val initial: State = {
    now: 0,
    status: "ready",
    version: 1,
    approved: Set(1),
    attempts: VERSIONS.mapBy(v => 0),
    stopped: Set(),
    leaseOwner: VERSIONS.mapBy(v => ""),
    leaseUntil: VERSIONS.mapBy(v => 0),
    workers: WORKERS.mapBy(w => IDLE),
    jobsCreated: VERSIONS.mapBy(v => 0),
    recorded: Set(),
    runJob: 0,
    activeVideo: 0,
    promoted: Set(),
    promotions: 0,
    unleasedCommits: 0,
  }

  var s: State

  action init = s' = initial

  action workerStep(w: str, accepted: bool): bool = any {
    all { canClaim(s, w), s' = claim(s, w) },
    all { canCallProvider(s, w), s' = callProvider(s, w) },
    all { canCallProvider(s, w), s' = timeout(s, w, accepted) },
    all { s.workers.get(w).phase == "accepted", s' = record(s, w) },
    // A crash or lost lease drops local progress; the lease runs out on its own.
    all { s.workers.get(w).phase != "idle", s' = idle(s, w) },
  }

  action completeOne = {
    nondet job = s.recorded.union(Set(0)).oneOf()
    all { job != 0, s' = complete(s, job) }
  }

  action step = {
    nondet w = WORKERS.oneOf()
    nondet accepted = Bool.oneOf()
    any {
      workerStep(w, accepted),
      all { s' = { ...s, now: s.now + 1 } },
      completeOne,
      all { canRetry(s), s' = retry(s) },
      all { s.status == "awaiting_approval", s' = approve(s) },
    }
  }

  // Invariants, named after the README guarantees they check.

  /// INV-03: the provider only ever sees approved plan versions.
  val providerSeesOnlyApproved: bool =
    VERSIONS.forall(v => s.jobsCreated.get(v) > 0 implies s.approved.contains(v))

  /// INV-04 and INV-08: recovery reuses the key, so a version creates one provider job.
  /// Not part of `safety`: a deduplicating provider makes this hold by itself.
  /// It is checked as a witness against `nondeduplicating`, where an overlap
  /// must break it.
  val oneJobPerKey: bool = VERSIONS.forall(v => s.jobsCreated.get(v) <= 1)

  /// INV-04: once a worker has claimed a version, only an explicit retry of its
  /// recorded job supersedes it, never recovery from an uncertain outcome.
  /// Edits before any claim also make new versions; the model leaves them out.
  val recoveryKeepsItsVersion: bool =
    VERSIONS.forall(v => (v < s.version and s.attempts.get(v) > 0)
      implies s.recorded.exists(j => versionOf(j) == v))

  /// INV-08: claims are bounded by the attempt budget.
  val attemptsBounded: bool = VERSIONS.forall(v => s.attempts.get(v) <= MAX_ATTEMPTS)

  /// INV-05: only the current job is promoted, and each job at most once.
  val onlyCurrentJobPromoted: bool = and {
    s.status == "succeeded" implies (s.activeVideo == s.runJob and s.runJob != 0),
    s.runJob != 0 implies versionOf(s.runJob) == s.version,
    s.promotions == s.promoted.size(),
    s.promoted.subseteq(s.recorded),
  }

  /// INV-08: a job is recorded only by the worker whose lease still holds.
  val commitsHoldTheLease: bool = s.unleasedCommits == 0

  val safety: bool = and {
    providerSeesOnlyApproved,
    recoveryKeepsItsVersion,
    attemptsBounded,
    onlyCurrentJobPromoted,
    commitsHoldTheLease,
  }

  // Witnesses: checks expect the simulator to violate these, which shows the
  // model reaches the states the invariants are about.

  /// Violated once a retried version is submitted and completes.
  val retriedRunNeverSucceeds: bool = not(s.status == "succeeded" and s.version > 1)

  /// Violated once a worker holds a provider answer after its lease expired or
  /// moved on, the state in which `record` must refuse to commit.
  val answersKeepTheirLease: bool =
    WORKERS.forall(w => s.workers.get(w).phase == "accepted" implies leaseHeld(s, w))

  /// Violated once two workers hold the same version's provider answer at once.
  val noOverlappingCalls: bool =
    WORKERS.forall(a => WORKERS.forall(b => or {
      a == b,
      s.workers.get(a).phase != "accepted",
      s.workers.get(b).phase != "accepted",
      s.workers.get(a).version != s.workers.get(b).version,
    }))
}

/// The real provider contract: a repeated key returns the existing job.
module deduplicating {
  import submission(
    PROVIDER_DEDUPES = true,
    WORKERS = Set("worker-a", "worker-b"),
    MAX_ATTEMPTS = 3,
    MAX_VERSIONS = 2,
    LEASE_TICKS = 2,
  ).*
}

/// A provider that ignores keys: an overlapping recovery creates a second job.
module nondeduplicating {
  import submission(
    PROVIDER_DEDUPES = false,
    WORKERS = Set("worker-a", "worker-b"),
    MAX_ATTEMPTS = 3,
    MAX_VERSIONS = 2,
    LEASE_TICKS = 2,
  ).*
}
