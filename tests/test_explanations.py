import copy
import json
import tempfile
import unittest
from pathlib import Path
from compiler.catalog import Catalog
from compiler.pipeline import compile_query
from compiler.journey import Journey
from compiler.explanations import nodes
from compiler.optimization import CostOptimizer
from run import create_demo


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = Catalog(Path(self.temp.name) / 'catalog.json')
        self.key = create_demo(self.catalog)
        self.tables = self.catalog.get(self.key)['tables']

    def tearDown(self):
        self.temp.cleanup()

    def test_explanations_arrive_only_after_their_phase(self):
        run = Journey(self.tables, 'SELECT name FROM students WHERE id = 42 AND 1 = 1;', 'execute')
        for phase in range(1, 8):
            r = run.advance()
            self.assertEqual(set(r['phase_explanations']), {str(i) for i in range(1, phase + 1)})
            note = r['phase_explanations'][str(phase)]
            self.assertTrue(note['query_details'])
            if phase < 4:
                self.assertNotIn('logical_plan_guide', r)
            if phase < 5:
                self.assertNotIn('plan_comparison', r)
            elif phase == 5:
                self.assertEqual(len(r['plan_comparison']['stages']), 2)
        self.assertEqual(len(r['plan_comparison']['stages']), 3)
        self.assertEqual(r['verification']['status'], 'passed')
        json.dumps(r, allow_nan=False)

    def test_original_is_estimated_without_index_selection(self):
        r = compile_query(self.tables, 'SELECT name, marks FROM students WHERE id = 42 AND 1 = 1;')
        original, rules, final = r['plan_comparison']['stages']
        self.assertEqual(original['estimated_cost'], 250.1)
        self.assertEqual(rules['estimated_cost'], 250.1)
        self.assertEqual(final['estimated_cost'], 3.55)
        self.assertEqual(original['scan_columns'], 7)
        self.assertEqual(rules['scan_columns'], 3)
        self.assertEqual(final['reduction_from_original'], 246.55)
        self.assertEqual(final['reduction_percent'], 98.58)

    def test_join_estimates_hold_baseline_algorithms_fixed(self):
        r = compile_query(self.tables, 'SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id=d.id WHERE s.marks>80;')
        original, rules, final = r['plan_comparison']['stages']
        self.assertLess(rules['estimated_cost'], original['estimated_cost'])
        self.assertLess(final['estimated_cost'], rules['estimated_cost'])
        self.assertIn('predicate_pushdown', [c['rule'] for c in r['rule_optimizations']])
        self.assertTrue(all(c['before'] and c['after'] for c in r['rule_optimizations']))
        self.assertEqual(r['verification']['status'], 'passed')

    def test_walkthrough_matches_codegen_temporaries(self):
        r = compile_query(self.tables, 'SELECT DISTINCT s.name FROM students s JOIN departments d ON s.department_id=d.id WHERE s.marks>80 ORDER BY s.name LIMIT 2;', execute=False)
        guide = r['logical_plan_guide']
        instructions = r['intermediate_code'].splitlines()
        self.assertEqual(len(guide['steps']), len(instructions) - 1)
        for i, step in enumerate(guide['steps'], 1):
            self.assertEqual(step['relation'], f't{i}')
            self.assertTrue(instructions[i - 1].startswith(f't{i} = '))
            self.assertTrue(all(int(t[1:]) < i for t in step['inputs']))
        self.assertEqual(guide['steps'][-1]['operator'], 'Limit')
        self.assertEqual(instructions[-1], 'RETURN ' + guide['result'])

    def test_imported_schema_drives_the_explanation(self):
        self.catalog.add_csv(self.key, 'orders', 'order_no,amount\n901,12\n902,25\n', indexed=['order_no'])
        r = compile_query(self.tables, 'SELECT amount FROM orders o WHERE o.order_no=902;')
        semantic = ' '.join(r['phase_explanations']['3']['query_details'])
        self.assertIn('o resolves to orders: 2 rows, 2 columns', semantic)
        self.assertIn('o.order_no', semantic)
        self.assertNotIn('students', semantic)
        self.assertEqual(r['result']['rows'], [[25]])

    def test_zero_cost_and_empty_plan(self):
        r = compile_query(self.tables, 'SELECT name FROM students LIMIT 0;')
        self.assertEqual(r['plan_comparison']['stages'][0]['estimated_cost'], 0)
        self.assertEqual(r['plan_comparison']['stages'][-1]['estimated_cost'], 0)
        self.assertEqual(r['cost_decisions'], [])
        self.catalog.add_json(self.key, 'empty', [], schema={'id': 'NUMBER'})
        r = compile_query(self.tables, 'SELECT id FROM empty;')
        self.assertIsNone(r['plan_comparison']['stages'][0]['reduction_percent'])
        json.dumps(r, allow_nan=False)

    def test_no_rewrite_or_eligible_index_is_reported_honestly(self):
        r = compile_query(self.tables, 'SELECT * FROM students;')
        self.assertEqual(r['rule_optimizations'], [])
        self.assertEqual(r['intermediate_code'], r['optimized_code'])
        costs = [s['estimated_cost'] for s in r['plan_comparison']['stages']]
        self.assertEqual(len(set(costs)), 1)
        self.assertEqual(r['cost_decisions'][0]['chosen'], 'SequentialScan')

    def test_estimation_does_not_mutate_logical_plan(self):
        r = compile_query(self.tables, 'SELECT name FROM students WHERE id=42;', execute=False)
        before = copy.deepcopy(r['original_plan'])
        estimated, _ = CostOptimizer().optimize(r['original_plan'], self.tables, choose_alternatives=False)
        self.assertEqual(r['original_plan'], before)
        self.assertIn('SequentialScan', [n['type'] for n in nodes(estimated)])
        self.assertNotIn('IndexLookup', [n['type'] for n in nodes(estimated)])
        self.assertNotIn('result', r)

    def test_syntax_only_needs_no_catalog(self):
        run = Journey({}, 'SELECT unknown FROM nowhere;', 'syntax')
        run.advance()
        r = run.advance()
        self.assertEqual(set(r['phase_explanations']), {'1', '2'})
        self.assertNotIn('plan_comparison', r)
        self.assertIn('Only grammar', ' '.join(r['phase_explanations']['2']['query_details']))


if __name__ == '__main__':
    unittest.main()
