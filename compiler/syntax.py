"""Own lexer and recursive-descent parser; never delegates queries to SQLite."""
import re
from .errors import CompilerError

KEYWORDS = set("SELECT DISTINCT FROM WHERE AND OR NOT AS INNER JOIN ON ORDER BY ASC DESC LIMIT IS NULL TRUE FALSE".split())
TOKEN_PATTERN = re.compile(r"(?P<SPACE>\s+)|(?P<COMMENT>--[^\n]*)|(?P<NUMBER>\d+(?:\.\d+)?)|(?P<IDENTIFIER>[A-Za-z_][A-Za-z_0-9]*)|(?P<OPERATOR><=|>=|<>|!=|[=<>+*/%-])|(?P<PUNCTUATION>[(),.;])")

def tokenize(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 20000:
        raise CompilerError("lexical", "Query must be nonblank text of at most 20,000 characters.")
    tokens, pos, line, column = [], 0, 1, 1
    while pos < len(sql):
        start_line, start_column = line, column
        if sql.startswith("/*", pos):
            end = sql.find("*/", pos + 2)
            if end < 0:
                raise CompilerError("lexical", "Unclosed block comment.", line, column)
            value, kind = sql[pos:end + 2], "COMMENT"
        elif sql[pos] == "'":
            end = pos + 1
            while end < len(sql):
                if sql[end] == "'":
                    if end + 1 < len(sql) and sql[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            else:
                raise CompilerError("lexical", "Unclosed string.", line, column)
            value, kind = sql[pos:end], "STRING"
        else:
            match = TOKEN_PATTERN.match(sql, pos)
            if not match:
                raise CompilerError("lexical", f"Unsupported character {sql[pos]!r}.", line, column)
            value, kind = match.group(), match.lastgroup
            if kind == "IDENTIFIER" and value.upper() in KEYWORDS:
                kind = "KEYWORD"
        if kind not in {"SPACE", "COMMENT"}:
            tokens.append({"type": kind, "value": value, "line": start_line, "column": start_column})
        if "\n" in value:
            line += value.count("\n")
            column = len(value.rsplit("\n", 1)[1]) + 1
        else:
            column += len(value)
        pos += len(value)
    tokens.append({"type": "EOF", "value": "", "line": line, "column": column})
    return tokens

def literal(value):
    return {"type": "Literal", "value": value}

def binary(kind, op, left, right):
    return {"type": kind, "operator": op, "left": left, "right": right}

class Parser:
    def __init__(self, tokens):
        self.tokens, self.pos = tokens, 0

    def current(self):
        return self.tokens[self.pos]

    def error(self, message):
        token = self.current()
        raise CompilerError("syntax", message + f" Found {token['value'] or 'end of input'!r}.", token["line"], token["column"])

    def accept(self, text):
        t = self.current()
        if (t["value"].upper() if t["type"] == "KEYWORD" else t["value"]) == text:
            self.pos += 1
            return True
        return False

    def expect(self, text):
        if not self.accept(text):
            self.error(f"Expected {text}.")

    def name(self):
        if self.current()["type"] != "IDENTIFIER":
            self.error("Expected an unquoted identifier.")
        value = self.current()["value"].lower()
        self.pos += 1
        return value

    def column(self):
        name = self.name()
        if self.accept("."):
            name += "." + self.name()
        return {"type": "Column", "name": name}

    def table(self):
        name = self.name()
        alias = name
        if self.accept("AS"):
            alias = self.name()
        elif self.current()["type"] == "IDENTIFIER":
            alias = self.name()
        return {"type": "Table", "name": name, "alias": alias}

    def parse(self):
        self.expect("SELECT")
        distinct = self.accept("DISTINCT")
        columns = [{"type": "Wildcard"}] if self.accept("*") else [self.column()]
        if columns[0]["type"] != "Wildcard":
            while self.accept(","):
                columns.append(self.column())
        self.expect("FROM")
        source, joins = self.table(), []
        while self.current()["value"].upper() in {"INNER", "JOIN"}:
            self.accept("INNER")
            self.expect("JOIN")
            table = self.table()
            self.expect("ON")
            joins.append({"table": table, "condition": self.parse_or()})
        where = self.parse_or() if self.accept("WHERE") else None
        order = []
        if self.accept("ORDER"):
            self.expect("BY")
            while True:
                col = self.column()
                direction = "DESC" if self.accept("DESC") else "ASC"
                if direction == "ASC":
                    self.accept("ASC")
                order.append({"column": col, "direction": direction})
                if not self.accept(","):
                    break
        limit = None
        if self.accept("LIMIT"):
            token = self.current()
            if token["type"] != "NUMBER" or "." in token["value"]:
                self.error("LIMIT requires a nonnegative integer.")
            limit = int(token["value"])
            self.pos += 1
        self.accept(";")
        if self.current()["type"] != "EOF":
            self.error("Unsupported syntax or extra statement.")
        return {"type": "SelectQuery", "columns": columns, "from": source,
                "joins": joins, "where": where, "distinct": distinct, "order_by": order, "limit": limit}

    def parse_or(self):
        left = self.parse_and()
        while self.accept("OR"):
            left = binary("Logical", "OR", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.accept("AND"):
            left = binary("Logical", "AND", left, self.parse_not())
        return left

    def parse_not(self):
        if self.accept("NOT"):
            return {"type": "Not", "condition": self.parse_not()}
        # Parentheses may enclose a condition or an arithmetic operand.
        saved = self.pos
        if self.accept("("):
            try:
                node = self.parse_or()
                self.expect(")")
                if self.current()["value"] in {"+", "-", "*", "/", "%", "=", "!=", "<>", "<", ">", "<=", ">="} or self.current()["value"].upper() == "IS":
                    self.pos = saved
                else:
                    return node
            except CompilerError:
                self.pos = saved
        left = self.additive()
        if self.accept("IS"):
            negate = self.accept("NOT")
            self.expect("NULL")
            return {"type": "IsNull", "operand": left, "negate": negate}
        t = self.current()
        if t["value"] in {"=", "!=", "<>", "<", ">", "<=", ">="}:
            self.pos += 1
            return binary("Comparison", t["value"], left, self.additive())
        return left  # Semantic analysis requires a Boolean WHERE/ON.

    def additive(self):
        node = self.multiplicative()
        while self.current()["value"] in {"+", "-"}:
            op = self.current()["value"]
            self.pos += 1
            node = binary("Arithmetic", op, node, self.multiplicative())
        return node

    def multiplicative(self):
        node = self.atom()
        while self.current()["value"] in {"*", "/", "%"}:
            op = self.current()["value"]
            self.pos += 1
            node = binary("Arithmetic", op, node, self.atom())
        return node

    def atom(self):
        if self.accept("-"):
            return binary("Arithmetic", "-", literal(0), self.atom())
        if self.accept("+"):
            return self.atom()
        if self.accept("("):
            node = self.additive()
            self.expect(")")
            return node
        t = self.current()
        if t["type"] == "NUMBER":
            self.pos += 1
            return literal(float(t["value"]) if "." in t["value"] else int(t["value"]))
        if t["type"] == "STRING":
            self.pos += 1
            return literal(t["value"][1:-1].replace("''", "'"))
        if t["value"].upper() in {"NULL", "TRUE", "FALSE"}:
            self.pos += 1
            return literal({"NULL": None, "TRUE": True, "FALSE": False}[t["value"].upper()])
        if t["type"] == "IDENTIFIER":
            return self.column()
        self.error("Expected a column, literal or expression.")
