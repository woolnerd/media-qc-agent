# Versioned quality findings and evidence

Every workflow run now stores one quality finding and its evidence in the same
SQLite transaction as the proposed repair. The finding records the exact
artifact version being evaluated. Each evidence record has its own exact
artifact-version reference, a statement, and one of three roles:

- **Fact:** a measurement or declared value supplied by a deterministic gate.
- **Inference:** the conclusion drawn from those facts.
- **Uncertainty:** a known limit on what the gate has established.

The environment gate records the script phrase and declared avatar environment
as facts, the mismatch as an inference, and the absence of pixel inspection as
uncertainty. The visual gate records each above-threshold motion jump against
the observed video version, then the jerky-motion inference and its perceptual
review limit. The caption gate checks cues already stored on an observed caption
version and records each violated rule with its observed value and limit.

Tests and callers may also inject a finding directly. They must supply an exact
observed artifact version for visual and caption findings. When they supply no
validator evidence, the repository stores the explanation as an **inference**
and explicitly records that independent validator evidence was not supplied.
It never promotes that explanation to a measured fact.

The observed artifact must have the kind expected for the failure class and
belong to the selected source lineage. Evidence may cite only those source
versions, the observed version, or its active video. The database uses foreign
keys to keep all references valid. A later repair may change the run's active
artifact pointers; existing findings and evidence still refer to the versions
that were evaluated.

This slice retains structured evidence and lineage. It does not inspect raw
media or prove that a declared scene matches avatar pixels.
