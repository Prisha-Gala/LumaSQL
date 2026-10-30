"""Rule rewrites plus a transparent, deliberately small educational cost model."""
from copy import deepcopy
from .syntax import binary, literal
from .execution import evaluate
from .codegen import expression, generate_code

def refs(node):
    if node["type"] == "Column":
        return {node["name"]}
    found = set()
    for value in node.values():
        if isinstance(value, dict) and "type" in value:
            found |= refs(value)
    return found

def split(node, op="AND"):
    if node["type"] == "Logical" and node["operator"] == op:
        return split(node["left"], op) + split(node["right"], op)
    return [node]

def combine(nodes, op="AND"):
    if not nodes:
        return literal(True)
    result = nodes[0]
    for node in nodes[1:]:
        result = binary("Logical", op, result, node)
    return result

def aliases(plan):
    if "alias" in plan:
        return {plan["alias"]}
    result = set()
    for key in ("input", "left", "right"):
        if key in plan:
            result |= aliases(plan[key])
    return result

def constant(node, value):
    return node["type"] == "Literal" and node["value"] is value

class RuleOptimizer:
    def optimize(self, original, tables):
        self.changes, self.tables = [], tables
        plan = self.visit(deepcopy(original))
        self.prune(plan, set())
        return plan, self.changes

    def log(self, rule, message, before, after):
        self.changes.append({"rule": rule, "explanation": message,
                             "before": before, "after": after})

    def simplify(self, node):
        node = deepcopy(node)
        for key in ("left", "right", "condition", "operand"):
            if key in node:
                node[key] = self.simplify(node[key])
        if node["type"] not in {"Column", "Literal"} and not refs(node):
            value = evaluate(node, {})
            self.log("constant_folding", "Evaluate this expression once during compilation instead of for every candidate row.", expression(node), expression(literal(value)))
            return literal(value)
        if node["type"] == "Not" and node["condition"]["type"] == "Not":
            self.log("boolean_simplification", "Removed double NOT, preserving SQL NULL behavior.", expression(node), expression(node["condition"]["condition"]))
            return node["condition"]["condition"]
        if node["type"] == "Logical":
            op = node["operator"]
            identity, absorbing = (True, False) if op == "AND" else (False, True)
            terms = split(node, op)
            unique = []
            for i, term in enumerate(terms):
                if constant(term, absorbing):
                    self.log("boolean_simplification", f"Applied the absorbing value for {op}.", expression(node), expression(literal(absorbing)))
                    return literal(absorbing)
                if constant(term, identity):
                    self.log("boolean_simplification", f"Removed an identity constant from {op}.", expression(combine(unique + terms[i:], op)), expression(combine(unique + terms[i + 1:], op)))
                elif term in unique:
                    self.log("duplicate_predicate", "Test the repeated condition once; repeating it does not change the answer.", expression(combine(unique + terms[i:], op)), expression(combine(unique + terms[i + 1:], op)))
                else:
                    unique.append(term)
            return combine(unique, op) if unique else literal(identity)
        return node

    def push(self, source, conditions):
        if not conditions:
            return source
        if source["type"] == "Join":
            before = generate_code({"type": "Filter", "condition": combine(conditions), "input": source})
            left_aliases, right_aliases = aliases(source["left"]), aliases(source["right"])
            left, right, remaining = [], [], []
            for condition in conditions:
                used = {r.split(".")[0] for r in refs(condition)}
                if used and used <= left_aliases:
                    left.append(condition)
                elif used and used <= right_aliases:
                    right.append(condition)
                else:
                    remaining.append(condition)
            source["left"] = self.push(source["left"], left)
            source["right"] = self.push(source["right"], right)
            result = {"type": "Filter", "condition": combine(remaining), "input": source} if remaining else source
            if left or right:
                self.log("predicate_pushdown", "Filter one join input earlier so fewer rows reach the INNER JOIN.", before, generate_code(result))
            if not remaining:
                return source
            conditions = remaining
        if source["type"] == "Filter":
            source["condition"] = self.simplify(combine([source["condition"]] + conditions))
            return source
        return {"type": "Filter", "condition": combine(conditions), "input": source}

    def visit(self, plan):
        kind = plan["type"]
        if kind == "Limit" and plan["count"] == 0:
            self.log("empty_result", "LIMIT 0 needs no input execution.", generate_code(plan), "EMPTY_RELATION()")
            return {"type": "EmptyResult"}
        for key in ("input", "left", "right"):
            if key in plan:
                plan[key] = self.visit(plan[key])
        if kind == "Filter":
            condition = self.simplify(plan["condition"])
            if constant(condition, True):
                self.log("filter_removal", "An always-true filter cannot discard rows, so remove it.", generate_code(plan), generate_code(plan["input"]))
                return plan["input"]
            if constant(condition, False) or constant(condition, None) or plan["input"]["type"] == "EmptyResult":
                self.log("empty_result", "WHERE cannot be TRUE; return no rows without scanning.", generate_code(plan), "EMPTY_RELATION()")
                return {"type": "EmptyResult"}
            return self.push(plan["input"], split(condition))
        if kind == "Join":
            plan["condition"] = self.simplify(plan["condition"])
            if constant(plan["condition"], False) or constant(plan["condition"], None) or any(plan[k]["type"] == "EmptyResult" for k in ("left", "right")):
                self.log("empty_result", "INNER JOIN cannot produce rows.", generate_code(plan), "EMPTY_RELATION()")
                return {"type": "EmptyResult"}
            # ON predicates are pushed only for INNER JOIN; outer joins are unsupported.
            left, right, remaining = [], [], []
            for condition in split(plan["condition"]):
                used = {r.split(".")[0] for r in refs(condition)}
                if used and used <= aliases(plan["left"]):
                    left.append(condition)
                elif used and used <= aliases(plan["right"]):
                    right.append(condition)
                else:
                    remaining.append(condition)
            if left or right:
                before = generate_code(plan)
                plan["left"] = self.push(plan["left"], left)
                plan["right"] = self.push(plan["right"], right)
                plan["condition"] = combine(remaining)
                self.log("predicate_pushdown", "Apply single-input ON conditions before the INNER JOIN, reducing its candidate rows.", before, generate_code(plan))
        return plan

    def prune(self, plan, required):
        kind = plan["type"]
        if kind == "Project":
            self.prune(plan["input"], {c["source"] for c in plan["columns"]})
        elif kind == "Filter":
            self.prune(plan["input"], required | refs(plan["condition"]))
        elif kind == "Sort":
            self.prune(plan["input"], required | {k["column"]["name"] for k in plan["keys"]})
        elif kind == "Join":
            needed = required | refs(plan["condition"])
            for side in ("left", "right"):
                used = aliases(plan[side])
                self.prune(plan[side], {c for c in needed if c.split(".")[0] in used})
        elif kind == "TableScan":
            before = generate_code(plan)
            schema = self.tables[plan["table"]].schema
            keep = [c for c in schema if plan["alias"] + "." + c in required]
            if len(keep) < len(schema):
                plan["columns"] = keep
                self.log("projection_pruning", f"Scan {plan['alias']} carries {len(keep)} of {len(schema)} columns; filter/join/sort columns are retained. This reduces materialized cells, even when the row-based cost estimate stays unchanged.", before, generate_code(plan))
        elif "input" in plan:
            self.prune(plan["input"], required)

class CostOptimizer:
    """Costs are work units, NOT milliseconds. Index setup is paid at import."""
    def optimize(self, plan, tables, *, choose_alternatives=True):
        self.choose_alternatives = choose_alternatives
        self.tables, self.decisions = tables, []
        self.alias_tables = {}
        def register(node):
            if node["type"] == "TableScan":
                self.alias_tables[node["alias"]] = tables[node["table"]]
            for key in ("input", "left", "right"):
                if key in node:
                    register(node[key])
        register(plan)
        physical = self.visit(deepcopy(plan))
        return physical, self.decisions

    def decorate(self, node, rows, cost):
        node["estimated_rows"] = round(max(0, rows), 3)
        node["estimated_cost"] = round(max(0, cost), 3)
        return node

    def selectivity(self, condition):
        kind = condition["type"]
        if not refs(condition):
            return 1.0 if evaluate(condition, {}) is True else 0.0
        if kind == "Logical":
            a, b = self.selectivity(condition["left"]), self.selectivity(condition["right"])
            return a * b if condition["operator"] == "AND" else a + b - a * b
        # Single-column distributions include NULL. Multi-column correlation is not modeled.
        columns = refs(condition)
        if len(columns) == 1:
            name = next(iter(columns))
            alias, col = name.split(".")
            table = self.alias_tables[alias]
            stat = table.stats[col]
            count = sum(n for value, n in stat["counts"].items() if evaluate(condition, {name: value}) is True)
            if stat["nulls"] and evaluate(condition, {name: None}) is True:
                count += stat["nulls"]
            return count / len(table.rows) if table.rows else 0.0
        return 0.33

    def lookup_candidates(self, condition, scan):
        table = self.tables[scan["table"]]
        candidates = []
        for term in split(condition):
            if term["type"] != "Comparison" or term["operator"] != "=":
                continue
            a, b = term["left"], term["right"]
            if a["type"] == "Literal" and b["type"] == "Column":
                a, b = b, a
            if a["type"] != "Column" or b["type"] != "Literal" or b["value"] is None:
                continue
            alias, col = a["name"].split(".")
            if alias != scan["alias"] or col not in table.indexes:
                continue
            hits = table.stats[col]["counts"].get(b["value"], 0)
            candidates.append({"type": "IndexLookup", "index_column": col, "lookup_value": b["value"],
                               "estimated_rows": hits, "access_cost": 2 + hits * 1.2})
        return candidates

    def equality_key(self, condition, left, right):
        for term in split(condition):
            if term["type"] != "Comparison" or term["operator"] != "=":
                continue
            a, b = term["left"], term["right"]
            if a["type"] != "Column" or b["type"] != "Column":
                continue
            if a["name"].split(".")[0] in aliases(right):
                a, b = b, a
            if a["name"].split(".")[0] in aliases(left) and b["name"].split(".")[0] in aliases(right):
                return a["name"], b["name"]
        return None

    def visit(self, plan):
        kind = plan["type"]
        if kind == "Limit" and plan["count"] == 0:
            # The baseline executor already short-circuits LIMIT 0 as well.
            return self.decorate(plan, 0, 0)
        if kind == "EmptyResult":
            return self.decorate(plan, 0, 0)
        if kind == "TableScan":
            plan["type"] = "SequentialScan"
            n = len(self.tables[plan["table"]].rows)
            self.decisions.append({'kind':'access_path', 'table':plan['table'], 'alias':plan['alias'],
                'alternatives':[{'type':'SequentialScan','estimated_rows':n,'access_cost':n}],
                'chosen':'SequentialScan','chosen_candidate':0,
                'explanation':'No eligible equality lookup predicate; read the table sequentially.'})
            return self.decorate(plan, n, n)
        if kind == "Filter" and plan["input"]["type"] == "TableScan":
            scan = plan["input"]
            n = len(self.tables[scan["table"]].rows)
            alternatives = [{"type": "SequentialScan", "estimated_rows": n, "access_cost": n}]
            alternatives += self.lookup_candidates(plan["condition"], scan)
            winner = min(alternatives, key=lambda a: a["access_cost"]) if self.choose_alternatives else alternatives[0]
            physical_scan = {**scan, "type": winner["type"]}
            if winner["type"] == "IndexLookup":
                physical_scan.update({k: winner[k] for k in ("index_column", "lookup_value")})
            plan["input"] = self.decorate(physical_scan, winner["estimated_rows"], winner["access_cost"])
            self.decisions.append({"kind": "access_path", "table": scan["table"], "alias": scan["alias"],
                                   "alternatives": alternatives, "chosen": winner["type"], "chosen_candidate": alternatives.index(winner),
                                   "explanation": "Minimum estimated access work; retained Filter checks the full predicate. Hash index setup is excluded from query cost and reported at import."})
            rows = min(winner["estimated_rows"], n * self.selectivity(plan["condition"]))
            return self.decorate(plan, rows, winner["access_cost"] + winner["estimated_rows"] * 0.25)
        if kind == "Join":
            left, right = self.visit(plan["left"]), self.visit(plan["right"])
            l, r = left["estimated_rows"], right["estimated_rows"]
            key = self.equality_key(plan["condition"], left, right)
            output = l * r * 0.33
            if key:
                distinct = []
                for name in key:
                    alias, col = name.split(".")
                    distinct.append(self.alias_tables[alias].stats[col]["distinct"])
                output = l * r / max(1, *distinct)
            alternatives = [{"type": "NestedLoopJoin", "join_cost": l * r}]
            if key:
                import math
                alternatives.append({"type": "HashJoin", "join_cost": 4 + l + r + output * 0.25 + output * math.log2(max(1, output)) * 0.1})
            winner = min(alternatives, key=lambda a: a["join_cost"]) if self.choose_alternatives else alternatives[0]
            plan.update({"type": winner["type"], "left": left, "right": right})
            if winner["type"] == "HashJoin":
                plan.update({"left_key": key[0], "right_key": key[1], "build_side": "left" if l <= r else "right"})
            self.decisions.append({"kind": "join_algorithm", "alternatives": alternatives, "chosen": winner["type"], "chosen_candidate": alternatives.index(winner),
                                   "explanation": "Compared pair checks with hash build/probe work; hash builds on the smaller estimated input. SQL join order is preserved."})
            return self.decorate(plan, output, left["estimated_cost"] + right["estimated_cost"] + winner["join_cost"])
        child = self.visit(plan["input"])
        plan["input"] = child
        n, cost = child["estimated_rows"], child["estimated_cost"]
        if kind == "Filter":
            return self.decorate(plan, n * self.selectivity(plan["condition"]), cost + n * 0.25)
        if kind == "Sort":
            import math
            cost += n * math.log2(max(1, n))
        elif kind == "Project":
            cost += n * len(plan["columns"]) * 0.05
        elif kind == "Distinct":
            cost += n
        elif kind == "Limit":
            n = min(n, plan["count"])
        return self.decorate(plan, n, cost)
