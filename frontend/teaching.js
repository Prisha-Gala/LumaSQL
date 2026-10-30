/* Teaching panels use backend facts and textContent, including imported names. */
function phaseNotes(r, phase) {
  const query = panel('Your submitted query');
  query.classList.add('query-context');
  query.append(make('pre', r.query, 'query-source'));
  const note = r.phase_explanations?.[String(phase)];
  if (!note) return;
  const p = panel('What this phase does for your query');
  p.classList.add('phase-notes');
  p.append(make('h4', note.title));
  const flow = make('div', undefined, 'phase-flow');
  for (const [label, value] of [['Input', note.input], ['Output', note.output]]) {
    const d = make('div'); d.append(make('b', label), make('p', value)); flow.append(d);
  }
  p.append(flow, make('p', note.why));
  const list = make('ul', undefined, 'query-facts');
  note.query_details.forEach(text => list.append(make('li', text)));
  p.append(list);
}

function codeComparison(title, versions) {
  const p = panel(title), grid = make('div', undefined, 'code-comparison');
  for (const [label, text, filename] of versions) {
    const column = make('section');
    column.append(make('h4', label));
    const pre = make('pre', undefined, 'generated-code');
    pre.tabIndex = 0; pre.setAttribute('aria-label', label);
    const code = make('code', text); pre.append(code);
    const actions = make('div', undefined, 'code-actions');
    const copy = make('button', 'Copy'), download = make('button', '↓ Download');
    const status = make('span', '', 'muted'); status.setAttribute('role', 'status');
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(text); status.textContent = 'Copied'; }
      catch {
        const range = document.createRange(); range.selectNodeContents(code);
        const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        status.textContent = 'Selected — press Ctrl+C / ⌘C.';
      }
    };
    download.onclick = () => saveBlob(new Blob([text + '\n'], {type:'text/plain;charset=utf-8'}), filename);
    actions.append(copy, download, status); column.append(actions, pre); grid.append(column);
  }
  p.append(grid);
}

function costComparison(r, phase) {
  const comparison = r.plan_comparison;
  if (!comparison) return;
  const stages = comparison.stages.slice(0, phase === 5 ? 2 : 3);
  const p = panel(phase === 5 ? 'Original versus rule-optimized costs' : 'Original versus both optimized costs');
  p.append(make('p', comparison.basis));
  table(p, ['Version', 'Total estimated cost', 'Δ cost vs original', 'Reduction %', 'Δ cost vs previous', 'Estimated result rows', 'Operators', 'Scan column count'], stages.map(s => [
    s.label, s.estimated_cost, s.reduction_from_original,
    s.reduction_percent === null ? 'N/A · original cost is zero' : `${s.reduction_percent}%`,
    s.reduction_from_previous, s.estimated_rows, s.operators, s.scan_columns,
  ]));
  p.append(make('p', 'Δ = earlier cost − later cost, in estimated work units. Positive means estimated savings; zero means unchanged; negative means a higher estimate. Scan column count adds the column widths of all scan nodes; it is not a row count.'));
  const bars = make('div', undefined, 'cost-bars');
  const max = Math.max(1, ...stages.map(s => s.estimated_cost));
  for (const s of stages) {
    const row = make('div', undefined, 'cost-row'), track = make('div', undefined, 'cost-track');
    const bar = make('div', undefined, 'cost-fill'); bar.style.width = `${100 * s.estimated_cost / max}%`;
    track.append(bar); row.append(make('span', s.label), track, make('b', String(s.estimated_cost))); bars.append(row);
  }
  p.append(bars, make('p', comparison.limitations, 'model-note'));
  const d = make('details'); d.append(make('summary', 'How the costs are calculated'));
  const list = make('ul', undefined, 'query-facts');
  [
    'Sequential scan: N input rows. Equality index: 2 + 1.2 × matching rows. Index setup is excluded.',
    'Filter: child cost + 0.25 × candidate rows. Project: child cost + 0.05 × rows × selected columns.',
    'Nested-loop join: child costs + L × R. Hash join: child costs + 4 + L + R + 0.25 × M + 0.1 × M × log₂(max(1, M)). M is estimated matches; the final term models deterministic pair ordering.',
    'Sort adds N × log₂(max(1, N)); DISTINCT adds N. A positive LIMIT caps output rows without reducing its materialized input cost. LIMIT 0 and EmptyResult cost zero because the executor skips their inputs.',
    'Single-column predicates use observed value frequencies, including NULLs. Combined conditions use simplified independence assumptions. Equijoin matches use L × R / max(1, distinct key counts); other joins use L × R × 0.33.',
    'Column pruning saves materialized cells, and expression simplification can reduce evaluation work. This model does not separately price either effect; see Phase 7 measured counters for actual execution work.',
  ].forEach(text => list.append(make('li', text)));
  d.append(list); p.append(d);
}

function ruleDetails(r) {
  const p = panel('Rules actually applied · before and after');
  if (!r.rule_optimizations.length) p.append(make('p', 'No applicable rewrite was found for this query. The original and rule-optimized code are the same.'));
  for (const [i, rule] of r.rule_optimizations.entries()) {
    const d = make('div', undefined, 'rule');
    d.append(make('h4', `${i + 1}. ${rule.rule.replaceAll('_', ' ')}`), make('p', rule.explanation));
    const grid = make('div', undefined, 'code-comparison');
    for (const [label, text] of [['Before this rule', rule.before], ['After this rule', rule.after]]) {
      const col = make('div'); col.append(make('b', label), make('pre', text, 'rule-code')); grid.append(col);
    }
    d.append(grid); p.append(d);
  }
  p.append(make('p', 'Each entry describes a local rewrite. The code comparison above shows the complete original and rewritten plans. The same rule can apply more than once.'));
}

function optimizationMethods() {
  const p = panel('Rule-based versus cost-based optimization');
  table(p, ['Aspect', 'Rule-based · Phase 5', 'Cost-based · Phase 6'], [
    ['Decision basis', 'Equivalent-expression rewrite rules', 'Statistics and estimated candidate costs'],
    ['Changes', 'Predicates, filter position, carried columns', 'Sequential/index access and nested-loop/hash joins'],
    ['Example', 'Remove AND TRUE; filter before an INNER JOIN', 'Use an equality index if cheaper than scanning'],
    ['Output', 'Optimized intermediate code', 'Execution strategies + physical code'],
    ['Evidence here', 'Actual before/after rule log', 'Eligible candidates, costs and selected strategy'],
  ]);
  p.append(make('p', 'The two phases cooperate: rules simplify the operations first, then costs guide the scan and join strategies. The generated code represents these operations; this project does not generate rewritten SQL or machine code.'));
}
