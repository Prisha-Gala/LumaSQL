"""Presentation examples and the exact missing-table journey behavior."""
import tempfile
import unittest
from pathlib import Path
from compiler.catalog import Catalog
from compiler.journey import Journey
from compiler.pipeline import compile_query
from run import create_demo


class DemoQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Catalog(Path(self.temp.name) / 'catalog.json')
        self.key = create_demo(self.catalog)
        self.tables = self.catalog.get(self.key)['tables']

    def tearDown(self):
        self.temp.cleanup()

    def test_different_demo_tables_and_join(self):
        cases = [
            ('SELECT name, marks FROM students WHERE id=42 AND 1=1;', [['Student 42', 92]]),
            ('SELECT id, name FROM departments ORDER BY id;', [[1, 'CSBS'], [2, 'Computer'], [3, 'IT'], [4, 'Mechanical']]),
            ('SELECT name FROM departments WHERE id=3;', [['IT']]),
            ('SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id=d.id WHERE s.marks>80 ORDER BY s.id LIMIT 3;', [['Student 31', 'Mechanical'], ['Student 32', 'CSBS'], ['Student 33', 'Computer']]),
            ('SELECT id, name, marks FROM students WHERE marks>95 AND marks>95 AND 2+3=5 ORDER BY marks DESC, id ASC LIMIT 5;', [[50, 'Student 50', 100], [101, 'Student 101', 100], [152, 'Student 152', 100], [49, 'Student 49', 99], [100, 'Student 100', 99]]),
        ]
        for query, expected in cases:
            with self.subTest(query=query):
                report = compile_query(self.tables, query)
                self.assertEqual(report['result']['rows'], expected)
                self.assertEqual(report['verification']['status'], 'passed')

    def test_missing_table_stops_at_semantics(self):
        for mode in ('execute', 'compile'):
            with self.subTest(mode=mode):
                run = Journey(self.tables, 'SELECT name FROM teachers;', mode)
                for _ in range(3):
                    report = run.advance()
                self.assertEqual(report['error']['phase'], 'semantic')
                self.assertEqual(report['error']['message'], "Table 'teachers' does not exist in this dataset.")
                self.assertEqual([s['status'] for s in report['stages']], ['complete', 'complete', 'error'])
                self.assertTrue(report['done'])
                self.assertNotIn('result', report)
                self.assertNotIn('intermediate_code', report)
                self.assertEqual(len(run.advance()['stages']), 3)

    def test_missing_table_is_not_a_syntax_error(self):
        run = Journey({}, 'SELECT name FROM teachers;', 'syntax')
        run.advance()
        report = run.advance()
        self.assertTrue(report['done'])
        self.assertNotIn('error', report)
        self.assertEqual(len(report['stages']), 2)

    def test_customer_examples_after_import(self):
        csv = (Path(__file__).resolve().parents[1] / 'examples/customers.csv').read_text(encoding='utf-8')
        self.catalog.add_csv(self.key, 'customers', csv, indexed=['id', 'city'])
        cases = [
            ("SELECT id, name, age FROM customers WHERE city='Mumbai' AND age>=20 ORDER BY id;", [[1, 'PersonA', 20], [2, 'PersonB', 21]]),
            ('SELECT id, name FROM customers WHERE age IS NULL;', [[5, 'PersonE']]),
        ]
        for query, expected in cases:
            report = compile_query(self.tables, query)
            self.assertEqual(report['result']['rows'], expected)
            self.assertEqual(report['verification']['status'], 'passed')
