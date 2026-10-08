# Diagrams

Visual maps of the system, drawn from the code as of the #56 cleanup. Mermaid
renders on GitHub.

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
    cli["cli<br/>serve, worker, demo, evaluate, compare, baseline"]

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
    ready --> submitting: reserve_submission<br/>(worker, atomic)
    ready --> succeeded: record_caption_repair<br/>(local, no provider)

    submitting --> submitted: record_submission
    submitting --> submitting: crash → lease expires →<br/>recover with the same key

    submitted --> succeeded: record_completion<br/>(callback applied)
    submitted --> awaiting_approval: request_retry<br/>(new version, new key)
    succeeded --> awaiting_approval: request_retry
    succeeded --> [*]
```

Every arrow except the worker's appends or checks a plan version, and every
reviewer command must name the version it was decided against
(`expected_plan_version_id`), so a stale page gets a 409 instead of acting on a
plan the reviewer never saw.

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
    W->>DB: claim lease (owner, attempt, expires_at)
    W->>DB: reserve_submission · status=submitting
    Note over DB: from here, edits are blocked<br/>and the key is fixed
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

    W1->>DB: reserve · status=submitting
    W1->>P: submit(key K)
    P-->>W1: job J
    Note over W1: process dies before recording J
    Note over DB: run stays submitting,<br/>lease expires after 30 s
    W2->>DB: claim expired lease (attempt 2)
    W2->>P: submit(key K)
    P-->>W2: job J (same job, deduplicated by K)
    W2->>DB: record_submission(J) · status=submitted
```
