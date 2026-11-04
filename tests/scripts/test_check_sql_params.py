import unittest

from scripts.check_sql_params import positional_queries


class PositionalQueryTests(unittest.TestCase):
    def test_flags_positional_parameters(self) -> None:
        sources = {
            "single line": 'db.execute("SELECT * FROM t WHERE id = ?", (1,))',
            "multiple lines": 'db.execute("""SELECT *\n   FROM t WHERE id = ?""", (1,))',
            "f-string": 'db.execute(f"UPDATE t SET a = ?, {TOUCH} WHERE id = 1", (1,))',
            "executemany": 'db.executemany("INSERT INTO t VALUES (?)", rows)',
            "numbered": 'db.execute("SELECT * FROM t WHERE id = ?1", (1,))',
            "lowercase": 'db.execute("select * from t where id = ?", (1,))',
            "held in a variable": 'query = "SELECT * FROM t WHERE id = ?"',
            "passed to a helper": 'self._row("SELECT * FROM t WHERE id = ?", 1)',
        }
        for name, source in sources.items():
            with self.subTest(name):
                self.assertEqual(positional_queries(source), [1])

    def test_reports_the_line_of_the_query(self) -> None:
        source = 'x = 1\n\ndb.execute(\n    "SELECT * FROM t WHERE id = ?",\n)'
        self.assertEqual(positional_queries(source), [4])

    def test_allows_named_parameters_and_other_text(self) -> None:
        sources = {
            "named": 'db.execute("SELECT * FROM t WHERE id = :id", {"id": 1})',
            "no parameters": 'db.execute("SELECT * FROM t")',
            "question mark in a SQL string": (
                "db.execute(\"SELECT * FROM t WHERE a = 'why?' AND b = :b\", {})"
            ),
            "escaped quote in a SQL string": (
                "db.execute(\"SELECT * FROM t WHERE a = 'it''s ok?'\")"
            ),
            "prose": 'print("Is this a query?")',
            "prose starting with a word": 'print("Update the row?")',
            "docstring": 'def f():\n    """Should this run?"""',
        }
        for name, source in sources.items():
            with self.subTest(name):
                self.assertEqual(positional_queries(source), [])


if __name__ == "__main__":
    unittest.main()
