# Media QC Agent — Project Plan

## 1. Project thesis

AI-media production pipelines often discover quality failures only after an
expensive artifact has been generated. Media QC Agent is a bounded quality
supervisor that should detect failures at the earliest useful stage, explain
the supporting evidence, propose the smallest safe repair, obtain human input
where judgment is required, and execute approved work without duplicating
provider jobs.

The central engineering hypothesis is that model judgment is most useful when
it is constrained by deterministic workflow policy. A model may diagnose and
recommend; application code owns artifact lineage, state transitions,
approvals, invalidation, retries, and side effects.

## 2. Project context and provenance

This is a focused portfolio implementation inspired by firsthand experience
with AI-media production workflows. It is intended to explore production-style
AI engineering, backend design, evaluation, observability, and selected
distributed-systems problems through a credible domain workflow.

The implementation is clean-room. It may use general workflow knowledge, but
must not reuse client code, prompts, credentials, database exports, private
assets, branding, screenshots, or confidential requirements. All people,
scripts, media, review feedback, and provider behavior must be synthetic.

## 3. Originating workflow and failure pattern

A representative workflow contains three broad stages:

1. Create versioned scripts, avatar looks, and voice profiles.
2. Select approved inputs and launch independent video-generation jobs.
3. Review completed videos, record findings, and approve or reject repairs.

Observed failure classes include poor scripts discovered only after rendering,
scripts that conflict with an avatar's environment, provider-specific TTS input
incompatibilities—for example, a speech model reading `450*F` as “four hundred
fifty star F” instead of “four hundred fifty degrees Fahrenheit”—and jerky or
visibly unnatural video.

The shared failure is late discovery. The system should move checks earlier
when possible and avoid invalidating artifacts that remain valid.

## 4. Job to be done

> When an AI-generated media asset fails a quality check, a media producer needs
> to identify the actual failure, determine which exact artifact versions are
> affected, and approve the least expensive safe repair before another provider
> job is launched.

For each finding, the system should answer:

1. What failed, and what evidence supports that diagnosis?
2. At which pipeline stage should it have been caught?
3. Which artifact versions are affected?
4. What is the minimum sufficient repair?
5. Does the repair require clarification or approval?
6. Was approved work executed exactly once from the user's perspective?
7. Did the resulting artifact address the original finding?

## 5. Product boundary

### In scope

- Versioned scripts, derived provider-facing TTS inputs, avatars, voices, videos,
  and captions.
- Pre-render script-quality, provider-specific TTS-input compatibility, and
  script/environment compatibility checks.
- Post-render speech, caption, and visual-quality checks.
- Free-text reviewer feedback converted into structured findings.
- Evidence-backed, versioned repair proposals.
- Focused human clarification and approval.
- Asynchronous provider jobs, callbacks, retries, and reconciliation.
- Durable workflow state and restart recovery.
- Synthetic scenarios, automated evaluations, and trace inspection.

### Out of scope

- Training avatar or voice models.
- Building a general-purpose media editor.
- Reproducing a former client workflow in full.
- Real customer data, client-specific providers, or real Slack integration.
- Multi-tenant billing, enterprise identity, or production-scale infrastructure.
- Autonomous publication without human accountability.

## 6. Inputs and evidence

The system is not intended to accept only a textual description of a problem.
Its evidence may include:

- script and caption text;
- structured avatar and environment metadata;
- synthetic images, video, and derived visual-quality signals;
- free-text human review feedback;
- automated validator findings; and
- artifact versions, dependency lineage, and workflow history.

Early slices may use deterministic fixtures or derived signals in place of raw
media analysis. Direct image or video evaluation should be added only when a
specific failure class and evaluation method justify it. Voice quality is not
an initial failure class.

## 7. Failure taxonomy and minimum-repair policy

| Failure | Earliest useful gate | Minimum repair | Preserve |
| --- | --- | --- | --- |
| Poor script quality | Before rendering | Revise and approve script, then regenerate descendants | Avatar and voice |
| Script/environment mismatch | Before rendering | Ask whether to change script or avatar, then regenerate descendants | Unselected branch |
| TTS input incompatibility or incorrect speech rendering | Before rendering | Normalize and validate provider-facing text for the selected model, then regenerate descendants if already rendered | Authored script intent, avatar, and voice profile |
| Caption formatting defect | After rendering | Repair captions without regenerating acceptable video | Script, avatar, voice, and video |
| Jerky or unnatural video | After rendering | Retry video, then regenerate dependent captions | Approved script, avatar, and voice |

The policy must prohibit unnecessary regeneration. Creative intent that cannot
be inferred safely remains unresolved until a human clarifies it.

`requires_repair_input` means the plan needs a replacement script, provider-
facing TTS input, or avatar before approval. A visual-quality finding needs no
such input when it can reuse approved inputs, but the retry still requires
human approval. The current slice accepts a preclassified finding; the visual
evidence and threshold that would produce one automatically remain an open
evaluation question.

## 8. Agent and application responsibilities

The agent operates a bounded observe–decide–act loop:

```text
review feedback and automated findings
                 ↓
inspect exact artifacts, lineage, history, and evidence
                 ↓
classify the failure and identify uncertainty
                 ↓
ask for clarification or propose a repair plan
                 ↓
wait for required human approval
                 ↓
invoke deterministic workflow tools
                 ↓
observe asynchronous results and re-evaluate
```

The agent may interpret ambiguous feedback, combine evidence, distinguish facts
from assumptions, request clarification, propose a minimal repair, and assess a
replacement artifact.

When creative direction is unresolved, the workflow stores a clarification
request with explicit options. A human selection creates a concrete repair
plan and moves the run to `needs_repair_input`. It cannot be approved until the
replacement script or avatar version is bound to that plan. The current slice
models the choice and invalidation scope; binding exact replacement versions
remains part of the versioned-artifact milestone.

Deterministic code must validate structured model output, enforce the artifact
dependency graph, bind approval to immutable plan and artifact versions,
generate stable idempotency keys, manage retries and leases, deduplicate events,
and record execution history.

## 9. Architectural direction

The target is a modular monolith:

```text
Review UI / demo controls
          ↓
Application API
          ↓
Workflow service ─────→ agent planner / evaluators
          ↓                       ↓
Durable database          model-provider adapter
          ↓
Background worker ─────→ media-provider adapters
          ↑
Provider callbacks
```

Likely implementation choices are Python, FastAPI, a database-backed worker,
provider protocols with deterministic fakes, OpenTelemetry-compatible
telemetry, and Langfuse for model traces and evaluations. SQLite is sufficient
while proving semantics in one process; Postgres or a workflow framework should
be introduced only when a demonstrated concurrency requirement justifies it.

Microservices, Kafka, and Kubernetes are not default milestones.

## 10. Core domain concepts

Media and lineage:

- `Project`, `Persona`
- `ScriptVersion`, `TtsInputVersion`, `AvatarVersion`, `VoiceVersion`
- `VideoVersion`, `CaptionVersion`
- `ArtifactDependency`

Quality and review:

- `QualityCheck`, `QualityFinding`, `Evidence`
- `ReviewDecision`, `ClarificationRequest`
- `RepairPlan`, `RepairPlanVersion`, `Approval`

Durable execution:

- `WorkflowRun`, `WorkflowStep`, `StepAttempt`
- `ProviderJob`, `ProviderEvent`, `IdempotencyKey`
- `ExecutionEvent`

Outputs reference the exact input versions that produced them. Artifact and
repair-plan versions are immutable; changing a plan invalidates its previous
approval.

## 11. Workflow semantics

The intended state progression is:

```text
needs_input (only when a human choice is unresolved)
      ↓
needs_repair_input (when a repair needs a replacement artifact)
      ↓
awaiting_approval
      ↓
ready
      ↓
submitting → submitted → running → succeeded
                  ↘ failed_retryable → ready
                  ↘ failed_terminal → needs_attention
```

External timeouts are uncertain outcomes, not proof of failure. Reconciliation
must query or retry using the same idempotency key. Duplicate callbacks are
no-ops, out-of-order callbacks cannot promote obsolete artifacts, and stale
workers cannot advance work after losing a lease.

These semantics matter more than distributing the system across services.

The current provider path implements `awaiting_approval → ready → submitting →
submitted → succeeded`. The `ready → submitting` transition atomically reserves
the approved plan version and idempotency key before the external call. Edits
and input rebinding are blocked while submission is unresolved. A crash or
ambiguous provider error leaves the run `submitting`; recovery uses the same
key, relying on provider deduplication. A new retry is permitted only from
`submitted` or `succeeded` and requires a new plan version and approval. Local
caption repair goes directly from `ready` to `succeeded` in one transaction.

`running`, classified failure states, retry limits, and worker leases remain
planned work. The reservation protects plan scope but does not stop concurrent
workers from calling the provider with the same key. See
[ADR 0018](docs/adr/0018-reserve-approved-plans-before-provider-submission.md).

## 12. Evaluation strategy

A synthetic evaluation case should identify its input artifacts and versions,
review feedback or automated signal, expected failure class and evidence,
expected clarification behavior, allowed and forbidden repair actions, and
expected invalidations.

The initial dataset should cover clear, ambiguous, and adversarial examples for
each failure class.

Deterministic evaluations should test dependency invalidation, approval gates,
unnecessary provider calls, duplicate and stale callbacks, crash recovery,
retry limits, and escalation behavior.

Model-behavior evaluations should test classification, evidence grounding,
clarification judgment, minimum repair scope, calibrated uncertainty, and
whether post-repair assessment addresses the original finding. False passes are
at least as important as false blocks.

## 13. Observability strategy

Application telemetry should cover workflow transitions, queue and execution
duration, provider latency and error categories, retry attempts, idempotency-key
hashes, duplicate or stale callbacks, artifact changes, and human wait time.

Model telemetry should capture a trace per diagnosis or planning turn, spans for
retrieval and validation, prompt and model versions, synthetic structured input
and output, evaluation scores, and links back to the relevant workflow and
artifact versions.

Database transitions and provider latency remain application telemetry rather
than being presented as LLM traces.

## 14. Demonstration scenario

The finished demonstration should show four assets moving through one pipeline:

1. A weak script is blocked before video generation.
2. An oven-related script assigned to an office avatar triggers clarification.
3. Provider-facing text containing `450*F` is normalized according to the
   selected TTS model's capabilities before speech and video generation; an
   unsafe fixture is rejected rather than pronounced as “450 star F.”
4. A jerky video is retried across a simulated process interruption, resumes
   with the same idempotency key, tolerates a duplicate callback, and yields one
   replacement version for review.

The review surface should expose artifact versions and lineage, findings and
evidence, repair plans and approvals, workflow state, fault-injection controls,
and links to application and model traces.

## 15. Delivery milestones

1. Prove deterministic repair policy and crash-gap recovery.
2. Add versioned artifacts and provider-event semantics.
3. Add deterministic quality gates and evidence records.
4. Add model-assisted interpretation, clarification, and versioned planning.
5. Add an API, durable worker, and review surface.
6. Add application observability and model-behavior evaluations.
7. Polish synthetic assets, documentation, and the demonstration.

Milestones describe durable outcomes rather than the current task list. Active
work and acceptance criteria belong in [GitHub issues](https://github.com/woolnerd/media-qc-agent/issues).

## 16. Success criteria

The project is ready to present when a reviewer can see that:

- the agent addresses a domain-specific workflow rather than generic chat;
- model judgment is constrained by deterministic policy;
- artifacts, evidence, plans, and approvals are versioned and auditable;
- asynchronous failures and retries are handled safely;
- interrupted work resumes without duplicate provider cost;
- system and model behavior are evaluated separately;
- traces make a bad decision diagnosable; and
- tradeoffs and limitations are explained without overstating scale or prior
  ownership.

## 17. Open design questions

These questions should be resolved through implementation evidence and recorded
in architectural decision records:

- What rubric makes poor script quality consistent enough to evaluate?
- What metadata is sufficient for script/environment compatibility before
  image analysis adds value?
- Which visual failures can be detected deterministically, and which require a
  multimodal model or human review?
- Which repair plans require approval: all plans, only expensive changes, or
  only creative decisions?
- At what point does SQLite stop supporting the required concurrency behavior?
- Which real model and observability provider best support a low-cost demo
  without becoming the product story?
