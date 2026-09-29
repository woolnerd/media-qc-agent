# Contributing

## Workflow

- Work on a feature branch and deliver changes through a pull request.
- Use conventional commits, for example
  `feat(workflow): deduplicate provider events`.
- Add tests for every new behavior and bug fix. For a bug, reproduce it with a
  failing test before changing the implementation.
- Favor pure decision functions; keep database, provider, and other I/O at the
  edges.
- Do not commit real customer data, private media, client artifacts, or
  credentials.
- Keep provider-specific behavior behind interfaces.
- Complete the PR template and follow [quality gates](docs/development/quality-gates.md).
  Review affected [architecture invariants](docs/architecture/architecture-invariants.md),
  failure evidence, and tradeoffs before merging.

## Before committing

Run the full test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
```

Install the check tools with `python3 -m pip install -r requirements-dev.txt`,
then run the same checks as CI:

```bash
PYTHONPATH=src python3 -m compileall -q src tests
PYTHONPATH=src python3 -m ruff check src tests
PYTHONPATH=src python3 -m ruff format --check src tests
PYTHONPATH=src python3 -m mypy src tests
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
PYTHONPATH=src python3 -m media_qc_agent.cli.demo
PYTHONPATH=src python3 -m media_qc_agent.cli.evaluate --mask-version-labels
```

The GitHub Actions `verify` job runs on pull requests and pushes to `main`.
Review its result before merging. This private repository's current GitHub plan
does not allow branch protection or rulesets to make the check mandatory.
Ruff also enforces a McCabe cyclomatic complexity limit of 6 per function
through `C901`; split decision logic when a function exceeds it.

## Documentation responsibilities

- Change `PROJECT_PLAN.md` only when the product or architectural direction
  changes.
- Maintain active implementation work and acceptance criteria in GitHub issues.
- Record consequential technical choices in `docs/adr/`.
- `PROJECT_STATE.md` is an opt-in handoff; create or update it only when explicitly
  requested by the user.
