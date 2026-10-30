"""Own logical/physical plan interpreter, including SQL three-valued conditions."""
import math
import operator
from collections import defaultdict
from time import perf_counter
from .errors import CompilerError

COMPARE = {"=": operator.eq, "!=": operator.ne, "<>": operator.ne,
           "<": operator.lt, ">": operator.gt, "<=": operator.le, ">=": operator.ge}

def sql_and(a, b):
    if a is False or b is False:
        return False
    return None if a is None or b is None else True

def sql_or(a, b):
    if a is True or b is True:
        return True
    return None if a is None or b is None else False

def evaluate(node, row, metrics=None):
    kind = node["type"]
    if kind == "Literal":
        return node["value"]
    if kind == "Column":
        return row[node["name"]]
    if kind == "Not":
        value = evaluate(node["condition"], row, metrics)
        return None if value is None else not value
    if kind == "IsNull":
        value = evaluate(node["operand"], row, metrics) is None
        return not value if node["negate"] else value
    left = evaluate(node["left"], row, metrics)
    op = node["operator"]
    if kind == "Logical":
        if op == "AND" and left is False:
            return False
        if op == "OR" and left is True:
            return True
        right = evaluate(node["right"], row, metrics)
        return sql_and(left, right) if op == "AND" else sql_or(left, right)
    right = evaluate(node["right"], row, metrics)
    if kind == "Comparison" and metrics is not None:
        metrics["comparisons"] += 1
    if left is None or right is None:
        return None
    if kind == "Comparison":
        return COMPARE[op](left, right)
    # Declared subset: division/modulo by zero produce NULL, like SQLite.
    if op in {"/", "%"} and right == 0:
        return None
    if metrics is not None:
        metrics["arithmetic_operations"] += 1
    if op == "/" and type(left) is int and type(right) is int:
        result = (abs(left) // abs(right)) * (-1 if (left < 0) != (right < 0) else 1)
    elif op == "%":
        a, b = int(left), int(right)
        if b == 0:
            return None
        quotient = (abs(a) // abs(b)) * (-1 if (a < 0) != (b < 0) else 1)
        result = a - quotient * b
        if isinstance(left, float) or isinstance(right, float):
            result = float(result)
    else:
        operations = {"+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv}
        result = operations[op](left, right)
    if isinstance(result, float) and not math.isfinite(result):
        raise CompilerError("execution", "Numeric result is not finite.")
    return result

class Executor:
    def __init__(self, tables, max_work=2000000, max_intermediate=100000):
        self.tables, self.max_work, self.max_intermediate = tables, max_work, max_intermediate

    def tick(self, key, amount=1):
        self.metrics[key] += amount
        if self.metrics["rows_examined"] + self.metrics["join_pairs_checked"] > self.max_work:
            raise CompilerError("execution", "Work limit reached; add a selective filter/index or use a smaller dataset.")

    def bounded(self, rows):
        if len(rows) > self.max_intermediate:
            raise CompilerError("execution", "Intermediate result exceeds 100,000 rows.")
        return rows

    def run_node(self, plan):
        kind = plan["type"]
        if kind == "EmptyResult":
            return []
        if kind in {"TableScan", "SequentialScan", "IndexLookup"}:
            table = self.tables[plan["table"]]
            if kind == "IndexLookup":
                self.tick("index_lookups")
                positions = table.indexes[plan["index_column"]].get(plan["lookup_value"], [])
                source = (table.rows[i] for i in positions)
            else:
                source = iter(table.rows)
            cols = plan.get("columns", list(table.schema))
            rows = []
            for row in source:
                self.tick("rows_examined")
                self.tick("cells_materialized", len(cols))
                rows.append({plan["alias"] + "." + c: row[c] for c in cols})
            return rows
        if kind == "Filter":
            result = []
            for row in self.run_node(plan["input"]):
                self.tick("filter_checks")
                if evaluate(plan["condition"], row, self.metrics) is True:
                    result.append(row)
            return result
        if kind in {"Join", "NestedLoopJoin", "HashJoin"}:
            left, right = self.run_node(plan["left"]), self.run_node(plan["right"])
            result = []
            if kind == "HashJoin":
                build, probe = (left, right) if plan["build_side"] == "left" else (right, left)
                build_key = plan["left_key"] if plan["build_side"] == "left" else plan["right_key"]
                probe_key = plan["right_key"] if plan["build_side"] == "left" else plan["left_key"]
                buckets = defaultdict(list)
                ordered = []
                for index, row in enumerate(build):
                    self.tick("hash_build_rows")
                    if row[build_key] is not None:
                        buckets[row[build_key]].append((index, row))
                for probe_index, row in enumerate(probe):
                    self.tick("hash_probe_rows")
                    for build_index, other in buckets.get(row[probe_key], []) if row[probe_key] is not None else []:
                        self.tick("join_pairs_checked")
                        combined = {**row, **other}
                        if evaluate(plan["condition"], combined, self.metrics) is True:
                            pair = (build_index, probe_index) if plan["build_side"] == "left" else (probe_index, build_index)
                            ordered.append((pair, combined))
                            self.bounded(ordered)
                # Preserve teaching-demo input order even without ORDER BY,
                # so LIMIT is reproducible across baseline and physical plans.
                ordered.sort(key=lambda item: item[0])
                result = [row for _, row in ordered]
            else:
                for l in left:
                    for r in right:
                        self.tick("join_pairs_checked")
                        combined = {**l, **r}
                        if evaluate(plan["condition"], combined, self.metrics) is True:
                            result.append(combined)
                            self.bounded(result)
            return result
        if kind == "Sort":
            rows = self.run_node(plan["input"])
            self.tick("sort_input_rows", len(rows))
            for key in reversed(plan["keys"]):
                col = key["column"]["name"]
                # NULL first in ASC, last in DESC.
                rows.sort(key=lambda r: (r[col] is not None, r[col]), reverse=key["direction"] == "DESC")
            return rows
        if kind == "Project":
            return [[row[c["source"]] for c in plan["columns"]] for row in self.run_node(plan["input"])]
        if kind == "Distinct":
            seen, result = set(), []
            for row in self.run_node(plan["input"]):
                key = tuple(row)
                if key not in seen:
                    seen.add(key)
                    result.append(row)
            return result
        if kind == "Limit":
            if plan["count"] == 0:
                return []
            return self.run_node(plan["input"])[:plan["count"]]
        raise CompilerError("execution", f"Unsupported plan {kind}.")

    def execute(self, plan):
        self.metrics = {k: 0 for k in ("rows_examined", "cells_materialized", "filter_checks", "comparisons", "arithmetic_operations", "index_lookups", "join_pairs_checked", "hash_build_rows", "hash_probe_rows", "sort_input_rows")}
        started = perf_counter()
        rows = self.run_node(plan)
        self.metrics["rows_returned"] = len(rows)
        self.metrics["execution_ms"] = round((perf_counter() - started) * 1000, 3)
        return rows, self.metrics.copy()
