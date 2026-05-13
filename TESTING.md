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
`seed` must be a JSON integer, and `use_warm_start` must be a boolean.

Run a three-file CSV solve:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli --employees-csv employees.csv --shifts-csv shifts.csv --demand-csv demand.csv --roster-csv roster.csv
```

CSV contract:

- `employees.csv`: `employee_id,name,roles,hourly_cost,max_weekly_hours,availability`
- `shifts.csv`: `shift,start_hour,end_hour`
- `demand.csv`: `day,shift,role,required`

Use `|` between multiple employee roles. The `availability` field uses `;`
between day rows and `|` between shift values, for example `1|0;1|1`.

Run the thin HTTP wrapper locally:

```bash
PYTHONPATH=. uvicorn workforce_scheduling.api:app --reload
```

HTTP endpoints:

- `GET /health`
- `GET /metadata`
- `POST /solve`
- `POST /solve-jobs`
- `GET /solve-jobs/{job_id}`

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
