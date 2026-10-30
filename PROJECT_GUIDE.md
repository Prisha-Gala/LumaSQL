# LumaSQL: presentation notes and test queries

## What changed in version 2

The logical/executable query-plan diagrams and their dedicated walkthrough sections have been removed from Phases 4, 5, 6 and 7. The Phase 2 AST and Phase 3 bound AST remain. Generated intermediate code, before/after rule explanations, cost comparisons, results and execution measurements are unchanged.

Internally, plans still connect validated SQL to optimization and execution. The compiler needs that internal representation; displaying a separate tree is optional. For your presentation, explain Phase 4 through the generated instructions instead.

## Start the project

Extract the ZIP fully. Open the `lumasql` folder containing `run.py`, then run `python run.py`. Python 3.10+ is required. There are no pip or npm setup steps.

Use **Compile & execute** to test the examples. Start the journey and advance through the phases with **Run phase**. Use the phase sidebar to revisit completed output.

For examples 1–6, select the **Example college dataset** containing `students` and `departments`. If you need a fresh copy, click **+ Demo dataset**. Expected results below refer to that fresh demo: 200 students and four departments. Imported files can contain different rows even when their table names are the same.

Paste and run **one statement at a time**. The compiler does not accept an entire multi-statement SQL script in one run. New editor buttons also load the departments, department lookup and missing-table examples.

## 1. Students: indexed lookup and rule simplification

```sql
SELECT name, marks
FROM students
WHERE id = 42 AND 1 = 1;
```

Expected result:

| name | marks |
|---|---:|
| Student 42 | 92 |

Phase 5 removes the redundant `1 = 1` expression and keeps only required source columns. Phase 6 can choose the ID equality index. Costs are estimated work units, while Phase 7 time is measured separately.

## 2. Departments: read a different table

```sql
SELECT id, name
FROM departments
ORDER BY id;
```

Expected result:

| id | name |
|---:|---|
| 1 | CSBS |
| 2 | Computer |
| 3 | IT |
| 4 | Mechanical |

This example reads `departments`, rather than `students`. There is no WHERE predicate, so there is no eligible equality lookup; a sequential scan is appropriate. No rule rewrite is needed for this query.

## 3. Departments: equality lookup on a small table

```sql
SELECT name
FROM departments
WHERE id = 3;
```

Expected result: one row, `IT`.

The demo departments table has an ID index. Its modeled lookup cost is 3.2 versus a scan cost of 4, so Phase 6 selects the lookup. This demonstrates that the same optimization process works on a different table.

## 4. Students and departments: INNER JOIN

```sql
SELECT s.name, d.name
FROM students s
INNER JOIN departments d ON s.department_id = d.id
WHERE s.marks > 80
ORDER BY s.id
LIMIT 3;
```

Expected result:

| s.name | d.name |
|---|---|
| Student 31 | Mechanical |
| Student 32 | CSBS |
| Student 33 | Computer |

Phase 3 checks aliases `s` and `d` and resolves their columns. Phase 5 can apply the marks filter to students before the INNER JOIN, reducing rows reaching the join. Phase 6 compares eligible join algorithms. Phase 7 verifies that optimization preserved the full answer.

## 5. Students: duplicate predicate, constant folding and sorting

```sql
SELECT id, name, marks
FROM students
WHERE marks > 95 AND marks > 95 AND 2 + 3 = 5
ORDER BY marks DESC, id ASC
LIMIT 5;
```

Expected result:

| id | name | marks |
|---:|---|---:|
| 50 | Student 50 | 100 |
| 101 | Student 101 | 100 |
| 152 | Student 152 | 100 |
| 49 | Student 49 | 99 |
| 100 | Student 100 | 99 |

The rule log should show constant folding, removal of the duplicate predicate and Boolean simplification. The two ORDER BY keys make the top-five result deterministic when students share the same marks.

## 6. Missing table: a semantic error

```sql
SELECT name
FROM teachers;
```

Do not import or create a `teachers` table for this example. The fresh demo has no such table.

Expected website behavior in **Compile & execute** or **Compile only**:

1. Phase 1 succeeds: the SQL contains valid tokens.
2. Phase 2 succeeds: the statement matches the supported grammar, and the AST is displayed.
3. Phase 3 stops with the error below.
4. The error panel is titled **Let’s fix this query**. The phase heading is **Semantic analysis**. Phases 4–7 do not run, and no result table is produced.
5. The next button says **Edit query →**. You can still revisit the completed first two phases.

Exact error message:

```text
Table 'teachers' does not exist in this dataset.
```

This is a **semantic error**, because the SQL grammar is valid but its referenced table cannot be resolved in the selected dataset.

**Check syntax only** deliberately stops after Phase 2 and will not report a missing table: that mode does not use a dataset or validate names. Use Compile & execute or Compile only when demonstrating this error.

## 7. Customers: test a third table after import

The ZIP includes `examples/customers.csv`. This table is supplied as an import example; it is not automatically part of the default college dataset.

Click **Import your data**, choose that CSV, and set its table name to `customers`. Add it to the selected dataset or create a new one. You may specify `id, city` as index columns. After importing, select the dataset containing `customers`.

```sql
SELECT id, name, age
FROM customers
WHERE city = 'Mumbai' AND age >= 20
ORDER BY id;
```

Expected result for the supplied CSV:

| id | name | age |
|---:|---|---:|
| 1 | PersonA | 20 |
| 2 | PersonB | 21 |

This demonstrates schema binding and execution on imported data, rather than only the built-in tables. An available index is considered, but the optimizer can still prefer a scan if its estimated work is lower.

## 8. Customers: SQL NULL handling

Run this against the same imported customers table:

```sql
SELECT id, name
FROM customers
WHERE age IS NULL;
```

Expected result: one row, ID `5`, name `PersonE`.

The blank age in the CSV becomes SQL NULL. Use `IS NULL` to test for it; `age = NULL` does not evaluate to TRUE.

## A short explanation for your teacher

“Our project accepts a SELECT query and processes it through seven compiler phases. First it recognizes tokens, checks grammar, then validates table names, column names and types. Phase 4 generates intermediate instructions using temporary relations. Phase 5 applies safe rules to simplify those operations. Phase 6 compares scan and join costs using dataset statistics. Finally, our Python executor runs the selected operations, displays the result and compares it with the original computation when verification succeeds.”

| Phase | What to show |
|---|---|
| 1. Lexical analysis | Tokens and source positions. |
| 2. Syntax analysis | AST showing SQL structure. |
| 3. Semantic analysis | Bound column/type mapping, or the missing-table error. |
| 4. Intermediate code | Instructions such as SCAN, FILTER and PROJECT; each `tN` stores an intermediate relation. |
| 5. Rule optimization | Original/optimized code and each rule's before/after change. |
| 6. Cost optimization | Original, rule-only and cost-selected costs; eligible strategies and chosen candidates. |
| 7. Execution | Results, measured work and baseline verification status. |

Rule-based optimization uses equivalence rules. Cost-based optimization compares eligible implementation strategies using statistics. A rule can reduce carried columns or expression work without changing this model's estimate, because not every effect is separately priced. Costs are not milliseconds, and measured runtime improvement is not guaranteed.

The frontend is plain HTML/CSS/JavaScript. A local Python HTTP server serves the website and APIs. The catalog stores rows, types, statistics and equality indexes. Data persists in `data/catalog.json`; active journeys are held in memory. SQLite files can be imported, but queries execute through LumaSQL's own Python operators.
