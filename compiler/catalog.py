"""Persistent user datasets, schema inference, statistics and real hash indexes."""
import csv
import io
import json
import math
import re
import sqlite3
import threading
import uuid
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from time import perf_counter
from .errors import CompilerError
from .syntax import KEYWORDS

MAX_ROWS, MAX_COLUMNS, MAX_TABLES = 30000, 64, 20
TYPES = {"NUMBER", "STRING", "BOOLEAN"}

def identifier(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,62}", name) or name.upper() in KEYWORDS:
        raise CompilerError("import", "Names must be unquoted identifiers (letters, digits, underscores), not SQL keywords.")
    return name.lower()

def value_type(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, (int, float)):
        try:
            if math.isfinite(value):
                return "NUMBER"
        except OverflowError:
            pass
    if isinstance(value, str):
        return "STRING"
    raise CompilerError("import", "Cells must contain finite numbers, strings, Booleans or null; nested objects and binary cells are unsupported.")

def cast_cell(text, kind):
    if text == "":
        return None
    if kind == "STRING":
        return text
    if kind == "BOOLEAN":
        if text.strip().lower() not in {"true", "false"}:
            raise ValueError("Expected true/false")
        return text.strip().lower() == "true"
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("Expected a finite number")
    return int(text) if re.fullmatch(r"[+-]?\d+", text.strip()) else number

def infer_csv_type(values):
    nonempty = [v for v in values if v != ""]
    if nonempty and all(v.strip().lower() in {"true", "false"} for v in nonempty):
        return "BOOLEAN"
    try:
        if nonempty:
            for v in nonempty:
                cast_cell(v, "NUMBER")
            return "NUMBER"
    except (ValueError, OverflowError):
        pass
    return "STRING"

class Table:
    def __init__(self, schema, rows, indexed=()):
        self.schema, self.rows = schema, rows
        self.indexed = list(indexed)
        self.stats, self.indexes = {}, {}
        started = perf_counter()
        for col in schema:
            counts = Counter(row[col] for row in rows if row[col] is not None)
            self.stats[col] = {"counts": counts, "distinct": len(counts),
                               "nulls": sum(row[col] is None for row in rows)}
        for col in indexed:
            buckets = defaultdict(list)
            for position, row in enumerate(rows):
                if row[col] is not None:
                    buckets[row[col]].append(position)
            self.indexes[col] = dict(buckets)
        self.setup_ms = round((perf_counter() - started) * 1000, 3)

    def stored(self):
        return {"schema": self.schema, "rows": self.rows, "indexed_columns": self.indexed}

    def describe(self, name):
        return {"name": name, "row_count": len(self.rows), "schema": self.schema,
                "indexed_columns": self.indexed, "setup_ms": self.setup_ms,
                "preview": self.rows[:10], "statistics": {
                    col: {"distinct_count": stat["distinct"], "null_count": stat["nulls"]}
                    for col, stat in self.stats.items()}}

def make_table(schema, rows, indexed):
    if not isinstance(schema, dict) or not 1 <= len(schema) <= MAX_COLUMNS:
        raise CompilerError("import", f"Provide 1–{MAX_COLUMNS} columns.")
    normalized = {}
    for name, kind in schema.items():
        col = identifier(name)
        if col in normalized or not isinstance(kind, str) or kind not in TYPES:
            raise CompilerError("import", "Duplicate column or invalid type. Types: NUMBER, STRING, BOOLEAN.")
        normalized[col] = kind
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise CompilerError("import", f"Provide at most {MAX_ROWS} rows per table.")
    converted = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CompilerError("import", "JSON rows must be objects.")
        mapped = {identifier(k): v for k, v in row.items()}
        if len(mapped) != len(row) or set(mapped) != set(normalized):
            raise CompilerError("import", f"Row {i + 1} must contain exactly the schema columns.")
        for col, value in mapped.items():
            if value_type(value) not in {"NULL", normalized[col]}:
                raise CompilerError("import", f"Row {i + 1}, column {col}: expected {normalized[col]}.")
        converted.append(mapped)
    if not isinstance(indexed, list):
        raise CompilerError("import", "indexed_columns must be a list.")
    indexed = list(dict.fromkeys(identifier(c) for c in indexed))
    if not set(indexed).issubset(normalized):
        raise CompilerError("import", "An indexed column does not exist.")
    return Table(normalized, converted, indexed)

class Catalog:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.datasets = {}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for key, data in saved.items():
                self.datasets[key] = {"name": data["name"], "tables": {
                    name: make_table(t["schema"], t["rows"], t["indexed_columns"])
                    for name, t in data["tables"].items()}}

    def save(self):
        data = {key: {"name": ds["name"], "tables": {n: t.stored() for n, t in ds["tables"].items()}}
                for key, ds in self.datasets.items()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(self.path)

    def create(self, name):
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise CompilerError("import", "Dataset name must contain 1–100 characters.")
        key = uuid.uuid4().hex
        with self.lock:
            self.datasets[key] = {"name": name.strip(), "tables": {}}
            self.save()
        return key

    def get(self, key):
        if key not in self.datasets:
            raise CompilerError("catalog", "Dataset not found. Create or select a dataset first.")
        return self.datasets[key]

    def list(self):
        return [{"id": key, "name": ds["name"], "tables": [t.describe(n) for n, t in ds["tables"].items()]}
                for key, ds in self.datasets.items()]

    def add_json(self, key, name, rows, schema=None, indexed=None):
        name = identifier(name)
        if not isinstance(rows, list):
            raise CompilerError("import", "rows must be a list of objects.")
        if schema is None:
            if not rows or not isinstance(rows[0], dict):
                raise CompilerError("import", "An empty table requires an explicit schema.")
            schema = {}
            for col in rows[0]:
                kinds = {value_type(row.get(col)) for row in rows if isinstance(row, dict)} - {"NULL"}
                if len(kinds) > 1:
                    raise CompilerError("import", f"Mixed types in {col}; provide consistent typed values.")
                schema[col] = next(iter(kinds), "STRING")
        table = make_table(schema, rows, indexed if indexed is not None else [])
        self.add_tables(key, {name: table})
        return table.describe(name)

    def add_tables(self, key, tables):
        ds = self.get(key)
        if set(tables) & set(ds["tables"]):
            raise CompilerError("import", "A table with that name already exists. Use a new name or dataset.")
        if len(ds["tables"]) + len(tables) > MAX_TABLES:
            raise CompilerError("import", f"Maximum {MAX_TABLES} tables per dataset.")
        ds["tables"].update(tables)
        try:
            self.save()
        except OSError:
            for name in tables:
                ds["tables"].pop(name)
            raise

    def add_csv(self, key, name, text, schema=None, indexed=None):
        if not isinstance(text, str):
            raise CompilerError("import", "csv_text must be text.")
        reader = csv.reader(io.StringIO(text.lstrip('\ufeff')))
        try:
            headers = next(reader)
            columns = [identifier(h.strip()) for h in headers]
            if len(set(columns)) != len(columns):
                raise CompilerError("import", "Duplicate CSV headers.")
            values = []
            for row in reader:
                if not row:
                    continue
                if len(row) != len(columns):
                    raise CompilerError("import", "CSV row length does not match its header.")
                values.append(row)
                if len(values) > MAX_ROWS:
                    raise CompilerError("import", f"CSV exceeds {MAX_ROWS} rows.")
        except (StopIteration, csv.Error) as exc:
            raise CompilerError("import", "Invalid or empty CSV.") from exc
        inferred = {c: infer_csv_type([row[i] for row in values]) for i, c in enumerate(columns)}
        if schema is not None:
            if not isinstance(schema, dict):
                raise CompilerError("import", "schema must be an object.")
            supplied = {identifier(c): k for c, k in schema.items()}
            if set(supplied) != set(columns) or not set(supplied.values()).issubset(TYPES):
                raise CompilerError("import", "Explicit schema must match every CSV header and use valid types.")
            inferred = supplied
        try:
            rows = [{c: cast_cell(row[i], inferred[c]) for i, c in enumerate(columns)} for row in values]
        except (ValueError, OverflowError) as exc:
            raise CompilerError("import", "A CSV cell cannot be converted to the specified type.") from exc
        return self.add_json(key, name, rows, inferred, indexed)

    def set_indexes(self, key, name, columns):
        name = identifier(name)
        ds = self.get(key)
        if name not in ds["tables"]:
            raise CompilerError("catalog", "Table not found.")
        old = ds["tables"][name]
        new = make_table(old.schema, old.rows, columns)
        ds["tables"][name] = new
        self.save()
        return new.describe(name)

    def import_sqlite(self, key, source_path):
        # SQLite is used ONLY to read uploaded source tables, not to run compiled SQL.
        tables = {}
        try:
            with closing(sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)) as con:
                con.execute("PRAGMA query_only=ON")
                names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                if not names or len(names) > MAX_TABLES:
                    raise CompilerError("import", f"SQLite file must have 1–{MAX_TABLES} ordinary tables.")
                for raw_name in names:
                    name = identifier(raw_name)
                    quoted = '"' + raw_name.replace('"', '""') + '"'
                    info = list(con.execute(f"PRAGMA table_info({quoted})"))
                    schema = {}
                    for col in info:
                        kind = col[2].upper()
                        schema[identifier(col[1])] = ("BOOLEAN" if "BOOL" in kind else "NUMBER" if any(t in kind for t in ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")) else "STRING")
                    cursor = con.execute(f"SELECT * FROM {quoted}")
                    data = cursor.fetchmany(MAX_ROWS + 1)
                    rows = []
                    for row in data:
                        mapped = dict(zip(schema, row))
                        for col, value in mapped.items():
                            if schema[col] == "BOOLEAN" and value in (0, 1) and value is not None:
                                mapped[col] = bool(value)
                        rows.append(mapped)
                    if name in tables:
                        raise CompilerError("import", "Case-insensitive table names collide.")
                    tables[name] = make_table(schema, rows, [c for c in schema if c == "id"])
        except sqlite3.Error as exc:
            raise CompilerError("import", "Cannot read this SQLite database.") from exc
        self.add_tables(key, tables)
        return [t.describe(n) for n, t in tables.items()]
