import copy
import tempfile
import unittest
from pathlib import Path
from compiler.catalog import Catalog
from compiler.codegen import generate_code, expression
from compiler.journey import Journey
from compiler.pipeline import compile_query
from run import create_demo

class CodeOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        catalog=Catalog(Path(self.temp.name)/'catalog.json')
        self.tables=catalog.get(create_demo(catalog))['tables']
    def tearDown(self):self.temp.cleanup()
    def test_code_appears_at_respective_phases(self):
        run=Journey(self.tables,'SELECT name FROM students WHERE id = 42 AND 1 = 1;', 'compile')
        for _ in range(3):r=run.advance()
        self.assertNotIn('intermediate_code',r)
        r=run.advance();before=r['intermediate_code']
        self.assertIn('(1 = 1)',before)
        self.assertNotIn('optimized_code',r)
        r=run.advance()
        self.assertNotIn('(1 = 1)',r['optimized_code'])
        self.assertIn('COLUMNS [id, name]',r['optimized_code'])
        self.assertEqual(before,r['intermediate_code'])
        self.assertNotIn('physical_code',r)
        r=run.advance()
        self.assertIn('INDEX_LOOKUP(students AS students, KEY id = 42',r['physical_code'])
        self.assertIn('FILTER(',r['physical_code'])
    def test_join_pushdown_is_visible(self):
        r=compile_query(self.tables,'SELECT s.name, d.name FROM students s JOIN departments d ON s.department_id = d.id WHERE s.marks > 80;')
        self.assertLess(r['intermediate_code'].index('JOIN('),r['intermediate_code'].index('FILTER('))
        self.assertLess(r['optimized_code'].index('FILTER('),r['optimized_code'].index('JOIN('))
        self.assertIn('HASH_JOIN(',r['physical_code'])
        self.assertIn('KEYS s.department_id = d.id',r['physical_code'])
        self.assertIn('BUILD RIGHT',r['physical_code'])
        self.assertEqual(r['verification']['status'],'passed')
    def test_remaining_operators_and_no_plan_mutation(self):
        r=compile_query(self.tables,'SELECT DISTINCT city FROM students ORDER BY city DESC LIMIT 2;')
        before=copy.deepcopy(r['physical_plan']);code=generate_code(r['physical_plan'])
        self.assertEqual(before,r['physical_plan'])
        for instruction in ['SEQ_SCAN(', 'PROJECT(', 'SORT(', 'DISTINCT(', 'LIMIT(', 'RETURN ']:self.assertIn(instruction,code)
        empty=compile_query(self.tables,'SELECT name FROM students LIMIT 0;')
        self.assertEqual(empty['optimized_code'],'t1 = EMPTY_RELATION()\nRETURN t1')
    def test_string_null_and_boolean_rendering(self):
        r=compile_query(self.tables,"SELECT name FROM students WHERE NOT (city = 'O''Brien') AND (marks + 2 > 80 OR city IS NULL);",execute=False)
        for fragment in ["'O''Brien'", 'NOT', 'IS NULL', '(students.marks + 2)']:self.assertIn(fragment,r['intermediate_code'])
        for value,display in [(None,'NULL'),(True,'TRUE'),(False,'FALSE')]:self.assertEqual(expression({'type':'Literal','value':value}),display)
