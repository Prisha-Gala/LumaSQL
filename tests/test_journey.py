import tempfile
import unittest
from pathlib import Path
from compiler.catalog import Catalog
from compiler.journey import Journey
from run import create_demo

class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        c=Catalog(Path(self.temp.name)/'catalog.json')
        self.tables=c.get(create_demo(c))['tables']
    def tearDown(self): self.temp.cleanup()
    def test_incremental_execution(self):
        run=Journey(self.tables,'SELECT name FROM students WHERE id = 42;', 'execute')
        for n in range(7):
            r=run.advance()
            self.assertEqual(len(r['stages']),n+1)
            if n<6:self.assertNotIn('result',r)
        self.assertEqual(r['result']['rows'],[['Student 42']])
        self.assertEqual(r['verification']['status'],'passed')
        self.assertEqual(len(run.advance()['stages']),7)
    def test_syntax_without_tables(self):
        run=Journey({},'SELECT name FROM unknown_table;','syntax')
        run.advance();r=run.advance()
        self.assertTrue(r['done']);self.assertNotIn('physical_plan',r)
    def test_compile_does_not_execute(self):
        run=Journey(self.tables,'SELECT name FROM students;','compile')
        for _ in range(7):r=run.advance()
        self.assertIn('physical_plan',r);self.assertNotIn('result',r)
        self.assertEqual(r['cost_decisions'][0]['chosen'],'SequentialScan')
    def test_error_preserves_completed_phases(self):
        run=Journey(self.tables,'SELECT missing FROM students;','execute')
        for _ in range(3):r=run.advance()
        self.assertIn('ast',r);self.assertEqual(r['error']['phase'],'semantic')
        self.assertTrue(r['done']);self.assertEqual(len(run.advance()['stages']),3)
    def test_invalid_syntax_stops(self):
        run=Journey({},'SELECT FROM;', 'syntax')
        run.advance();r=run.advance()
        self.assertEqual(r['stages'][-1]['status'],'error')
