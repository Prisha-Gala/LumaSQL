-- Paste ONE statement at a time into LumaSQL.
-- Queries 1–6: select a fresh Example college dataset.

-- 1. Students: expected Student 42, marks 92.
SELECT name, marks FROM students WHERE id = 42 AND 1 = 1;

-- 2. Departments: expected four rows, IDs 1–4.
SELECT id, name FROM departments ORDER BY id;

-- 3. Department lookup: expected IT.
SELECT name FROM departments WHERE id = 3;

-- 4. Join: expected Student 31/Mechanical, Student 32/CSBS, Student 33/Computer.
SELECT s.name, d.name
FROM students s
INNER JOIN departments d ON s.department_id = d.id
WHERE s.marks > 80
ORDER BY s.id
LIMIT 3;

-- 5. Rule rewrites: expected IDs 50, 101, 152, 49, 100.
SELECT id, name, marks
FROM students
WHERE marks > 95 AND marks > 95 AND 2 + 3 = 5
ORDER BY marks DESC, id ASC
LIMIT 5;

-- 6. Intentional error: do NOT create teachers.
-- Use Compile & execute or Compile only, then advance to Phase 3.
-- Expected: Table 'teachers' does not exist in this dataset.
SELECT name FROM teachers;

-- Queries 7–8: first import examples/customers.csv as customers.
-- Select the dataset containing that imported table.

-- 7. Expected PersonA (20) and PersonB (21).
SELECT id, name, age
FROM customers
WHERE city = 'Mumbai' AND age >= 20
ORDER BY id;

-- 8. Expected ID 5, PersonE.
SELECT id, name FROM customers WHERE age IS NULL;
