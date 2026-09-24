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

## Before committing

Run the full test suite:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Formatting, linting, and type-checking commands should be added here when those
tools are introduced. All configured checks must pass before a commit.

## Documentation responsibilities

- Change `PROJECT_PLAN.md` only when the product or architectural direction
  changes.
- Maintain active implementation work in GitHub issues; use `BACKLOG.md` only
  until those issues exist.
- Record consequential technical choices in `docs/adr/`.
- Update `PROJECT_STATE.md` at meaningful handoffs rather than after every task.
