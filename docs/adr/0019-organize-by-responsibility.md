# 0019: Organize source, tests, and documentation by responsibility

Status: Accepted

## Context

A flat package mixed domain policy, quality checks, provider adapters, SQLite
transitions, and executable demos. Readers had to inspect imports to understand
which files belonged together. The flat documentation directory mixed guarantees,
check methods, model experiments, and development policy.

## Decision

Use five source packages: `domain`, `quality`, `workflow`, `agent`, and `cli`.
Mirror those responsibilities in the test directories, with explicit package
markers and test discovery from the repository root. Group documentation into
`architecture`, `quality`, `agent`, and `development`, and retain `adr` as the
decision history. Add a documentation guide with an ownership and dependency map.

Domain code stays independent of model adapters, quality checks, workflow state,
and executable commands. Quality and agent code consume domain definitions.
Workflow owns persistence and execution and can use domain and quality code.
CLI code assembles the parts. Package initializers are descriptive; the root
keeps its deliberate public convenience API.

Move modules and update their imports, mock targets, CLI commands, documentation
links, and evaluation dataset path together. Do not add forwarding modules for
the previous internal paths. Current executable commands are:

```bash
PYTHONPATH=src python3 -m media_qc_agent.cli.demo
PYTHONPATH=src python3 -m media_qc_agent.cli.review
PYTHONPATH=src python3 -m media_qc_agent.cli.evaluate --mask-version-labels
```

## Alternatives considered

- Keep the flat package and add a file index: helps navigation but leaves
  ownership implicit in the source tree.
- Introduce separate projects or generic service/repository frameworks: adds
  packaging and coordination overhead before those boundaries are needed.
- Split the SQLite repository during the move: makes it harder to distinguish
  structural changes from changes to transaction semantics.

## Consequences

Internal import and command paths change. Root convenience imports, stored data,
repair policy, and workflow behavior retain their existing contracts. The test
suite must discover every nested package, and executable tests verify that the
new command paths work outside the repository working directory.

The evaluation command remains a source-checkout tool: it finds the repository's
`evals` directory relative to its module location. Installing datasets as package
resources is a separate packaging decision. Recorded evaluation JSON is preserved
as historical experiment output.
