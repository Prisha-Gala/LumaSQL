# LumaSQL — Advanced Optimizing Compiler for SQL

A complete local frontend and Python backend for an educational SQL compiler.
The UI is original: a cream, lilac, mint and peach workspace with a guided seven-phase journey, connected SVG trees and real optimizer comparisons.

## Version 2: simpler presentation

The logical and executable plan diagrams and their walkthrough panels have been removed from Phases 4–7. Phase 4 shows generated intermediate code; Phase 5 shows rule changes and original/optimized code; Phase 6 shows costs and physical code; Phase 7 shows results and measured work. The AST displays in Phases 2–3 remain. Internal compiler plans still support optimization, execution and correctness verification.

Every phase retains the submitted SQL and query-specific explanations. Original, rule-only and cost-selected costs are compared under the same model. Costs are work estimates, not milliseconds; column pruning or expression simplification can help even when the row-based estimate is unchanged.

Read [PROJECT_GUIDE.md](PROJECT_GUIDE.md) for a short presentation explanation and eight tested queries with expected outputs, including a missing-table error and optional customers CSV examples. Copy one statement at a time from [examples/try_queries.sql](examples/try_queries.sql). New editor buttons load departments, department lookup and missing-table examples. [VALIDATION.md](VALIDATION.md) records verification and its limits.

## Start here (Windows / VS Code)

1. Extract the ZIP using **Extract All**.
2. Open the extracted **lumasql** folder in VS Code. `run.py` must be visible directly in that folder.
3. Open **Terminal → New Terminal** and run:

```powershell
python run.py
```

Or double-click `start_windows.bat`. Python 3.10+ is required. No pip install, virtual environment, npm, database server or separate frontend command is needed. The browser opens `http://127.0.0.1:8000`. Keep the terminal running. Stop with Ctrl+C. If the port is busy: `python run.py --port 8001`.

If `python` is not recognized, install Python with its PATH option enabled, then reopen VS Code. `py run.py` is an alternative only if the Windows Python launcher is installed. Do not run `cd backend`: this project has no backend folder.

Open this as a separate project. It replaces running the older phase scripts individually; you do not need to paste those scripts into this project.

## What the user sees

1. Enter SQL, choose a sample, or import data.
2. Choose **Compile & execute**, **Compile only**, or **Check syntax only**.
3. Start the journey. The browser shows a phase-loading transition while requesting that phase from the backend.
4. Review the output, then click **Run phase …**. Completed phases remain accessible in the sidebar.
5. Review results, applied rules, costs and execution measurements. Download the run report, tree SVG or result CSV.
6. Click **Try another query** to return to the editor.

The loading transition has a minimum 550 ms duration for readability. Displayed phase timings come from Python and exclude that visual delay, network time and user reading time. Each advance really runs a new backend phase; outputs are not canned.

## The seven phases and files

| Phase / responsibility | File | What it does |
|---|---|---|
| 1. Lexical analysis | `compiler/syntax.py` | Recognizes SQL keywords, identifiers, strings, numbers and operators with source positions. |
| 2. Syntax analysis | `compiler/syntax.py` | Recursive-descent parser validates grammar and constructs an AST. |
| 3. Semantic analysis | `compiler/semantics.py` | Resolves table aliases and columns, checks types and ambiguity. |
| 4. Intermediate code (ICG) | `compiler/semantics.py` + `compiler/codegen.py` | Constructs the relational plan and emits readable instructions using temporary relations. |
| 5. Rule optimization | `compiler/optimization.py` | Applies valid equivalence-preserving rewrites. |
| 6. Cost optimization | `compiler/optimization.py` | Estimates eligible access paths and join algorithms, choosing a physical plan. |
| 7. Execution | `compiler/execution.py` | Executes our selected physical operators and collects measured work. |
| Phase orchestration | `compiler/journey.py` | Advances one phase per request, preserves completed output on errors, verifies baseline results. |
| One-call orchestration | `compiler/pipeline.py` | Existing `/api/compile` interface for all phases in one call. |
| Teaching notes and comparisons | `compiler/explanations.py` | Query-specific phase details, internal plan metadata and comparable three-stage costs. |
| Dynamic data | `compiler/catalog.py` | Imports data, infers or validates types, persists datasets, constructs indexes and column statistics. |
| Code output | `compiler/codegen.py` | Serializes original, rule-optimized and physical plans as relational three-address-style code. |
| Errors | `compiler/errors.py` | Structured phase-specific errors. |
| Local web server | `run.py` | Serves frontend, data API and compiler API on localhost. |
| Frontend markup | `frontend/index.html` | Input screen, journey screen, dialogs and accessible labels. |
| Visual design | `frontend/style.css` | Responsive layout, colors, animations and reduced-motion support. |
| Frontend behavior | `frontend/app.js` | Imports, API calls, phase navigation, costs, metrics and exports. |
| Teaching panels | `frontend/teaching.js` | Phase explanations, rule/code comparisons and cost charts. |
| Tree visualization | `frontend/tree.js` | Native SVG AST/plan layout with pan, zoom, node inspection and download. |
| Tests | `tests/` | Compiler, imports, execution, HTTP and incremental journey tests. |
| Example data | `examples/` | Small files for import demonstrations. |

SQL terms are defined as tokens/keywords and grammar in `syntax.py`. Their meaning and permitted types are checked in `semantics.py`. Relational operators are implemented in `execution.py`; their transformations and cost rules are in `optimization.py`.

## Data options

The first launch creates a sample college dataset: 200 students and 4 departments. Expand a table in **Set the scene** to see its columns and indexes. Sample queries target these tables.

**Import your data** supports:
- CSV with a header row; blank fields become NULL.
- JSON arrays of row objects.
- SQLite `.db`, `.sqlite` and `.sqlite3` files, importing tables and supported indexes.

Choose a new dataset or add a new table to an existing dataset. Use the same dataset for tables participating in a join. Imported table names must be unique in the destination. CSV/JSON types are inferred, or supply a complete mapping using NUMBER, STRING and BOOLEAN. Example: `{"id":"NUMBER","name":"STRING"}`. Specify equality-index columns during import, or use **Edit indexes** under a table afterwards.

Datasets persist in `data/catalog.json`, created on first launch. SQLite is an import format, not the query execution engine. All submitted queries execute through our Python operators.

Syntax-only mode runs phases 1–2 without tables. It cannot claim names/types are valid, estimate meaningful costs, or return data. Compile-only uses real table metadata/statistics and produces a physical plan but does not run the query.

## Intermediate and optimized code displays

Phase 4 shows **Generated intermediate code (ICG)** with no separate logical-plan diagram or walkthrough. Phase 5 compares original and rule-optimized ICG. Phase 6 compares rule-only logical code with cost-selected physical code, including selected scan/index and join operations; original ICG remains expandable. The code comparisons have copy and text-download buttons. The JSON report contains `intermediate_code`, `optimized_code`, `physical_code`, `phase_explanations`, `logical_plan_guide` and `plan_comparison`; both compiler APIs include them as the relevant phases complete.

For `SELECT name FROM students WHERE id = 42 AND 1 = 1;`, phase 4 produces:

```text
t1 = SCAN(students AS students, COLUMNS [*])
t2 = FILTER(t1, ((students.id = 42) AND (1 = 1)))
t3 = PROJECT(t2, [students.name AS name])
RETURN t3
```

Phase 5 removes the constant-true condition and prunes unnecessary columns:

```text
t1 = SCAN(students AS students, COLUMNS [id, name])
t2 = FILTER(t1, (students.id = 42))
t3 = PROJECT(t2, [students.name AS name])
RETURN t3
```

With the demo table's ID index, phase 6 changes the first instruction to:

```text
t1 = INDEX_LOOKUP(students AS students, KEY id = 42, COLUMNS [id, name])
```

Each `tN` holds a temporary relation. This is relational three-address-style IR: expressions remain inside FILTER / JOIN instructions, rather than being lowered to separate scalar temporaries. It is generated from actual plan nodes, not a fixed example. Physical code is a readable representation of the plan object executed by the backend; it is not rewritten SQL or a separate text-program interpreter. Fewer instructions alone do not imply a better plan—predicate placement, carried columns and access/join strategies matter.

## Advanced features, with honest scope

| Feature | Implementation / benefit |
|---|---|
| Constant folding | Evaluates constant arithmetic/comparisons and simplifies true/false predicates. |
| Boolean simplification | Removes redundant logical expressions while preserving SQL NULL behavior. |
| Duplicate predicate removal | Eliminates structurally identical repeated AND/OR terms. |
| Predicate pushdown | Moves suitable filters below inner joins to reduce intermediate rows. |
| Column pruning | Keeps needed output, join, filter and sort columns while avoiding unnecessary materialization. |
| Empty-plan simplification | Detects impossible predicates and LIMIT 0. |
| Statistics-based estimates | Uses row counts, distinct values, NULL counts and per-value frequencies; combined predicates use simplified assumptions. |
| Cost-based access | Compares sequential scan with eligible equality hash-index lookups. |
| Cost-based joins | Compares nested-loop pair checks with hash build/probe work for eligible equijoins. |
| Physical execution | Actually executes the selected scan/index/hash/nested-loop operators. |
| Equivalence check | Executes the original logical plan and compares full rows with optimized output before display truncation. |

Phase 5 lists rules actually used. Phase 6 lists eligible candidates, numeric costs, and the specific selected candidate. A query without a join explicitly reports that join selection does not apply. An ineligible index is not assigned an invented cost.

The simplified cost model uses work units:
- Sequential scan: N rows.
- Equality index lookup: 2 + 1.2 × matching rows.
- Filter: 0.25 × candidate rows, added to access work.
- Nested-loop join: L × R pair checks.
- Hash join: 4 + L + R + 0.25 × estimated matches + 0.1 × matches × log2(max(1, matches)). The final term accounts for restoring deterministic pair order.
- Plan costs include child costs. Local candidate costs are not directly interchangeable with whole-plan costs.

Indexes are in-memory hash buckets, not disk B-trees. Their setup time is excluded from per-query cost and available in table metadata. The optimizer keeps the SQL join order; it does not enumerate global join permutations. No production-database speedup is claimed. Measured execution times vary and tiny timings are noisy.

Final output is an **executable physical query plan plus result rows**, not a rewritten SQL string. This fulfills the query-plan branch of the project title. A separate SQL code generator is not implemented.

## Supported SQL / limits

SELECT columns or `*`, DISTINCT, FROM, table aliases, INNER JOIN / JOIN ... ON, WHERE, AND/OR/NOT, comparisons, arithmetic inside conditions, IS NULL / IS NOT NULL, ORDER BY columns ASC/DESC, and LIMIT.

No aggregates, GROUP BY, subqueries, outer joins, INSERT/UPDATE/DELETE, arbitrary SQL dialect compatibility, or SELECT arithmetic expressions/column aliases. Use unquoted SQL identifiers and single-quoted strings. Unsupported queries return errors rather than being silently executed elsewhere.

Limits: 8 MiB per request, 30,000 rows per table, 64 columns, 20 tables per dataset, bounded execution work and 100,000 intermediate rows. The UI shows up to 1,000 result rows; CSV export exports the displayed rows. Verification uses the full result. If the baseline exceeds its work limit, the UI reports that verification was skipped. The 50 newest in-memory journeys are retained; restarting the server clears journeys but keeps datasets.

Local classroom use only: this server has no multi-user authentication or production deployment setup.

## Presenting to your teacher

1. Explain the compiler pipeline, then choose **Index lookup**. Phase 2 demonstrates the actual connected AST. Phase 3 shows bound column names/types.
2. Phase 5 shows removal of `1 = 1`; phase 6 compares scanning 200 rows (cost 200) against looking up ID 42 (cost 3.2).
3. Phase 7 returns `Student 42`, marks `92`, and confirms the baseline result matches.
4. Try **Join strategy** to show predicate pushdown, column pruning, estimated join alternatives and the selected physical operator.
5. Try **Rule magic** to demonstrate duplicate predicate elimination and constant folding.
6. Import your own CSV; select its dataset and write a query for its columns. Show that the schema is dynamic.
7. Enter an invalid column to demonstrate semantic errors, then switch to syntax-only mode to explain why schema validation requires table information.

Do not promise that optimization always improves elapsed time. Explain the candidate model, actual work counters, and correctness check.

## API / development

`GET /api/docs` lists the data and one-call compiler endpoints.

Incremental frontend API:
- `POST /api/runs` with `{"dataset_id":"…","query":"SELECT …","mode":"execute"}` returns `run_id`. Modes: execute, compile, syntax. Syntax does not require dataset_id.
- `POST /api/next` with `{"run_id":"…"}` executes one phase and returns cumulative output, stages/timings, done, and any compiler error. Repeating after completion does not execute again.

Run backend tests from this folder:

```powershell
python -m unittest discover -s tests -v
```

The frontend is plain HTML/CSS/JavaScript with no CDN dependencies and works offline after Python is installed. Browser automation used during development is not required to run the app.
