# LumaSQL: project explanation and presentation guide

## What the project does

LumaSQL is an educational optimizing compiler for a supported subset of SQL SELECT queries. You provide SQL and choose a dataset. The compiler checks the query, translates it into relational operations, improves that plan, selects concrete scan/join strategies, and executes the selected plan using its own Python operators.

Its main output is an **executable physical query plan**, together with result rows in execute mode. The code panels are readable representations of plan objects. They are not rewritten SQL, machine code, or scripts that are passed to SQLite for execution.

## How the website works

1. **Choose data.** Use the college demo, or import CSV, JSON or SQLite tables. Tables used together in a join must belong to the same dataset. The schema panel shows available columns, types and indexes.
2. **Enter SQL.** Type a query or choose Index lookup, Join strategy or Rule magic.
3. **Choose a mode.** Compile & execute runs all seven phases. Compile only validates and optimizes without running the executor. Check syntax only runs phases 1–2 and needs no dataset.
4. **Start the journey.** The editor is hidden and the results view appears in the same website. Each Run phase button asks the backend to run the next phase; the sidebar lets you revisit completed phases.
5. **Inspect the evidence.** Each phase shows the submitted query, its input/output, its purpose and details derived from that query. Phase 4 explains the logical plan, Phase 5 compares rule changes, Phase 6 compares costs and physical choices, and Phase 7 shows results and measured work.
6. **Export or edit.** Download the JSON run report, code text, SVG trees or displayed result CSV. Back to editor returns to the SQL input.

The loading animation includes a minimum visual delay. Backend phase timings exclude that delay, network time and your reading time. Estimated plan costs and measured execution time are different quantities.

## How the parts connect

The frontend is plain HTML, CSS and JavaScript. A local Python HTTP server serves the website and handles compiler/data API requests. The catalog stores table rows, schema information, statistics and in-memory equality indexes. Dataset contents persist in `data/catalog.json`; active compiler journeys are kept in memory.

The incremental API creates a run, then advances one phase per request. The one-call compile API can perform the whole pipeline. Both include the new explanations and plan comparisons in their reports.

SQLite is an **import format**. Imported rows enter LumaSQL's catalog and are queried through LumaSQL's own executor.

## The seven compiler phases

| Phase | Input → output | Responsibility |
|---|---|---|
| 1. Lexical analysis | SQL text → tokens | Recognizes keywords, identifiers, literals, operators and punctuation. Records line/column positions. Whitespace/comments are skipped; EOF marks the end. |
| 2. Syntax analysis | Tokens → abstract syntax tree | Checks the supported grammar and groups the statement into SELECT, FROM, JOIN, WHERE, ORDER BY, DISTINCT and LIMIT fields. Syntax success alone does not validate table or column names. |
| 3. Semantic analysis | AST + schema → bound AST and output types | Resolves aliases and column references, expands `*`, rejects unknown/ambiguous names and checks expression types. |
| 4. Intermediate code generation | Bound AST → original logical plan + ICG | Converts the statement into relational operators and names their temporary results `t1`, `t2`, etc. This is the baseline plan before optimization. |
| 5. Rule-based optimization | Original plan → equivalent rewritten logical plan | Applies eligible constant folding, Boolean simplification, duplicate-predicate removal, filter pushdown, column pruning and empty-result rewrites. |
| 6. Cost-based optimization | Rewritten plan + statistics/indexes → physical plan | Compares eligible sequential/index access and nested-loop/hash joins, then selects the cheaper modeled alternatives. |
| 7. Execution and verification | Physical plan + data → results and measurements | Executes the selected Python operators. Execute mode also attempts to run the original plan and compare full result rows before truncation. Compile-only mode skips execution. |

Relevant files: `syntax.py` handles phases 1–2; `semantics.py` validates names/types and builds the original plan; `codegen.py` renders plan instructions; `optimization.py` implements the two optimizers; `execution.py` runs operators. `journey.py` advances phases, `pipeline.py` provides one-call compilation, and the new `explanations.py` builds teaching notes and comparable estimates. Frontend teaching panels are in `teaching.js`.

## The logical query plan after Phase 4

### What it represents

A logical query plan is a **tree of relational operations** describing how the answer is derived. Each node produces a collection of rows that its parent consumes. SQL normally permits duplicate rows; DISTINCT explicitly removes them.

The tree expresses data dependencies. Its leaves provide source relations. Its root produces the final answer. It does not yet decide whether a table should use an index or whether a join should use hashing.

Do not read the diagram as a top-to-bottom flowchart. **Data flows from the leaves upward.** The numbered walkthrough uses a left-then-right traversal that produces inputs before their parent, matching the temporary names in the ICG. That numbering does not imply parallel execution.

### Example: the default index query

```sql
SELECT name, marks
FROM students
WHERE id = 42 AND 1 = 1;
```

Its original logical tree is:

```text
t3  Project [students.name, students.marks]   ← final result
     |
t2  Filter [(students.id = 42) AND (1 = 1)]
     |
t1  TableScan [students]
```

Read it as follows:

1. **TableScan:** obtain the students relation. In this original plan, all table columns are carried. Phase 4 has not selected an access strategy.
2. **Filter:** retain rows where the complete WHERE expression evaluates to TRUE. SQL FALSE and UNKNOWN/NULL do not pass. Here, `1 = 1` is redundant, but it is still present before rule optimization.
3. **Project:** keep only the requested output columns, name and marks. Projection selects **columns**; filtering selects **rows**.
4. **RETURN:** deliver the root relation as the query answer.

The same tree is serialized as:

```text
t1 = SCAN(students AS students, COLUMNS [*])
t2 = FILTER(t1, ((students.id = 42) AND (1 = 1)))
t3 = PROJECT(t2, [students.name AS name, students.marks AS marks])
RETURN t3
```

Each `tN` is a temporary **relation**, not a single scalar value. The ICG is generated from the real plan; the executor consumes the plan object, not this displayed text.

### Reading a plan with a join

```sql
SELECT s.name, d.name
FROM students s
JOIN departments d ON s.department_id = d.id
WHERE s.marks > 80
ORDER BY s.name;
```

The baseline tree has this shape:

```text
Project [s.name, d.name]
  |
Sort [s.name ASC]
  |
Filter [s.marks > 80]
  |
Join [s.department_id = d.id]
  /                         \
TableScan [students AS s]    TableScan [departments AS d]
```

Both scans supply rows to Join. The inner join combines matching student/department pairs. Filter then keeps qualifying pairs; Sort orders them; Project returns the selected names.

Phase 5 can move `s.marks > 80` below the join onto the students branch because it refers only to `s` and this is an INNER JOIN. That reduces the student rows reaching the join while preserving the answer. Columns needed for output, filtering, ordering and the join key remain available after pruning. Phase 6 then chooses the actual join algorithm.

This implementation puts Sort before Project so ORDER BY may use a source column that is not selected, except where DISTINCT imposes the supported semantic restriction.

### Operator reference

| Plan node | Relational notation | Meaning |
|---|---|---|
| TableScan | Named relation | Obtain the source table relation. |
| Filter | σ, selection | Keep rows where the condition is TRUE. |
| Project | π, projection | Return the selected columns. |
| Join | ⋈ | Combine input rows satisfying the ON condition. |
| Sort | τ, extended relational algebra | Order rows by the specified keys/directions. |
| Distinct | δ, duplicate elimination | Remove identical output rows. |
| Limit | LIMIT, extended operator | Return at most the requested number of rows. |
| EmptyResult | ∅ | Produce no rows without a table access when an optimizer proves this is possible. |

Sort and Limit are extensions beyond basic relational algebra. In this executor, a positive LIMIT materializes its child first; it does not automatically stop an underlying scan early. LIMIT 0 is special: the executor skips the child, and its modeled cost is zero even for the original plan. Without ORDER BY, SQL does not guarantee row order, even though this educational engine preserves a deterministic order for baseline comparisons.

### Why the logical plan is useful

- **A bridge between meaning and execution:** the AST reflects SQL grammar; the logical plan turns that meaning into relational operations.
- **A structure for safe optimization:** the optimizer can inspect operator dependencies, move eligible filters and retain required columns.
- **A basis for physical choices:** Phase 6 replaces abstract scans and joins with concrete implementation strategies.
- **A correctness baseline:** the original plan can be executed and its full result compared with the final plan's result.
- **An explanation tool:** you can trace which table, condition or operation produced each intermediate relation.

The key distinction is: **AST = statement structure; logical plan = required relational operations; physical plan = concrete execution strategies.**

## Rule-based versus cost-based optimization

| Aspect | Rule-based optimization | Cost-based optimization |
|---|---|---|
| Main question | Which equivalent rewrites apply? | Which eligible implementation has the lowest estimated work? |
| Basis | Expression and relational equivalence rules | Row counts, value frequencies, distinct counts and indexes |
| Typical changes | Remove `AND TRUE`, fold arithmetic, remove duplicates, push filters, prune columns | Select sequential scan/index lookup and nested-loop/hash join |
| Output | Rewritten logical plan and ICG | Physical plan and physical code |
| Website evidence | Before/after code and each rule's actual local change | Three-stage total-cost comparison and each candidate's local cost |

The phases cooperate: logical rewrites first reshape the work, then cost-based choices implement that rewritten plan.

### Understanding the new cost comparison

The website shows three versions under the **same cost model and dataset statistics**:

1. **Original:** the Phase 4 structure, estimated using sequential scans and nested-loop joins.
2. **Rule-only:** the Phase 5 structure, estimated with those same baseline strategies.
3. **Cost-selected:** the Phase 6 plan using its selected physical strategies.

For the default query above on the fresh 200-row demo:

| Version | Total estimated work | Explanation |
|---|---:|---|
| Original | 250.1 | Scan 200 + filter 0.25 × 200 + project 0.05 × 1 row × 2 output columns. |
| Rule-only | 250.1 | Redundant condition removed and scan columns reduced from 7 to 3 (`id`, `name`, `marks`). This row-based model does not separately price those savings. |
| Cost-selected | 3.55 | Index lookup 2 + 1.2 × 1 = 3.2, retained filter 0.25, projection 0.1. |

The modeled total reduction is **246.55 work units, about 98.58%**. This is not a claim of a 98.58% runtime speedup. With the unchanged demo data, execution returns `Student 42` and marks `92`.

The retained Filter checks the full predicate after the index lookup. Access-path candidates compare **local access work** (200 versus 3.2 here); total plan cost also includes filter/projection and any other applicable operations.

The table's cost delta is earlier cost minus later cost. Positive means estimated savings, zero means unchanged, and negative means a higher estimate. A percentage is unavailable when the original cost is zero.

### Model limits and actual measurements

The model uses work units, not milliseconds. It prices row access, filtering, projection, joins, sort and DISTINCT. Column pruning and expression simplification are not separately priced, so a valid optimization can leave the estimate unchanged. Compound predicates and joins use simplified cardinality assumptions, which can produce different row estimates for equivalent plans.

Phase 7 separately shows actual execution milliseconds and counters such as rows examined, cells materialized, filter checks, join pairs, hash build/probe rows and index lookups. Small-query timings can be noisy. No optimization is guaranteed to improve elapsed time.

Hash-index creation occurs during data setup and is excluded from per-query cost. These are in-memory equality indexes, not disk B-trees. The optimizer retains SQL join order; it does not search all possible join permutations.

Verification compares full results before the website's 1,000-row display limit. If the baseline exceeds its execution budget, verification is reported as skipped rather than passed. CSV exports contain the displayed rows.

## What changed in this version

- Added per-query explanations for all seven phases, including the submitted SQL, inputs, outputs and phase-specific facts.
- Added an original-versus-rule code comparison and actual before/after evidence for each applied rule.
- Added original, rule-only and cost-selected plan totals, signed differences, percentages, cost bars and formula explanations.
- Added a detailed Phase 4 plan walkthrough, temporary-relation labels and readable node inspection.
- Added a How LumaSQL works guide inside the website.
- Corrected constant-condition cost estimation and closed SQLite import connections explicitly to avoid Windows file locks.

## Running and presenting

Extract the updated ZIP, open the `lumasql` folder containing `run.py`, and run:

```text
python run.py
```

Python 3.10+ is required. There are no pip, npm or database-server setup steps. Open `http://127.0.0.1:8000` if the browser does not open automatically. Keep the terminal running.

A useful demonstration sequence is Index lookup → Phase 4 logical walkthrough → Phase 5 rule evidence → Phase 6 cost comparison → Phase 7 verified result. Then try Join strategy to explain filter pushdown and join selection. Import your own data to demonstrate that the explanations follow the selected schema and submitted query.

Supported scope: SELECT, `*`, DISTINCT, aliases, INNER JOIN, WHERE with supported Boolean/comparison/arithmetic expressions, IS NULL, ORDER BY and LIMIT. Aggregates, GROUP BY, subqueries, outer joins and data-changing SQL are outside this implementation.
