"""Run with: python run.py (no pip install, no virtual environment required)."""
import argparse
import uuid
from collections import OrderedDict
from compiler.journey import Journey
import json
import logging
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from compiler.catalog import Catalog
from compiler.errors import CompilerError
from compiler.pipeline import compile_query

ROOT = Path(__file__).resolve().parent
MAX_BODY = 8 * 1024 * 1024

API_HELP = {
    "POST /api/runs": {"dataset_id":"optional for syntax", "query":"SELECT name FROM students;", "mode":"execute | compile | syntax"},
    "POST /api/next": {"run_id":"returned by /api/runs"},
    "GET /api/datasets": "List saved datasets, tables, inferred types, statistics and previews.",
    "POST /api/datasets": {"name": "My project data"},
    "POST /api/tables": {"dataset_id": "returned_id", "name": "students", "csv_text": "id,name,age\n1,PersonA,20\n", "indexed_columns": ["id"], "schema": "Optional full mapping, e.g. {id: NUMBER, name: STRING, age: NUMBER}; omit to infer."},
    "POST /api/tables (JSON alternative)": {"dataset_id": "returned_id", "name": "students", "rows": [{"id": 1, "name": "PersonA", "age": 20}], "indexed_columns": ["id"]},
    "POST /api/indexes": {"dataset_id": "returned_id", "table": "students", "columns": ["id", "city"]},
    "POST /api/import/sqlite?dataset_id=returned_id": "Raw .sqlite/.db bytes; Content-Type application/octet-stream. Ordinary tables imported atomically, existing names rejected.",
    "POST /api/compile": {"dataset_id": "returned_id", "query": "SELECT name FROM students WHERE id = 1;", "execute": True, "verify": True, "result_limit": 1000},
    "POST /api/demo": "Create a new example dataset: 200 students and 4 departments.",
    "GET /api/health": "Server readiness."
}

def create_demo(catalog):
    key = catalog.create("Example college dataset")
    students = [{"id": i, "name": f"Student {i}", "age": 17 + i % 10, "city": "Mumbai" if i % 2 else "Pune",
                 "marks": 50 + i % 51, "active": bool(i % 2), "department_id": 1 + i % 4} for i in range(1, 201)]
    catalog.add_json(key, "students", students, indexed=["id", "city", "department_id"])
    catalog.add_json(key, "departments", [{"id": i, "name": n} for i, n in enumerate(["CSBS", "Computer", "IT", "Mechanical"], 1)], indexed=["id"])
    return key

def handler_class(catalog):
    runs = OrderedDict()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

        def reply(self, status, body, content_type="application/json; charset=utf-8"):
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if isinstance(body, (dict, list)) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def validate_origin(self):
            port = self.server.server_port
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in hosts:
                raise CompilerError("request", "Use the localhost URL printed by the server.")
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in hosts}:
                raise CompilerError("request", "Cross-origin requests are disabled. Serve the future frontend on this same origin.")

        def read_body(self, json_body=True):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise CompilerError("request", "Invalid Content-Length.")
            if not 0 < length <= MAX_BODY:
                raise CompilerError("request", "Request must be between 1 byte and 8 MiB.")
            content_type = self.headers.get("Content-Type", "").split(";")[0]
            if json_body and content_type != "application/json":
                raise CompilerError("request", "Use Content-Type: application/json.")
            if not json_body and content_type != "application/octet-stream":
                raise CompilerError("request", "SQLite upload requires application/octet-stream.")
            self.connection.settimeout(30)
            body = self.rfile.read(length)
            if len(body) != length:
                raise CompilerError("request", "Incomplete request body.")
            if not json_body:
                return body
            try:
                value = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                raise CompilerError("request", "Invalid JSON.")
            if not isinstance(value, dict):
                raise CompilerError("request", "JSON body must be an object.")
            return value

        def do_GET(self):
            try:
                self.validate_origin()
                path = urlparse(self.path).path
                with catalog.lock:
                    if path == "/api/health":
                        return self.reply(200, {"status": "ok", "version": "1.0", "storage": "persistent", "mode": "local educational backend"})
                    if path == "/api/datasets":
                        return self.reply(200, {"datasets": catalog.list()})
                    if path == "/api/docs":
                        return self.reply(200, API_HELP)
                if path in {"/", "/index.html"}:
                    return self.reply(200, (ROOT / "frontend" / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path in {'/app.js','/style.css','/tree.js','/teaching.js'}:
                    mime = 'text/css' if path.endswith('.css') else 'text/javascript'
                    return self.reply(200, (ROOT / 'frontend' / path[1:]).read_bytes(), mime+'; charset=utf-8')
                self.reply(404, {"error": {"phase": "request", "message": "Route not found."}})
            except CompilerError as exc:
                self.reply(400, {"error": exc.detail()})

        def do_POST(self):
            try:
                self.validate_origin()
                parsed = urlparse(self.path)
                path = parsed.path
                with catalog.lock:
                    if path == "/api/import/sqlite":
                        key = parse_qs(parsed.query).get("dataset_id", [""])[0]
                        catalog.get(key)
                        body = self.read_body(False)
                        if not body.startswith(b"SQLite format 3\x00"):
                            raise CompilerError("import", "File is not a SQLite database.")
                        # Close before reopening on Windows.
                        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as temp:
                            temp.write(body)
                            temp_path = Path(temp.name)
                        try:
                            imported = catalog.import_sqlite(key, temp_path)
                        finally:
                            temp_path.unlink(missing_ok=True)
                        return self.reply(201, {"tables": imported})
                    data = self.read_body()
                    if path == "/api/datasets":
                        return self.reply(201, {"dataset_id": catalog.create(data.get("name"))})
                    if path == "/api/demo":
                        return self.reply(201, {"dataset_id": create_demo(catalog)})
                    if path == '/api/runs':
                        mode = data.get('mode', 'execute')
                        tables = {} if mode == 'syntax' else catalog.get(data.get('dataset_id'))['tables']
                        run_id = uuid.uuid4().hex
                        runs[run_id] = Journey(tables, data.get('query'), mode)
                        while len(runs) > 50: runs.popitem(last=False)
                        return self.reply(201, {'run_id': run_id})
                    if path == '/api/next':
                        run = runs.get(data.get('run_id'))
                        if run is None: raise CompilerError('request', 'Run expired. Start again from the editor.')
                        return self.reply(200, run.advance())
                    key = data.get("dataset_id")
                    if not isinstance(key, str):
                        raise CompilerError("request", "dataset_id must be a string.")
                    ds = catalog.get(key)
                    if path == "/api/tables":
                        if ("csv_text" in data) == ("rows" in data):
                            raise CompilerError("import", "Provide exactly one of csv_text or rows.")
                        if "csv_text" in data:
                            table = catalog.add_csv(key, data.get("name"), data["csv_text"], data.get("schema"), data.get("indexed_columns"))
                        else:
                            table = catalog.add_json(key, data.get("name"), data["rows"], data.get("schema"), data.get("indexed_columns"))
                        return self.reply(201, {"table": table})
                    if path == "/api/indexes":
                        table = catalog.set_indexes(key, data.get("table"), data.get("columns"))
                        return self.reply(200, {"table": table})
                    if path == "/api/compile":
                        report = compile_query(ds["tables"], data.get("query"), verify=data.get("verify", True), execute=data.get("execute", True), result_limit=data.get("result_limit", 1000))
                        return self.reply(200, report)
                    self.reply(404, {"error": {"phase": "request", "message": "Route not found."}})
            except CompilerError as exc:
                self.reply(400, {"error": exc.detail()})
            except RecursionError:
                self.reply(400, {"error": {"phase": "syntax", "message": "Query nesting is too deep."}})
            except (OSError, TimeoutError):
                self.reply(500, {"error": {"phase": "storage", "message": "Cannot read/save data or request timed out."}})
            except Exception:
                logging.exception("Unexpected backend failure")
                self.reply(500, {"error": {"phase": "internal", "message": "Unexpected error; see server terminal."}})
    return Handler

def main():
    parser = argparse.ArgumentParser(description="Advanced Optimizing SQL Compiler backend")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    catalog = Catalog(args.data_dir / "catalog.json")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_class(catalog))
    except OSError as exc:
        print(f"Cannot start: {exc}. Try: python run.py --port 8001")
        return
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"\nSQL compiler backend ready: {url}\nAPI reference: {url}/api/docs\nStop with Ctrl+C.\n", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped. Your imported datasets are saved.")
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
