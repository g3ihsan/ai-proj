# Solver Limits

This project is a deterministic solver sandbox, not a production capacity
guarantee. Benchmark results are intended to compare solver behavior across
known fixtures and to identify scaling risk before adding a service boundary.

## Current Benchmark Coverage

- Small fully feasible case
- Temporal/rest-window constrained case
- Unavoidable understaffing case
- Fairness-vs-cost case
- Synthetic 20-, 40-, 80-, and 120-employee weekly cases

Normal pytest coverage uses small fixtures and the 20-employee scaling case.
The 80- and 120-employee scaling cases are manual benchmark cases because they
can be slower and depend on machine resources.

## Commands

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark
PYTHONPATH=. python -m workforce_scheduling.benchmark --json
PYTHONPATH=. python -m workforce_scheduling.benchmark --compare-warm-start
PYTHONPATH=. python -m workforce_scheduling.benchmark --scaling
```

## Interpreting Results

- `OPTIMAL` means CP-SAT proved the best roster under the current objective.
- `FEASIBLE` means CP-SAT found a valid incumbent but did not prove optimality.
- `best_bound`, `absolute_optimality_gap`, and
  `relative_optimality_gap_percent` describe remaining optimality uncertainty.
- If objective and best bound are both zero, relative gap is `0`.
- If objective is zero but best bound is nonzero, relative gap is not defined.
- Warm-start comparison reports observed search metrics only. Hints can help,
  hurt, or be neutral without changing optimization correctness.

## Current Scope Boundaries

The benchmark runner must not change solver decisions. It only reports solve
status, objective components, validation counts, search metrics, and warm-start
comparison facts.

`workforce_scheduling.schemas` provides a JSON-safe request/response boundary
for future service integration. It is an in-process schema adapter only; this
project still does not include an HTTP API, database, queue, worker, or frontend.
`solve_payload(...)` returns a stable envelope: `{"ok": true, "result": ...}`
for processed solve requests and `{"ok": false, "error": ...}` for malformed
requests or solver input errors.
