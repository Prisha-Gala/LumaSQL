"""Incremental compiler: each advance executes exactly one phase."""
from time import perf_counter
from .codegen import generate_code
from .explanations import add_comparison, add_phase_explanation
from .pipeline import tokenize, Parser, analyze, make_plan, RuleOptimizer, CostOptimizer, Executor, CompilerError

class Journey:
    def __init__(self, tables, query, mode):
        if mode not in {'execute', 'compile', 'syntax'}:
            raise CompilerError('request', 'Unknown run mode.')
        if not isinstance(query, str) or not query.strip():
            raise CompilerError('request', 'Enter a SQL query.')
        self.tables, self.query, self.mode = dict(tables), query, mode
        self.report = {'query': query, 'mode': mode, 'stages': [], 'verification': {'status':'not_executed'}}
        self.step = 0
        self.failed = False

    def advance(self):
        r, n = self.report, self.step
        if self.failed or n >= (2 if self.mode == 'syntax' else 7):
            return r
        start = perf_counter()
        try:
            if n == 0: r['tokens'] = tokenize(self.query)
            elif n == 1: r['ast'] = Parser(r['tokens']).parse()
            elif n == 2: r['bound_ast'], r['output_columns'] = analyze(r['ast'], self.tables)
            elif n == 3:
                r['original_plan'] = make_plan(r['bound_ast'], r['output_columns'])
                r['intermediate_code'] = generate_code(r['original_plan'])
            elif n == 4:
                r['optimized_logical_plan'], r['rule_optimizations'] = RuleOptimizer().optimize(r['original_plan'], self.tables)
                r['optimized_code'] = generate_code(r['optimized_logical_plan'])
                add_comparison(r, self.tables)
            elif n == 5:
                r['physical_plan'], r['cost_decisions'] = CostOptimizer().optimize(r['optimized_logical_plan'], self.tables)
                r['physical_code'] = generate_code(r['physical_plan'])
                add_comparison(r, self.tables, physical=True)
            elif self.mode == 'execute':
                rows, r['optimized_metrics'] = Executor(self.tables).execute(r['physical_plan'])
                r['result'] = {'columns':[c['name'] for c in r['output_columns']], 'rows':rows[:1000], 'total_rows':len(rows), 'truncated':len(rows)>1000}
                try:
                    baseline, r['baseline_metrics'] = Executor(self.tables).execute(r['original_plan'])
                    if baseline != rows: raise CompilerError('verification','Original and optimized results differ.')
                    r['verification'] = {'status':'passed', 'explanation':'Full baseline and optimized results match before display truncation.'}
                except CompilerError as exc:
                    if exc.phase != 'execution': raise
                    r['verification'] = {'status':'skipped_work_limit','explanation':str(exc)}
            add_phase_explanation(r, n + 1, self.tables)
            self.step += 1
            r['stages'].append({'phase':n+1,'status':'complete','ms':round((perf_counter()-start)*1000,3)})
        except (CompilerError, RecursionError) as exc:
            self.failed = True
            r.pop('result', None)
            r['error'] = exc.detail() if isinstance(exc,CompilerError) else {'phase':'syntax','message':'Query nesting is too deep.'}
            r['stages'].append({'phase':n+1,'status':'error','ms':round((perf_counter()-start)*1000,3)})
        r['done'] = self.failed or self.step >= (2 if self.mode == 'syntax' else 7)
        return r
