from copy import deepcopy
from .catalog import value_type
from .errors import CompilerError

def analyze(ast, tables):
    ast = deepcopy(ast)
    scope = {}

    def register(table):
        name, alias = table["name"], table["alias"]
        if name not in tables:
            raise CompilerError("semantic", f"Table '{name}' does not exist in this dataset.")
        if alias in scope:
            raise CompilerError("semantic", f"Duplicate table alias '{alias}'.")
        scope[alias] = tables[name].schema

    def bind(node):
        kind = node["type"]
        if kind == "Column":
            name = node["name"]
            if "." in name:
                alias, col = name.split(".")
                if alias not in scope or col not in scope[alias]:
                    raise CompilerError("semantic", f"Unknown column '{name}'.")
            else:
                matches = [a for a, schema in scope.items() if name in schema]
                if len(matches) != 1:
                    raise CompilerError("semantic", f"Column '{name}' is {'ambiguous; qualify it with an alias' if matches else 'unknown'}.")
                alias, col = matches[0], name
            node["name"] = alias + "." + col
            return scope[alias][col]
        if kind == "Literal":
            return value_type(node["value"])
        if kind == "IsNull":
            bind(node["operand"])
            return "BOOLEAN"
        if kind == "Not":
            if bind(node["condition"]) not in {"BOOLEAN", "NULL"}:
                raise CompilerError("semantic", "NOT requires a Boolean condition.")
            return "BOOLEAN"
        left, right = bind(node["left"]), bind(node["right"])
        if kind == "Arithmetic":
            if left not in {"NUMBER", "NULL"} or right not in {"NUMBER", "NULL"}:
                raise CompilerError("semantic", "Arithmetic requires numeric operands.")
            return "NUMBER"
        if kind == "Comparison":
            if left != right and "NULL" not in {left, right}:
                raise CompilerError("semantic", f"Cannot compare {left} and {right}; no implicit string conversion.")
            if "BOOLEAN" in {left, right} and node["operator"] not in {"=", "!=", "<>"}:
                raise CompilerError("semantic", "Boolean ordering is unsupported.")
            return "BOOLEAN"
        if kind == "Logical":
            if left not in {"BOOLEAN", "NULL"} or right not in {"BOOLEAN", "NULL"}:
                raise CompilerError("semantic", "AND/OR require Boolean conditions.")
            return "BOOLEAN"
        raise CompilerError("semantic", f"Unsupported expression {kind}.")

    register(ast["from"])
    for join in ast["joins"]:
        register(join["table"])
        if bind(join["condition"]) not in {"BOOLEAN", "NULL"}:
            raise CompilerError("semantic", "ON requires a Boolean condition.")
    outputs = []
    for col in ast["columns"]:
        if col["type"] == "Wildcard":
            for alias, schema in scope.items():
                for name, data_type in schema.items():
                    outputs.append({"name": name if len(scope) == 1 else alias + "." + name,
                                    "source": alias + "." + name, "data_type": data_type})
        else:
            label = col["name"]
            data_type = bind(col)
            outputs.append({"name": label, "source": col["name"], "data_type": data_type})
    if ast["where"] is not None and bind(ast["where"]) not in {"BOOLEAN", "NULL"}:
        raise CompilerError("semantic", "WHERE requires a Boolean condition.")
    for item in ast["order_by"]:
        bind(item["column"])
        if ast["distinct"] and item["column"]["name"] not in {o["source"] for o in outputs}:
            raise CompilerError("semantic", "With DISTINCT, ORDER BY columns must appear in SELECT.")
    return ast, outputs

def make_plan(ast, outputs):
    def scan(table):
        return {"type": "TableScan", "table": table["name"], "alias": table["alias"]}
    plan = scan(ast["from"])
    for join in ast["joins"]:
        plan = {"type": "Join", "left": plan, "right": scan(join["table"]), "condition": deepcopy(join["condition"])}
    if ast["where"] is not None:
        plan = {"type": "Filter", "condition": deepcopy(ast["where"]), "input": plan}
    if ast["order_by"]:
        plan = {"type": "Sort", "keys": deepcopy(ast["order_by"]), "input": plan}
    plan = {"type": "Project", "columns": deepcopy(outputs), "input": plan}
    if ast["distinct"]:
        plan = {"type": "Distinct", "input": plan}
    if ast["limit"] is not None:
        plan = {"type": "Limit", "count": ast["limit"], "input": plan}
    return plan
