"""Fail on SQL that uses positional `?` parameters instead of named ones.

Recognises strings passed to `execute` or `executemany`, and any string that
starts with an uppercase SQL keyword. SQL built up or held in another form,
such as lowercase text in a variable, is not recognised.
"""

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOTS = ("src", "tests", "scripts")
EXECUTE_METHODS = {"execute", "executemany"}
SQL_START = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|REPLACE|WITH)\b")
SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _text(node: ast.AST) -> str | None:
    """Return a string's text, with any f-string fields left out."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = (_text(value) for value in node.values)
        return "".join(part for part in parts if part is not None)
    return None


def _executed(tree: ast.AST) -> Iterator[ast.AST]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in EXECUTE_METHODS
            and node.args
        ):
            yield node.args[0]


def _is_positional(sql: str) -> bool:
    return "?" in SQL_LITERAL.sub("", sql)


def positional_queries(source: str) -> list[int]:
    """Return the line of each query in `source` that uses a `?` parameter."""

    tree = ast.parse(source)
    executed = {id(node) for node in _executed(tree)}
    lines = []
    for node in ast.walk(tree):
        text = _text(node)
        if text is None or not (id(node) in executed or SQL_START.match(text)):
            continue
        if _is_positional(text):
            lines.append(node.lineno)  # type: ignore[attr-defined]
    return sorted(set(lines))


def main() -> int:
    failures = [
        f"{path}:{line}: use a named parameter (:name) instead of ?"
        for root in ROOTS
        for path in sorted(Path(root).rglob("*.py"))
        for line in positional_queries(path.read_text())
    ]
    for failure in failures:
        print(failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
