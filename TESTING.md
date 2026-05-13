# Testing

Install dependencies:

```bash
pip install -r requirements.txt
```

Run tests:

```bash
PYTHONPATH=. pytest -q
```

Run a JSON solve request through the file boundary:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli --request-json request.json --response-json response.json
```

The checked-in fixture `tests/fixtures/solve_request_small.json` is a minimal
valid request for contract tests.
JSON solve options are bounded at the schema boundary: `0 < time_limit_sec <= 30`,
`seed` must be a JSON integer, `use_warm_start` must be a boolean, and
`response_mode` must be one of `compact`, `standard`, or `debug`. The default
mode is `debug`, which preserves the full explainability payload. `standard`
keeps the main solve result plus fairness metrics and shortage diagnostics.
`compact` keeps only core metrics, assignments, shortages, violations, and the
objective breakdown.

Run a three-file CSV solve:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli --employees-csv employees.csv --shifts-csv shifts.csv --demand-csv demand.csv --roster-csv roster.csv --min-rest-hours 8 --max-consecutive-days 5 --shortage-penalty 1000
```

Example CSV inputs are checked in under `examples/csv/`.

CSV contract:

- `employees.csv`: `employee_id,name,roles,hourly_cost,max_weekly_hours,available_day0_shift0,...`
- `shifts.csv`: `shift,shift_name,start_hour,end_hour`
- `demand.csv`: `day,shift,role,required`

In `shifts.csv`, `shift` is the zero-based shift id and `shift_name` is the
manager-facing label written to roster output. Shift ids must be consecutive:
`0`, `1`, `2`, and so on. Global solver settings are not read from
`shifts.csv`; pass `--min-rest-hours`, `--max-consecutive-days`, and
`--shortage-penalty` explicitly. Use `|` between multiple employee roles. Add
one explicit availability column for every day/shift combination in the demand
and shift files, using the pattern
`available_day{day_index}_shift{shift_index}`. Values may be `true`/`false`,
`yes`/`no`, or `1`/`0`.

Roster output CSV uses this header:

```csv
record_type,employee_id,name,day,shift,shift_name,role,status,value,message
```

Assignment rows use `record_type=assignment`, `status=assigned`, and
`value=1`. Shortage rows use `record_type=shortage`, `status=unfilled`, and
`value` equal to the unfilled count. All shortage records from the canonical
solve response are written, including zero-shortage rows; zero-shortage rows
leave `message` blank, while positive shortage rows include an unfilled-demand
message. CSV generated from a full `solve_payload(...)` response also includes a
`record_type=metric` row for each solver metric. Metric rows use `status` for
the metric name and `value` for the metric value. Solver metric rows include
status, objective value, best bound, wall time, conflicts, branches, variables,
and constraints. Business metric rows include total shortage, labor cost,
workload spread, and validation violation count when available. The output can
also include `record_type=validation` or `record_type=error` rows when the
response contains validation violations or an error envelope.

Run the thin HTTP wrapper locally:

```bash
PYTHONPATH=. uvicorn workforce_scheduling.api:app --reload
```

HTTP endpoints:

- `GET /health`
- `GET /metadata`
- `POST /solve`
- `POST /solve-csv`
- `POST /solve-jobs`
- `GET /solve-jobs/{job_id}`

`POST /solve-csv` accepts multipart fields `employees_csv`, `shifts_csv`, and
`demand_csv`, plus the same CSV solve settings used by the CLI:
`min_rest_hours`, `max_consecutive_days`, `shortage_penalty`, `time_limit_sec`,
`seed`, and `use_warm_start`. It returns the standard roster CSV as `text/csv`.
All HTTP responses include `X-Request-ID`. Incoming `X-Request-ID` values are
preserved; otherwise the API generates one. JSON solve routes reject request
bodies larger than 1,000,000 bytes before parsing.

Run benchmark fixtures:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark
PYTHONPATH=. python -m workforce_scheduling.benchmark --json
```

Compare benchmark fixtures without and with warm-start hints:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark --compare-warm-start
PYTHONPATH=. python -m workforce_scheduling.benchmark --compare-warm-start --json
```

Run manual scaling benchmark fixtures:

```bash
PYTHONPATH=. python -m workforce_scheduling.benchmark --scaling
PYTHONPATH=. python -m workforce_scheduling.benchmark --scaling --compare-warm-start
```

Benchmark interpretation:

- Benchmarks are deterministic fixtures, not production capacity guarantees.
- `FEASIBLE` means CP-SAT found a valid incumbent inside the time limit but did not prove optimality.
- Use `best_bound`, absolute gap, and relative gap percent to judge remaining optimality uncertainty.
- Warm-start comparison reports observed facts only; hints can help, hurt, or be neutral.
- The 80- and 120-employee scaling cases are intended for manual runs, not normal pytest coverage.
- See `SOLVER_LIMITS.md` for the current benchmark reporting contract and operating-limit notes.
