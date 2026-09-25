# ADR 0014: Persist artifact-bound quality evidence

## Status

Accepted.

## Context

The repair planner received a classified finding, but the workflow stored only
its plan. A reviewer could not later see which artifact version had been judged
or what measurements supported the conclusion. Synthetic gates had structured
signals in memory, while manually injected findings could look equally certain.

## Decision

Store a finding for each workflow run and ordered evidence records in the same
transaction. Require an exact observed artifact version and validate its kind
and lineage. Evidence records distinguish facts, inferences, and uncertainty;
measured values and limits are retained where a gate supplies them. An injected
finding without validator evidence receives only inference and uncertainty
records. Keep records tied to their original versions after repair transitions.

## Alternatives considered

- Keep only the latest finding on the workflow run. This would lose the evidence
  and conflate the judged version with a later replacement.
- Store an untyped JSON blob. This would weaken reference integrity and make the
  distinction between measurement and interpretation optional.

## Consequences

The review trail survives process restarts and can be audited by exact artifact
version. The repository performs additional lineage checks and writes more rows
per run. The synthetic measurements remain limited signals; they do not imply
raw-media inspection or perceptual certainty.
