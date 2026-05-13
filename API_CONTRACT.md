# API Contract

This project exposes a deterministic workforce scheduling solver through three
thin boundaries:

- in-process JSON: `solve_payload(...)`
- HTTP: `workforce_scheduling.api`
- files: JSON request files and three-file CSV input/output

The solver remains the source of truth. API, job, and CSV surfaces must not add
solver objectives, constraints, persistence, or alternate scheduling behavior.

## Versioning

- `schema_version`: `1`
- JSON is the canonical internal service contract.
- CSV input is an adapter that builds the canonical JSON solve request.
- CSV output is rendered from the canonical solve response envelope.

## HTTP Operational Contract

Base service module:

```bash
PYTHONPATH=. uvicorn workforce_scheduling.api:app --reload
```

Every HTTP response includes `X-Request-ID`.

- If the request includes `X-Request-ID`, the API preserves it.
- If it is missing, the API generates one.
- Error envelopes include the request ID where practical.

The API logs method, path, status code, request ID, duration, route name, solve
success, and error type. It does not log request bodies, uploaded CSV content,
or full solver responses.

Request limits:

- JSON request body: `1,000,000` bytes
- Each uploaded CSV file: `1,000,000` bytes
- Solver time limit option: `0 < time_limit_sec <= 30`

The optional static viewer is served by the same process at `/viewer/`. It calls
the existing JSON, job, and CSV endpoints only; it has no persistence, auth, or
separate scheduling behavior.

## Endpoints

### `GET /health`

Returns service health.

Success:

```json
{
  "ok": true,
  "service": "workforce_scheduling_solver"
}
```

### `GET /metadata`

Returns the live service contract metadata, including endpoints, solve options,
job limits, and request limits. This endpoint does not run the solver.

### `GET /viewer/`

Returns a static roster viewer for local API demonstration and manual CSV/JSON
smoke testing. The viewer is not a separate solver boundary.

### `POST /solve`

Accepts the canonical JSON solve request and returns a JSON envelope.

Request example:

```bash
curl -X POST http://localhost:8000/solve \
  -H "Content-Type: application/json" \
  -H "X-Request-ID: demo-request-1" \
  --data @tests/fixtures/solve_request_small.json
```

Success envelope:

```json
{
  "ok": true,
  "result": {
    "schema_version": 1,
    "metrics": {},
    "assignments": [],
    "shortages": [],
    "violations": [],
    "objective_breakdown": {}
  }
}
```

Error envelope:

```json
{
  "ok": false,
  "error": {
    "type": "SchemaValidationError",
    "message": "Solve request must contain a problem object",
    "request_id": "demo-request-1"
  }
}
```

### `POST /solve-csv`

Accepts three uploaded CSV files, solves through the same canonical JSON
boundary, and returns one roster CSV as `text/csv`.

Multipart file fields:

- `employees_csv`
- `shifts_csv`
- `demand_csv`

Multipart form fields:

- `min_rest_hours`
- `max_consecutive_days`
- `shortage_penalty`
- `time_limit_sec`
- `seed`
- `use_warm_start`

Request example:

```bash
curl -X POST http://localhost:8000/solve-csv \
  -H "X-Request-ID: demo-csv-1" \
  -F employees_csv=@examples/csv/employees.csv \
  -F shifts_csv=@examples/csv/shifts.csv \
  -F demand_csv=@examples/csv/demand.csv \
  -F min_rest_hours=8 \
  -F max_consecutive_days=5 \
  -F shortage_penalty=1000 \
  -F time_limit_sec=5 \
  -F seed=1 \
  -F use_warm_start=false \
  -o roster.csv
```

CSV upload errors return JSON error envelopes, not CSV rows.

### `POST /solve-jobs`

Creates an in-memory async solve job prototype. The submitted request payload is
the same canonical JSON solve request used by `POST /solve`.

Success:

```json
{
  "ok": true,
  "job": {
    "job_id": "string",
    "status": "queued",
    "created_at": "ISO-8601 UTC timestamp",
    "updated_at": "ISO-8601 UTC timestamp",
    "started_at": null,
    "finished_at": null,
    "duration_sec": null
  },
  "status_url": "/solve-jobs/{job_id}"
}
```

Job limits:

- executor workers: `2`
- active queued/running jobs: `10`
- retained job records: `100`

When active capacity is full, the endpoint returns `429`.

### `GET /solve-jobs/{job_id}`

Returns the current in-memory job record.

Terminal success includes `job.result`. Terminal failure includes `job.error`.
Jobs are not durable and disappear when the process restarts.

## Canonical JSON Solve Request

Top-level shape:

```json
{
  "schema_version": 1,
  "problem": {},
  "options": {}
}
```

`problem` fields:

- `employees`
- `roles`
- `days`
- `shifts`
- `shift_start_hours`
- `shift_end_hours`
- `min_rest_hours`
- `max_consecutive_days`
- `shortage_penalty`
- `demand`
- `hint_assignments`

Employee record:

```json
{
  "employee_id": 0,
  "name": "E0",
  "roles": ["worker"],
  "hourly_cost": 20,
  "max_weekly_hours": 40,
  "availability": [[true], [true]]
}
```

Demand record:

```json
{
  "day": 0,
  "shift": 0,
  "role": "worker",
  "required": 1
}
```

Solve options:

```json
{
  "time_limit_sec": 5.0,
  "seed": 1,
  "use_warm_start": false,
  "response_mode": "debug"
}
```

Option rules:

- `time_limit_sec`: numeric, `0 < time_limit_sec <= 30`
- `seed`: JSON integer, not boolean
- `use_warm_start`: boolean
- `response_mode`: `compact`, `standard`, or `debug`

Checked-in example:

```txt
tests/fixtures/solve_request_small.json
```

## Response Modes

`debug` is the default and preserves the full explainability payload.

`compact` includes:

- `schema_version`
- `metrics`
- `assignments`
- `shortages`
- `violations`
- `objective_breakdown`

`standard` includes all `compact` fields plus:

- `fairness_metrics`
- `shortage_diagnostics`

`debug` includes all solver result fields, including:

- `constraint_metadata`
- `objective_metadata`
- `constraint_records`
- `fairness_metrics`
- `objective_breakdown`
- `shortage_diagnostics`
- `demanded_slot_diagnostics`
- `assignment_explanations`

Changing `response_mode` changes serialization only. It does not change solver
decisions.

## CSV Input Contract

CSV input uses exactly three files.

### `employees.csv`

Header pattern:

```csv
employee_id,name,roles,hourly_cost,max_weekly_hours,available_day0_shift0,available_day0_shift1
```

Rules:

- `employee_id`: integer
- `roles`: pipe-delimited role names, for example `worker|supervisor`
- `hourly_cost`: integer
- `max_weekly_hours`: integer
- availability columns must exist for every day/shift combination
- availability values may be `true`/`false`, `yes`/`no`, or `1`/`0`

### `shifts.csv`

Required header:

```csv
shift,shift_name,start_hour,end_hour
```

Rules:

- `shift`: consecutive zero-based integer ID
- `shift_name`: manager-facing label
- `start_hour`: integer hour
- `end_hour`: integer hour
- overnight shifts are represented by `end_hour <= start_hour`

Global solver settings are not read from `shifts.csv`.

### `demand.csv`

Required header:

```csv
day,shift,role,required
```

Rules:

- `day`: zero-based integer day index
- `shift`: shift ID or shift name
- `role`: role name
- `required`: non-negative integer demand

Checked-in CSV examples:

```txt
examples/csv/employees.csv
examples/csv/shifts.csv
examples/csv/demand.csv
```

## CSV Output Contract

Roster CSV output header:

```csv
record_type,employee_id,name,day,shift,shift_name,role,status,value,message
```

Record types:

- `metric`
- `assignment`
- `shortage`
- `validation`
- `error`

Assignment rows:

- `record_type=assignment`
- `status=assigned`
- `value=1`

Shortage rows:

- `record_type=shortage`
- `status=unfilled`
- `value=shortage_count`
- zero-shortage rows are emitted
- positive shortage rows include an unfilled-demand message

Metric rows include solver metrics and available business metrics such as total
shortage, labor cost, workload spread, and validation violation count.

## CLI Contract

JSON request file:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli \
  --request-json tests/fixtures/solve_request_small.json \
  --response-json response.json
```

Three-file CSV solve:

```bash
PYTHONPATH=. python -m workforce_scheduling.cli \
  --employees-csv examples/csv/employees.csv \
  --shifts-csv examples/csv/shifts.csv \
  --demand-csv examples/csv/demand.csv \
  --roster-csv roster.csv \
  --min-rest-hours 8 \
  --max-consecutive-days 5 \
  --shortage-penalty 1000 \
  --time-limit 5 \
  --seed 1
```

## Scope Boundaries

This contract intentionally excludes:

- database or persistence
- authentication or user accounts
- websocket delivery
- production queues or durable workers
- frontend or dashboard behavior
- multi-site scheduling
- forecasting or payroll
- new solver objectives or constraints

The current API is a thin in-process wrapper around the existing solver and CSV
adapters.
