"""Query-specific teaching notes and comparable estimates; no query execution here."""
from collections import Counter
from .codegen import expression, generate_code
from .optimization import CostOptimizer


PHASES = [
    ("Recognize the vocabulary of SQL", "SQL source text", "A token stream with source positions",
     "The parser needs meaningful symbols rather than individual characters. Spaces and comments separate tokens but are not emitted; EOF marks the end."),
    ("Recognize the structure of the statement", "Phase 1 token stream", "An abstract syntax tree (AST)",
     "The AST groups SELECT, FROM, JOIN, WHERE, ORDER BY and LIMIT into structured fields. It represents SQL grammar; it does not choose execution operators."),
    ("Check what the query means in this dataset", "AST and selected dataset schema", "Bound AST and typed output columns",
     "Binding resolves every column to a table alias. Type checks reject invalid comparisons and ambiguous or missing names before execution."),
    ("Translate SQL meaning into relational operations", "Bound AST and output-column mapping", "Relational intermediate code (ICG)",
     "ICG expresses the query as SCAN, FILTER, JOIN, PROJECT and other supported instructions. Each t1, t2, etc. holds an intermediate relation; RETURN identifies the output."),
    ("Rewrite the computation using equivalence rules", "Original intermediate representation", "Optimized intermediate code",
     "Rules simplify expressions, move eligible filters below INNER JOINs and prune unused columns. Rules do not select access paths by comparing costs. Result equivalence is checked later in execute mode."),
    ("Choose concrete ways to perform the operations", "Optimized intermediate representation, table statistics and available indexes", "Selected execution strategies, physical code and candidate costs",
     "The optimizer compares eligible sequential scans/index lookups and nested-loop/hash joins. It keeps the SQL join order. The chosen plan object is what the executor runs."),
    ("Run the physical operators and verify the answer", "Selected execution instructions and table rows", "Result rows, measured work and verification status",
     "The Python executor evaluates the plan itself. It also runs the original plan when requested and compares full results before display truncation. SQLite is only a data-import format."),
]


def nodes(plan):
    yield plan
    for key in ("input", "left", "right"):
        if key in plan:
            yield from nodes(plan[key])


def plan_summary(plan, tables):
    counts = Counter(n["type"] for n in nodes(plan))
    scans = [n for n in nodes(plan) if n["type"] in {"TableScan", "SequentialScan", "IndexLookup"}]
    return {
        "operators": sum(counts.values()),
        "filters": counts["Filter"],
        "joins": sum(counts[k] for k in ("Join", "NestedLoopJoin", "HashJoin")),
        "scan_columns": sum(len(n.get("columns", tables[n["table"]].schema)) for n in scans),
        "access_paths": [f"{n['alias']}: {n['type']}" for n in scans],
    }


def add_comparison(report, tables, *, physical=False):
    """Price baseline and rewrites with scans/nested loops, then chosen strategies.

    All stages use the same existing model. This makes the contribution of
    physical choices visible without silently optimizing the original plan.
    """
    if not physical:
        report["plan_comparison"] = {
            "unit": "estimated work units",
            "basis": "Original and rule-only versions use sequential scans and nested-loop joins. The final version uses the strategies selected in Phase 6. All totals include child costs and use the same cost formulas and dataset statistics.",
            "limitations": "These are estimates, not milliseconds. Column pruning and simpler predicate expressions are not separately priced by this row-based model. Combined predicates and join cardinalities use simplifying assumptions, so equivalent plans can have different row estimates. A lower estimate or runtime is not guaranteed.",
            "stages": [],
        }
        for key, label in (("original_plan", "Original · Phase 4"), ("optimized_logical_plan", "Rule-only · Phase 5")):
            estimated, _ = CostOptimizer().optimize(report[key], tables, choose_alternatives=False)
            report["plan_comparison"]["stages"].append({
                "label": label, "estimated_cost": estimated["estimated_cost"],
                "estimated_rows": estimated["estimated_rows"],
                **plan_summary(report[key], tables),
            })
    else:
        plan = report["physical_plan"]
        report["plan_comparison"]["stages"].append({
            "label": "Cost-selected · Phase 6", "estimated_cost": plan["estimated_cost"],
            "estimated_rows": plan["estimated_rows"], **plan_summary(plan, tables),
        })
    stages = report["plan_comparison"]["stages"]
    baseline = stages[0]["estimated_cost"]
    for i, stage in enumerate(stages):
        stage["reduction_from_original"] = round(baseline - stage["estimated_cost"], 3)
        stage["reduction_percent"] = round(100 * (baseline - stage["estimated_cost"]) / baseline, 2) if baseline else None
        stage["reduction_from_previous"] = round(stages[i - 1]["estimated_cost"] - stage["estimated_cost"], 3) if i else 0


def logical_walkthrough(plan):
    """Postorder IDs match the tN names emitted by codegen."""
    steps = []

    def visit(node):
        inputs = [visit(node[k]) for k in ("input", "left", "right") if k in node]
        kind = node["type"]
        source = ", ".join(inputs)
        symbol = {"TableScan": "relation", "Join": "⋈", "Filter": "σ", "Project": "π", "Sort": "τ", "Distinct": "δ", "Limit": "LIMIT", "EmptyResult": "∅"}[kind]
        if kind == "TableScan":
            detail = f"Read table {node['table']} as {node['alias']}. The original scan carries all table columns. No sequential-scan/index choice has been made at this phase."
        elif kind == "Join":
            detail = f"Combine {inputs[0]} and {inputs[1]} where {expression(node['condition'])} is TRUE. This INNER JOIN retains matching pairs; its algorithm is selected in Phase 6."
        elif kind == "Filter":
            detail = f"Keep rows of {source} where {expression(node['condition'])} is TRUE. FALSE and SQL UNKNOWN (NULL) do not pass."
        elif kind == "Project":
            detail = f"Take {source} and return only " + ", ".join(f"{c['source']} as {c['name']}" for c in node["columns"]) + ". Projection chooses columns, whereas filtering chooses rows."
        elif kind == "Sort":
            detail = f"Order {source} by " + ", ".join(f"{k['column']['name']} {k['direction']}" for k in node["keys"]) + ". This implementation sorts before projection so an ORDER BY column can be used even when it is not selected."
        elif kind == "Distinct":
            detail = f"Remove duplicate output rows from {source}; this is the SELECT DISTINCT operation."
        elif kind == "Limit":
            detail = (f"Return no rows without reading {source}; this executor short-circuits LIMIT 0." if node['count'] == 0 else f"Return at most {node['count']} rows from {source}. For a positive LIMIT, this executor materializes the child first; it does not automatically stop its scan early.")
        else:
            detail = "Produce an empty relation without accessing a table."
        temporary = f"t{len(steps) + 1}"
        steps.append({"relation": temporary, "operator": kind, "symbol": symbol, "inputs": inputs, "explanation": detail})
        return temporary

    result = visit(plan)
    return {"steps": steps, "result": result,
            "reading": "Start at the leaves (TableScan), follow each branch upward, and read the root as the final result. For a join, produce both input relations before combining them. The numbered walkthrough follows a left-then-right postorder traversal, matching the ICG temporary names; it is not a promise of parallel execution.",
            "purpose": "This plan bridges the validated AST and execution. It makes data dependencies explicit, gives Phase 5 a structure to rewrite safely, and supplies Phase 6 with operators to implement using physical strategies. The original tree also serves as the execution baseline for verification.",
            "distinction": "AST: how the SQL statement is structured. Logical plan: which relational operations produce the answer. Physical plan: how those operations run (for example, IndexLookup or HashJoin). ICG: a readable text serialization using temporary relations. The executor consumes the plan object, not the displayed text."}


def add_phase_explanation(report, phase, tables):
    title, incoming, outgoing, reason = PHASES[phase - 1]
    details = []
    ast = report.get("ast") if phase == 2 else report.get("bound_ast", report.get("ast"))
    if phase == 1:
        tokens = [t for t in report["tokens"] if t["type"] != "EOF"]
        for kind, count in Counter(t["type"] for t in tokens).items():
            examples = list(dict.fromkeys(t["value"] for t in tokens if t["type"] == kind))[:6]
            details.append(f"{kind}: {count} token(s); examples: {', '.join(examples)}.")
        details.append("Line and column positions point back to the submitted SQL when an error is reported.")
    elif phase in (2, 3):
        sources = [ast["from"]] + [j["table"] for j in ast["joins"]]
        details.append("Sources: " + "; ".join(f"{t['name']} AS {t['alias']}" for t in sources) + ".")
        details.append("WHERE: " + (expression(ast["where"]) if ast["where"] else "absent; no WHERE filter") + ".")
        details.extend("JOIN ON: " + expression(j["condition"]) + "." for j in ast["joins"])
        details.append("SELECT: " + ", ".join(c.get("name", "*") for c in ast["columns"]) + ".")
        details.append("ORDER BY: " + (", ".join(k["column"]["name"] + " " + k["direction"] for k in ast["order_by"]) or "absent; SQL does not guarantee row order") + ".")
        details.append(f"DISTINCT: {'yes' if ast['distinct'] else 'no'}. LIMIT: {ast['limit'] if ast['limit'] is not None else 'absent'}.")
        if phase == 2:
            details.append("Only grammar has been checked at this point. Table existence and column types are checked in Phase 3.")
        else:
            details.extend(f"{t['alias']} resolves to {t['name']}: {len(tables[t['name']].rows)} rows, {len(tables[t['name']].schema)} columns; indexed columns: {', '.join(tables[t['name']].indexes) or 'none'}." for t in sources)
            details.append(f"{len(report['output_columns'])} output column(s) have resolved sources and data types; see the mapping below.")
    elif phase == 4:
        walkthrough = logical_walkthrough(report["original_plan"])
        report["logical_plan_guide"] = walkthrough
        details.append("Your operator flow: " + " → ".join(f"{s['relation']} {s['operator']}" for s in walkthrough["steps"]) + f" → RETURN {walkthrough['result']}.")
        details.append("These are the original instructions before optimization. Phase 5 rewrites eligible expressions and operation order; Phase 6 chooses scan and join strategies.")
    elif phase == 5:
        counts = Counter(c["rule"] for c in report["rule_optimizations"])
        details.append("Applied rules: " + (", ".join(f"{rule} × {count}" for rule, count in counts.items()) or "none; this query has no applicable rewrite") + ".")
        details.append("Each rule below includes its actual before/after expression or subplan. The full code comparison shows the cumulative change.")
        details.append("The estimates here hold physical strategies fixed (sequential scans/nested loops) to show the effect of the logical rewrites under the same model.")
    elif phase == 6:
        for decision in report["cost_decisions"]:
            alternatives = decision["alternatives"]
            winner = alternatives[decision["chosen_candidate"]]
            local = winner.get("access_cost", winner.get("join_cost"))
            target = f"{decision['table']} AS {decision['alias']}" if "table" in decision else "join"
            details.append(f"{target}: selected {decision['chosen']} from {len(alternatives)} eligible candidate(s), local cost {local:.3f} work units.")
        if not report["cost_decisions"]:
            details.append("No table access or join remains, so no physical candidate selection is needed.")
        details.append("Local candidate costs cover only that scan or join. Whole-plan totals also include children, filters, projection, sorting and DISTINCT as applicable.")
    elif phase == 7:
        if "result" in report:
            details.append(f"Returned {report['result']['total_rows']} full result row(s); showing {len(report['result']['rows'])}.")
            details.append("Verification: " + report["verification"]["status"] + ". " + report["verification"].get("explanation", ""))
            details.append("Measured execution time excludes compilation and index setup. Work counters show which operations actually ran; estimated cost is not a timing prediction.")
        else:
            title = "Finish compilation without running the query"
            incoming, outgoing = "Selected physical plan", "Compilation complete; no result rows requested"
            reason = "Compile-only mode produces the same selected physical plan, but skips the executor and baseline verification."
            details.append("Compile-only mode stops with an executable plan. There is no measured execution or result-equivalence check because the query was not executed.")
    report.setdefault("phase_explanations", {})[str(phase)] = {
        "title": title, "input": incoming, "output": outgoing, "why": reason, "query_details": details,
    }
