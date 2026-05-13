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
for future service integration. It is an in-process schema adapter used by a
thin HTTP wrapper only; this project still does not include a database, queue,
worker, persistence layer, or frontend.
`solve_payload(...)` returns a stable envelope: `{"ok": true, "result": ...}`
for processed solve requests and `{"ok": false, "error": ...}` for malformed
requests or solver input errors.
JSON remains the canonical internal service contract. The three-file CSV
boundary is a file adapter only: it converts `employees.csv`, `shifts.csv`, and
`demand.csv` into `ProblemData`, runs the same solver, and writes one roster CSV.
Employee availability should be provided with explicit
`available_day{day}_shift{shift}` columns so non-technical managers can inspect
and edit the file without decoding a compact matrix.
In `shifts.csv`, `shift` is a consecutive zero-based id used by demand rows and
availability columns, while `shift_name` is the readable label shown in roster
output. Global solver settings are intentionally outside the three CSV files:
`min_rest_hours`, `max_consecutive_days`, `shortage_penalty`, `time_limit_sec`,
`seed`, and `use_warm_start` are explicit adapter or CLI parameters. The CSV
adapter builds the same JSON-safe solve request payload used by
`solve_payload(...)`; CSV is not a separate solver contract. The roster CSV
output uses one standard record shape for assignments and shortages:
`record_type,employee_id,name,day,shift,shift_name,role,status,value,message`.
Request contract failures use `SchemaValidationError` in the error envelope so
future wrappers can distinguish malformed JSON payloads from solver infeasibility
or normal validation violations.
Solve request options include `use_warm_start`, which defaults to `false`.
When set to `true`, the boundary uses the existing deterministic warm-start
hint generator only; it does not add objectives, constraints, or different
optimization behavior.
Request options are intentionally bounded before the solver is called:
`0 < time_limit_sec <= 30`, `seed` must be a JSON integer, and
`use_warm_start` must be a boolean. These limits keep the in-process HTTP
wrapper from accepting ambiguous or unexpectedly expensive solve requests.

`workforce_scheduling.api` is a thin FastAPI wrapper over `solve_payload(...)`.
It exposes `GET /health`, `GET /metadata`, `POST /solve`, `POST /solve-jobs`,
and `GET /solve-jobs/{job_id}`. The synchronous solve endpoint preserves the
existing success/error envelope. The job endpoints are an in-process,
in-memory prototype for the future async contract only; jobs disappear when the
process restarts and are not a durable queue. Submitted jobs run in the current
process through a bounded `ThreadPoolExecutor(max_workers=2)`, not a production
worker system. Job payloads include `created_at`, `updated_at`, `started_at`,
`finished_at`, and `duration_sec`; queued jobs report `null` for fields that do
not exist yet. The API does not add persistence, auth, websocket delivery, or
any new solver behavior.
