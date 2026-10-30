import json
import random
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

from compiler.catalog import Catalog
from compiler.errors import CompilerError
from compiler.pipeline import compile_query
from run import handler_class, create_demo

class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "catalog.json"
        self.catalog = Catalog(self.path)
        self.key = self.catalog.create("User supplied data")
        rows = [{"id": i, "name": f"Person {i}", "age": None if i % 7 == 0 else 17 + i % 10,
                 "city": "Mumbai" if i % 2 else "Pune", "active": bool(i % 2), "department_id": i % 3 + 1}
                for i in range(1, 101)]
        self.catalog.add_json(self.key, "people", rows, indexed=["id", "city", "department_id"])
        self.catalog.add_json(self.key, "departments", [{"id": i, "name": n} for i, n in enumerate(["CSBS", "IT", "Computer"], 1)], indexed=["id"])
        self.tables = self.catalog.get(self.key)["tables"]

    def tearDown(self):
        self.temp.cleanup()

    def query(self, sql, **kwargs):
        return compile_query(self.tables, sql, **kwargs)

    def test_index_is_used_and_executed(self):
        report = self.query("SELECT name FROM people WHERE id = 42;")
        self.assertEqual(report["cost_decisions"][0]["chosen"], "IndexLookup")
        self.assertEqual(report["result"]["rows"], [["Person 42"]])
        self.assertEqual(report["optimized_metrics"]["rows_examined"], 1)
        self.assertEqual(report["baseline_metrics"]["rows_examined"], 100)
        self.assertEqual(report["verification"]["status"], "passed")

    def test_scan_selected_when_lookup_overhead_is_higher(self):
        self.catalog.add_json(self.key, "tiny", [{"id": 1}], indexed=["id"])
        report = self.query("SELECT id FROM tiny WHERE id = 1;")
        self.assertEqual(report["cost_decisions"][0]["chosen"], "SequentialScan")

    def test_empty_result_and_limit_zero(self):
        for sql in ("SELECT name FROM people WHERE 1 = 0;", "SELECT name FROM people LIMIT 0;", "SELECT name FROM people WHERE NULL;"):
            report = self.query(sql)
            self.assertEqual(report["result"]["rows"], [])
            self.assertEqual(report["optimized_metrics"]["rows_examined"], 0)

    def test_constant_fold_duplicates_pruning(self):
        report = self.query("SELECT name FROM people WHERE 10 > 5 AND age >= 18 AND city = 'Mumbai' AND age >= 18;")
        rules = {r["rule"] for r in report["rule_optimizations"]}
        self.assertTrue({"constant_folding", "duplicate_predicate", "projection_pruning"} <= rules)
        self.assertLess(report["optimized_metrics"]["cells_materialized"], report["baseline_metrics"]["cells_materialized"])
        self.assertEqual(report["verification"]["status"], "passed")

    def test_null_logic(self):
        self.assertEqual(self.query("SELECT id FROM people WHERE age = NULL;")["result"]["rows"], [])
        nulls = self.query("SELECT id FROM people WHERE age IS NULL;")["result"]["rows"]
        self.assertEqual(len(nulls), 14)
        self.assertEqual(self.query("SELECT id FROM people WHERE NULL OR TRUE;")["result"]["total_rows"], 100)
        self.assertEqual(self.query("SELECT id FROM people WHERE NOT (age = NULL);")["result"]["rows"], [])

    def test_hash_join_and_pushdown(self):
        sql = "SELECT p.name, d.name FROM people p INNER JOIN departments d ON p.department_id = d.id WHERE p.age >= 20 ORDER BY p.id;"
        report = self.query(sql)
        self.assertIn("HashJoin", [d["chosen"] for d in report["cost_decisions"]])
        self.assertIn("predicate_pushdown", [r["rule"] for r in report["rule_optimizations"]])
        self.assertEqual(report["verification"]["status"], "passed")
        self.assertLess(report["optimized_metrics"]["join_pairs_checked"], report["baseline_metrics"]["join_pairs_checked"])

    def test_non_equality_join_uses_nested_loop(self):
        report = self.query("SELECT d.id, e.id FROM departments d JOIN departments e ON d.id < e.id;")
        self.assertEqual(report["cost_decisions"][-1]["chosen"], "NestedLoopJoin")
        self.assertEqual(report["result"]["rows"], [[1, 2], [1, 3], [2, 3]])

    def test_duplicate_output_columns(self):
        report = self.query("SELECT id, id FROM people WHERE id=1;")
        self.assertEqual(report["result"]["columns"], ["id", "id"])
        self.assertEqual(report["result"]["rows"], [[1, 1]])

    def test_join_limit_preserves_demo_order(self):
        report = self.query("SELECT p.id, d.name FROM people p JOIN departments d ON p.department_id=d.id LIMIT 3;")
        self.assertEqual(report["verification"]["status"], "passed")
        self.assertEqual([r[0] for r in report["result"]["rows"]], [1, 2, 3])

    def test_distinct_order_limit(self):
        report = self.query("SELECT DISTINCT city FROM people ORDER BY city DESC LIMIT 1;")
        self.assertEqual(report["result"]["rows"], [["Pune"]])

    def test_compile_only_and_truncation(self):
        report = self.query("SELECT * FROM people;", execute=False)
        self.assertNotIn("result", report)
        report = self.query("SELECT * FROM people;", result_limit=3)
        self.assertEqual(len(report["result"]["rows"]), 3)
        self.assertEqual(report["result"]["total_rows"], 100)
        self.assertTrue(report["result"]["truncated"])

    def test_errors_are_at_correct_phase(self):
        cases = [("SELECT @ FROM people", "lexical"), ("SELECT FROM people", "syntax"),
                 ("SELECT id FROM missing", "semantic"), ("SELECT salary FROM people", "semantic"),
                 ("SELECT id FROM people WHERE age = 'twenty'", "semantic"),
                 ("SELECT id FROM people p JOIN departments d ON p.department_id = d.id", "semantic"),
                 ("SELECT name FROM people WHERE age", "semantic"),
                 ("SELECT COUNT(*) FROM people", "syntax"),
                 ("SELECT id FROM people; SELECT id FROM people;", "syntax")]
        for sql, phase in cases:
            with self.subTest(sql=sql), self.assertRaises(CompilerError) as caught:
                self.query(sql)
            self.assertEqual(caught.exception.phase, phase)

    def test_csv_inference_override_and_persistence(self):
        self.catalog.add_csv(self.key, "accounts", "code,balance,active\n001,50,true\n002,,false\n", {"code": "STRING", "balance": "NUMBER", "active": "BOOLEAN"}, ["code"])
        tables = Catalog(self.path).get(self.key)["tables"]
        report = compile_query(tables, "SELECT code FROM accounts WHERE balance IS NULL;")
        self.assertEqual(report["result"]["rows"], [["002"]])
        self.assertIn("code", tables["accounts"].indexes)

    def test_bad_imports(self):
        with self.assertRaises(CompilerError):
            self.catalog.add_csv(self.key, "bad", "id,id\n1,2\n")
        with self.assertRaises(CompilerError):
            self.catalog.add_csv(self.key, "bad", "id,name\n1\n")
        with self.assertRaises(CompilerError):
            self.catalog.add_json(self.key, "bad", [{"id": 1}, {"id": "one"}])
        with self.assertRaises(CompilerError):
            self.catalog.add_json(self.key, "people", [{"id": 1}])

    def test_sqlite_import(self):
        source = Path(self.temp.name) / "source.db"
        with closing(sqlite3.connect(source)) as con, con:
            con.execute("CREATE TABLE products (id INTEGER, name TEXT, price REAL)")
            con.execute("INSERT INTO products VALUES (1,'Book',12.5)")
        self.catalog.import_sqlite(self.key, source)
        report = self.query("SELECT name FROM products WHERE price > 10;")
        self.assertEqual(report["result"]["rows"], [["Book"]])

    def test_three_table_join(self):
        sql = "SELECT p.id, d.name, e.name FROM people p JOIN departments d ON p.department_id=d.id JOIN departments e ON d.id=e.id WHERE p.id=42;"
        self.assertEqual(self.query(sql)["verification"]["status"], "passed")

    def test_arithmetic_parentheses_and_zero(self):
        for query in ("SELECT id FROM people WHERE (age + 2) >= 25;",
                      "SELECT id FROM people WHERE (age) IS NULL;",
                      "SELECT id FROM people WHERE 1 / 0 IS NULL;",
                      "SELECT id FROM people WHERE -5 / 2 = -2;",
                      "SELECT id FROM people WHERE -5 % 2 = -1;"):
            self.assertEqual(self.query(query)["verification"]["status"], "passed")

    def test_sqlite_differential(self):
        # Independent oracle: compiled queries still execute in our own engine.
        with closing(sqlite3.connect(":memory:")) as con, con:
            con.execute("CREATE TABLE people(id INTEGER,name TEXT,age INTEGER,city TEXT,active INTEGER,department_id INTEGER)")
            con.executemany("INSERT INTO people VALUES (?,?,?,?,?,?)", [tuple(r.values()) for r in self.tables["people"].rows])
            con.execute("CREATE TABLE departments(id INTEGER,name TEXT)")
            con.executemany("INSERT INTO departments VALUES (?,?)", [tuple(r.values()) for r in self.tables["departments"].rows])
            queries = [
                "SELECT id FROM people WHERE age IS NULL ORDER BY id;",
                "SELECT id FROM people WHERE NOT (age >= 20) ORDER BY id;",
                "SELECT id FROM people WHERE age >= 20 OR age IS NULL ORDER BY id;",
                "SELECT id FROM people WHERE age + 2 >= 25 ORDER BY id;",
                "SELECT id FROM people WHERE age >= (18 + 2) ORDER BY id;",
                "SELECT id FROM people WHERE (age + 2) >= 25 ORDER BY id;",
                "SELECT id FROM people WHERE age / 2 >= 10 ORDER BY id;",
                "SELECT id FROM people WHERE age % 3 = 1 ORDER BY id;",
                "SELECT id FROM people WHERE 10 > 5 AND age >= 18 AND age >= 18 ORDER BY id;",
                "SELECT id FROM people WHERE city = 'Mumbai' AND active = TRUE ORDER BY id;",
                "SELECT DISTINCT city FROM people ORDER BY city;",
                "SELECT p.id,d.name FROM people p JOIN departments d ON p.department_id=d.id WHERE p.age>=20 ORDER BY p.id;",
                "SELECT id,age FROM people ORDER BY age DESC,id ASC LIMIT 10;"
            ]
            rng = random.Random(42)
            for _ in range(50):
                age, identifier = rng.randint(16, 30), rng.randint(0, 110)
                op = rng.choice(["AND", "OR"])
                queries.append(f"SELECT id,name FROM people WHERE (age >= {age} {op} id = {identifier}) AND NOT (city = 'Pune') ORDER BY id;")
            for query in queries:
                with self.subTest(query=query):
                    expected = [list(row) for row in con.execute(query)]
                    self.assertEqual(self.query(query)["result"]["rows"], expected)

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Catalog(Path(self.temp.name) / "catalog.json")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class(self.catalog))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_upload_compile_http_flow(self):
        code, body = self.request("/api/datasets", {"name": "My upload"})
        self.assertEqual(code, 201)
        key = body["dataset_id"]
        code, _ = self.request("/api/tables", {"dataset_id": key, "name": "customers", "csv_text": "id,name\n1,PersonA\n2,PersonB\n", "indexed_columns": ["id"]})
        self.assertEqual(code, 201)
        code, report = self.request("/api/compile", {"dataset_id": key, "query": "SELECT name FROM customers WHERE id=1;"})
        self.assertEqual(code, 200)
        self.assertEqual(report["result"]["rows"], [["PersonA"]])
        self.assertEqual(self.request("/api/datasets")[0], 200)
        self.assertEqual(self.request("/api/compile", {"dataset_id": key, "query": "SELECT missing FROM customers"})[0], 400)

    def test_origin_and_request_validation(self):
        self.assertEqual(self.request("/api/demo", {}, "https://example.com")[0], 400)
        self.assertEqual(self.request("/api/compile", {"dataset_id": []})[0], 400)
        self.assertEqual(self.request("/api/datasets", {"name": ""})[0], 400)
        self.assertEqual(self.request("/api/health")[0], 200)

    def test_teaching_assets_are_served(self):
        for path in ('/', '/app.js', '/teaching.js', '/tree.js', '/style.css'):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read())

    def test_incremental_api_explanations_and_comparison(self):
        _, demo = self.request('/api/demo', {})
        _, run = self.request('/api/runs', {'dataset_id': demo['dataset_id'], 'query': 'SELECT name FROM students WHERE id=42 AND 1=1;', 'mode': 'execute'})
        for phase in range(1, 8):
            status, report = self.request('/api/next', {'run_id': run['run_id']})
            self.assertEqual(status, 200)
            self.assertIn(str(phase), report['phase_explanations'])
        self.assertEqual(len(report['plan_comparison']['stages']), 3)
        self.assertEqual(report['verification']['status'], 'passed')

    def test_sqlite_raw_upload(self):
        _, body = self.request("/api/datasets", {"name": "SQLite"})
        source = Path(self.temp.name) / "upload.db"
        with closing(sqlite3.connect(source)) as con, con:
            con.execute("CREATE TABLE goods(id INTEGER, title TEXT)")
            con.execute("INSERT INTO goods VALUES (1,'Pen')")
        request = urllib.request.Request(self.base + "/api/import/sqlite?dataset_id=" + body["dataset_id"], data=source.read_bytes(), headers={"Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 201)
        _, report = self.request("/api/compile", {"dataset_id": body["dataset_id"], "query": "SELECT title FROM goods;"})
        self.assertEqual(report["result"]["rows"], [["Pen"]])

if __name__ == "__main__":
    unittest.main()
