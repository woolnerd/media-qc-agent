# Diagrams

Visual maps of the system. Mermaid renders on GitHub.

## 1. UML class diagram: core models

Frozen dataclasses; every one is immutable.

```mermaid
classDiagram
    direction LR

    class WorkflowRun {
        id
        status: WorkflowStatus
        sources: VideoSources
        active_video_version_id
        active_caption_version_id
        external_job_id
        clarification: ClarificationRequest?
        plan_version_id()
    }
    class RepairPlanVersion {
        id
        run_id
        revision: int
        idempotency_key?
        target_video_version_id?
        target_caption_version_id?
        observed_artifact_version_id
        replacement_choices
    }
    class RepairPlan {
        action: RepairAction
        invalidates: set~ArtifactKind~
        requires_repair_input: bool
        rationale
    }
    class ClarificationRequest {
        question
    }
    class RepairOption {
        action: RepairAction
        invalidates
        rationale
    }
    class PlanApproval {
        plan_version_id
        created_at
    }
    class VideoSources {
        script_version_id
        tts_input_version_id
        avatar_version_id
        voice_version_id
        dependencies()
    }
    class ArtifactVersion {
        id
        kind: ArtifactKind
        source_versions
        external_job_id?
    }
    class ProviderJob {
        external_job_id
        run_id
        plan_version_id
        idempotency_key
        action
    }
    class ProviderEvent {
        external_event_id
        external_job_id
        disposition: ProviderEventDisposition
    }
    class QualityFinding {
        kind: FailureKind
        explanation
        confidence: 0..1
    }

    WorkflowRun --> "0..1" RepairPlanVersion : current plan_version
    WorkflowRun --> "0..1" PlanApproval : approval
    WorkflowRun --> "0..1" ClarificationRequest
    WorkflowRun --> VideoSources : observed sources
    RepairPlanVersion *-- RepairPlan : plan
    RepairPlanVersion *-- VideoSources : sources
    ClarificationRequest *-- "2..*" RepairOption
    PlanApproval ..> RepairPlanVersion : names exactly one
    ProviderJob ..> RepairPlanVersion : submitted for
    ProviderEvent --> ProviderJob
    VideoSources ..> ArtifactVersion : 4 version ids
```

The planner is the pure function between findings and plans:

```mermaid
flowchart LR
    F[QualityFinding] --> P{"plan_repair()"}
    P -->|one safe repair| RP[RepairPlan]
    P -->|creative choice needed| CR[ClarificationRequest]
    CR -->|"select_repair(action)"| RP
```

## 2. Database tables (ER)

```mermaid
erDiagram
    workflow_runs ||--o| repair_plan_versions : "points at current"
    workflow_runs ||--|{ repair_plan_versions : "has history of"
    repair_plan_versions ||--o| plan_approvals : "approved by"
    workflow_runs ||--|| quality_findings : has
    quality_findings ||--|{ quality_evidence : "cites"
    workflow_runs ||--o{ provider_jobs : submits
    repair_plan_versions ||--o{ provider_jobs : "submitted as"
    provider_jobs ||--o{ provider_events : receives
    repair_plan_versions ||--o| worker_attempts : "leased via"

    artifact_versions ||--o{ artifact_dependencies : "derived from"
    artifact_versions ||--o| script_versions : "content"
    artifact_versions ||--o| tts_input_versions : "content"
    artifact_versions ||--o| avatar_versions : "content"
    artifact_versions ||--o| caption_contents : "content"
    workflow_runs }o--|| artifact_versions : "observed + active"
    quality_evidence }o--|| artifact_versions : "about"

    workflow_runs {
        text id PK
        text status
        text plan_version_id FK
        text active_video_version_id FK
        text active_caption_version_id FK
        text external_job_id
    }
    repair_plan_versions {
        text id PK
        text run_id FK
        int revision
        json snapshot "immutable (trigger)"
    }
    plan_approvals {
        text plan_version_id PK "immutable (trigger)"
    }
    provider_jobs {
        text external_job_id PK
        text idempotency_key UK
        text plan_version_id FK
        text traceparent
    }
    provider_events {
        text external_event_id PK
        text disposition "applied|stale|redundant|rejected"
    }
    worker_attempts {
        text plan_version_id PK
        int attempts
        text lease_owner
        real lease_until
        real next_attempt_at
        int stopped
    }
    artifact_versions {
        text id PK
        text kind
        text external_job_id UK
    }
```

`worker_policy` (one row: capacity, attempts, lease, backoff) is omitted.

## 3. Packages: who imports whom

Arrows point at the dependency. Lower layers never import upper ones.

```mermaid
flowchart BT
    domain["domain<br/>models, planner, evidence, ids<br/><i>pure rules, no I/O</i>"]
    quality["quality<br/>captions, environment,<br/>spoken_text, visual_quality<br/><i>deterministic gates</i>"]
    agent["agent<br/>interpretation, openrouter, jev<br/><i>model proposes, policy validates</i>"]
    workflow["workflow<br/>repository, artifacts, schema,<br/>executor, worker, queue, provider<br/><i>durable state + side effects</i>"]
    api["api<br/>FastAPI JSON + review HTML"]
    cli["cli<br/>serve, worker, review, demo,<br/>evaluate, compare, baseline"]

    quality --> domain
    agent --> domain
    workflow --> domain
    workflow --> quality
    api --> workflow
    api --> quality
    api --> domain
    cli --> api
    cli --> workflow
    cli --> agent
    cli --> quality
    cli --> domain
```

Note that `workflow` never imports `agent`: the model has no path to durable
state.

## 4. Runtime: the processes and stores

```mermaid
flowchart TB
    Reviewer(("Reviewer<br/>browser")) --> API
    Client(("JSON client")) --> API

    subgraph serve["cli.serve process"]
        API["FastAPI<br/>review pages + /runs API"] --> Repo1["WorkflowRepository"]
    end

    subgraph worker["cli.worker process"]
        Loop["Worker.run_once"] --> Queue["SubmissionQueue<br/>leases, capacity, backoff"]
        Loop --> Exec["WorkflowExecutor"] --> Repo2["WorkflowRepository"]
    end

    Repo1 --> DB[("review.sqlite3<br/>runs, plan versions,<br/>artifacts, jobs, events")]
    Queue --> DB
    Repo2 --> DB
    Exec -- "submit(idempotency_key)" --> Ledger[("*.provider.sqlite3<br/>fake provider ledger,<br/>keyed by idempotency key")]
    Ledger -. "synthetic callback<br/>POST /callbacks/provider" .-> API
```

The two processes share nothing but the SQLite file. The fake provider is a
separate database so it survives a worker crash, like a real external service.

## 5. Artifact lineage

Every artifact is an immutable version. Arrows read "is derived from".

```mermaid
flowchart RL
    Script["script v1"]
    TTS["tts_input v1<br/><i>spoken text for one TTS model</i>"]
    Avatar["avatar v1"]
    Voice["voice v1"]
    Video["video v1<br/><i>from a provider job</i>"]
    Captions["captions v1"]

    TTS --> Script
    Video --> Script
    Video --> TTS
    Video --> Avatar
    Video --> Voice
    Captions --> Video
```

A repair replaces one node and invalidates everything to its left that depends
on it. Everything else is kept.

```mermaid
flowchart LR
    subgraph s1["Jerky video → regenerate_video"]
        direction RL
        a1[script]:::keep
        b1[tts_input]:::keep
        c1[avatar]:::keep
        d1[voice]:::keep
        e1[video]:::redo
        f1[captions]:::redo
        e1 --> a1 & b1 & c1 & d1
        f1 --> e1
    end
    subgraph s2["Bad captions → repair_captions"]
        direction RL
        a2[script]:::keep
        b2[tts_input]:::keep
        c2[avatar]:::keep
        d2[voice]:::keep
        e2[video]:::keep
        f2[captions]:::redo
        e2 --> a2 & b2 & c2 & d2
        f2 --> e2
    end
    subgraph s3["TTS notation → repair_tts_input"]
        direction RL
        a3[script]:::keep
        b3[tts_input]:::redo
        c3[avatar]:::keep
        d3[voice]:::keep
        e3[video]:::redo
        f3[captions]:::redo
        e3 --> a3 & b3 & c3 & d3
        f3 --> e3
    end
    classDef keep fill:#d9f2d9,stroke:#3a7d3a
    classDef redo fill:#ffd6d6,stroke:#b03030
```

Green is kept, red is replaced. A script revision turns everything except
avatar and voice red; an avatar change turns avatar, video, and captions red.

## 6. Run lifecycle (state machine)

`WorkflowStatus`, with the repository method that causes each transition.

```mermaid
stateDiagram-v2
    [*] --> needs_input: create<br/>(finding needs a creative choice)
    [*] --> needs_repair_input: create<br/>(plan needs a replacement)
    [*] --> awaiting_approval: create<br/>(plan is complete)

    needs_input --> needs_repair_input: select_repair
    needs_repair_input --> awaiting_approval: bind_replacement / bind_tts_input
    awaiting_approval --> ready: approve(plan_version_id)

    ready --> awaiting_approval: revise_plan / bind_tts_input<br/>(new version, approval no longer current)
    ready --> submitting: queue.claim (worker, atomic)<br/>or reserve_submission (no worker)
    ready --> succeeded: record_caption_repair<br/>(local, no provider)

    submitting --> submitted: record_submission
    submitting --> submitting: crash → lease expires →<br/>recover with the same key
    submitting --> submitting: timeout → lease released, backoff →<br/>recover with the same key

    submitted --> succeeded: record_completion<br/>(callback applied)
    submitted --> awaiting_approval: request_retry<br/>(new version, new key)
    succeeded --> awaiting_approval: request_retry
    succeeded --> [*]
```

Every arrow except the worker's appends or checks a plan version, and every
reviewer command must name the version it was decided against
(`expected_plan_version_id`), so a stale page gets a 409 instead of acting on a
plan the reviewer never saw.

A run that runs out of recovery attempts, or hits a non-retryable provider error,
stays in `submitting` with its capacity slot held, because the provider may have
accepted the job. Nothing in the code resolves it yet (#87). A worker that lost
its lease changes nothing and returns `lease_lost`.

## 7. Sequence: approval to replacement video

The happy path, then the crash in the middle of it.

```mermaid
sequenceDiagram
    actor R as Reviewer
    participant A as API
    participant DB as SQLite
    participant W as Worker
    participant P as Provider

    R->>A: approve(plan_version_id)
    A->>DB: BEGIN IMMEDIATE · check version · insert approval · status=ready
    W->>DB: claim lease (owner, attempt, expires_at) · status=submitting
    Note over DB: from here, edits are blocked<br/>and the key is fixed
    W->>DB: reserve_submission · recheck lease and version
    W->>P: submit(key = workflow-run:{run}:plan:{version})
    P-->>W: external_job_id
    W->>DB: record_submission · status=submitted
    P-->>A: callback "completed"
    A->>DB: record_completion · insert video v2 · status=succeeded
    P-->>A: same callback again
    A->>DB: disposition=redundant (no second video)
```

```mermaid
sequenceDiagram
    participant W1 as Worker (first owner)
    participant DB as SQLite
    participant P as Provider
    participant W2 as Worker (recovery)

    W1->>DB: claim lease · status=submitting
    W1->>P: submit(key K)
    P-->>W1: job J
    Note over W1: process dies before recording J
    Note over DB: run stays submitting,<br/>lease expires after 30 s
    W2->>DB: claim expired lease (attempt 2)
    W2->>P: submit(key K)
    P-->>W2: job J (same job, deduplicated by K)
    W2->>DB: record_submission(J) · status=submitted
```

## 8. Verification: which check covers which guarantee

The hand-written tests force chosen orderings. The Hypothesis state machine
runs the real code in random orders; the Quint model samples interleavings of
a simplified protocol. Dashed arrows are witnesses the Quint simulator must
break.

```mermaid
flowchart LR
    subgraph Real code
        H[Hypothesis state machine<br/>test_submission_properties]
    end
    subgraph Model
        Q[Quint model<br/>specs/submission.qnt]
    end
    H --> I3[INV-03 approved version only<br/>editor races: hand-written tests]
    H --> I4[INV-04 one job per key]
    H --> I4R[INV-04 recovery keeps the version]
    H --> I5[INV-05 only current job promoted<br/>with its job's exact inputs]
    H --> I8[INV-08 expired owners cannot commit]
    H --> I9[INV-09 capacity counted<br/>from the provider ledger]
    Q --> I3
    Q --> I4R
    Q --> I5
    Q --> I8
    Q -. must break .-> W1[provider without dedup<br/>creates a second job]
    Q -. must break .-> W2[two workers overlap<br/>on one version]
    Q -. must break .-> W3[a retried run succeeds]
    Q -. must break .-> W4[an answer outlives<br/>its lease]
```

## 9. Evaluation: what runs offline and what gets recorded

Each command replays committed inputs, so CI needs no model and no API key.
`scripts/check.sh` runs all three.

```mermaid
flowchart LR
    subgraph inputs["Committed inputs"]
        AC["agent-cases-v1.json<br/>15 cases with fixture responses"]
        RO["recorded-outputs-v1.json<br/>105 recorded model outputs"]
        BC["baseline-cases-v1.json<br/>19 held-out cases"]
        BR["results/baseline-runs-v1.json<br/>3 recorded Luna runs"]
    end
    subgraph commands["Offline commands"]
        E["cli.evaluate<br/>score each case"]
        C["cli.compare<br/>score under each acceptance policy"]
        B["cli.baseline<br/>score each routing arm"]
    end
    subgraph checks["What fails the build"]
        EP["any case fails"]
        PC["any difference from<br/>results/policy-comparison-v1.json"]
        BP["any difference from<br/>results/baseline-comparison-v1.json"]
    end

    AC --> E --> EP
    AC --> C
    RO --> C --> PC
    BC --> B
    BR --> B --> BP
```

`cli.compare` also fails if the fixtures don't pass under every policy, with or
without a snapshot.

The baseline compares three routing arms. Every arm sees the same evidence.

| Arm | Gate has a finding | Gate is silent |
| --- | --- | --- |
| `rules_only` | Gate decides | Human diagnoses |
| `single_shot` | Model decides, with the gate's evidence as input | Model decides |
| `layered` | Gate decides | Model decides |

If the model abstains or its output is rejected, the case goes to a human.

Recording new outputs is separate, billed, and refuses to start unless
`MEDIA_QC_ALLOW_PAID=1` is set.

```mermaid
flowchart LR
    Gate{{"MEDIA_QC_ALLOW_PAID=1"}} --> EL["cli.evaluate --live<br/>--traces PATH"]
    Gate --> BL["cli.baseline --live RUN_ID"]
    EL --> M(("Model API"))
    BL --> M
    EL --> T[("JSONL trace records<br/>one per case")]
    BL --> BR[("results/baseline-runs-v1.json<br/>run appended")]
```

None of these commands imports `workflow`: evaluations measure routing and
validation, not the worker or the database.

## 10. Observability: events flow out, never back

Workflow events are emitted after their transaction commits. A failing sink
cannot retry provider work or undo a commit (INV-10).

```mermaid
flowchart LR
    W["DurableWorker<br/>claimed, finished"] --> O["ExecutionObserver.emit"]
    X["WorkflowExecutor<br/>provider started, response, error,<br/>submission recorded"] --> O
    R["WorkflowRepository<br/>approved, reserved, attempt requested,<br/>completion recorded, artifact created"] --> O

    O --> Counts["In-process counts<br/>not durable"]
    O --> Sink["sink<br/>cli.worker --events prints JSON to stderr"]
    O --> Metrics["metric_sink (optional)<br/>ExecutionMetrics: bounded labels"]
```

Traces connect a submission to its later callback through the database, because
the two happen far apart in time.

```mermaid
sequenceDiagram
    participant X as WorkflowExecutor
    participant DB as provider_jobs
    participant R as record_completion

    X->>X: provider_span around provider.submit
    X->>DB: save traceparent with the job
    R->>DB: read traceparent for the job
    R->>R: completion_span linked to the saved context
```

Model turns are traced separately (`agent/tracing.py`). Spans hold only
allowlisted, hashed attributes. Full synthetic inputs and outputs go only in the
evaluator's JSONL records, joined to spans by trace ID (ADR 0024). Spans go
nowhere unless an OpenTelemetry SDK is configured at the assembly edge.
