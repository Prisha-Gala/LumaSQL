from time import perf_counter
from .syntax import tokenize, Parser
from .semantics import analyze, make_plan
from .optimization import RuleOptimizer, CostOptimizer
from .execution import Executor
from .errors import CompilerError
from .codegen import generate_code
from .explanations import add_comparison, add_phase_explanation

def compile_query(tables, query, *, verify=True, execute=True, result_limit=1000):
    if type(verify) is not bool or type(execute) is not bool:
        raise CompilerError("request", "verify and execute must be Booleans.")
    if type(result_limit) is not int or not 1 <= result_limit <= 10000:
        raise CompilerError("request", "result_limit must be an integer between 1 and 10,000.")
    started = perf_counter()
    tokens = tokenize(query)
    ast = Parser(tokens).parse()
    bound, outputs = analyze(ast, tables)
    original = make_plan(bound, outputs)
    logical, rules = RuleOptimizer().optimize(original, tables)
    physical, decisions = CostOptimizer().optimize(logical, tables)
    intermediate_code = generate_code(original)
    optimized_code = generate_code(logical)
    physical_code = generate_code(physical)
    report = {"query": query, "tokens": tokens, "ast": ast, "bound_ast": bound,
              "output_columns": outputs, "original_plan": original,
              "optimized_logical_plan": logical, "physical_plan": physical,
              "rule_optimizations": rules, "cost_decisions": decisions,
              "intermediate_code": intermediate_code, "optimized_code": optimized_code,
              "physical_code": physical_code,
              "verification": {"status": "not_requested"},
              "notes": ["Costs are estimated work units, not milliseconds.",
                        "Index/statistics construction is done at import/startup and reported separately.",
                        "SELECT subset only; INSERT/UPDATE/DELETE, aggregates, subqueries and outer joins are unsupported."]}
    add_comparison(report, tables)
    add_comparison(report, tables, physical=True)
    for phase in range(1, 7):
        add_phase_explanation(report, phase, tables)
    report['compilation_ms'] = round((perf_counter() - started) * 1000, 3)
    if execute:
        rows, metrics = Executor(tables).execute(physical)
        report["result"] = {"columns": [c["name"] for c in outputs], "rows": rows[:result_limit],
                            "total_rows": len(rows), "truncated": len(rows) > result_limit}
        report["optimized_metrics"] = metrics
        if verify:
            try:
                original_rows, baseline = Executor(tables).execute(original)
                if original_rows != rows:
                    raise CompilerError("verification", "Original and optimized results differ; no successful result returned.")
                report["baseline_metrics"] = baseline
                report["verification"] = {"status": "passed", "explanation": "Full results matched, including duplicates and deterministic demo row order, before display truncation."}
            except CompilerError as exc:
                if exc.phase != "execution":
                    raise
                report["verification"] = {"status": "skipped_work_limit", "explanation": str(exc)}
        add_phase_explanation(report, 7, tables)
        return report
    report["verification"] = {"status": "not_executed"}
    add_phase_explanation(report, 7, tables)
    return report
