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

The debug response includes the Solver Evidence Layer. This is deterministic
post-solve evidence derived from CP-SAT assignments, shortages, diagnostics, and
objective values. It is intended for future AI explanation tools, but no LLM is
used by the solver. Future assistants must consume this evidence instead of
generating schedules directly. Evidence fields include
`assignment_explanations`, `non_assignment_explanations`,
`shortage_explanations`, `constraint_blockers`, and
`decision_evidence_summary`. The evidence contract version is reported as
`decision_evidence_summary.evidence_contract_version`. Compact and standard
modes intentionally omit the AI-ready evidence fields. Internal lowercase
blocker names must be mapped intentionally to stable public uppercase reason
codes; tests fail if an emitted blocker is not represented in the public
mapping.

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
- `GET /viewer/`
- `POST /solve`
- `POST /explain/summary`
- `POST /explain/shortages`
- `POST /explain/assignment`
- `POST /explain/employee`
- `POST /explain/shift`
- `POST /explain/narrate`
- `POST /assistant/ask`
- `POST /recommendations`
- `POST /recommend/what-if`
- `POST /solve-csv`
- `POST /solve-jobs`
- `GET /solve-jobs/{job_id}`

`GET /viewer` redirects to `GET /viewer/`. `GET /viewer/` serves a static
roster viewer for the existing JSON, job, and CSV solve endpoints. It has no
separate build step and does not change solver behavior. Demo CSV files are
available at `/viewer/examples/employees.csv`, `/viewer/examples/shifts.csv`,
and `/viewer/examples/demand.csv` for local viewer demos. The JSON solve panel
includes a `compact`/`standard`/`debug` response mode selector that updates the
canonical request `options.response_mode` before solving. The viewer disables
action buttons while checks, solves, uploads, or job polling are running, and
uses the Issues tab for API errors, validation rows, and missing-input messages.
If the JSON editor contains malformed JSON, changing response mode leaves the
editor unchanged and reports an `InvalidJson` issue instead of falling back to
sample data.

`POST /solve-csv` accepts multipart fields `employees_csv`, `shifts_csv`, and
`demand_csv`, plus the same CSV solve settings used by the CLI:
`min_rest_hours`, `max_consecutive_days`, `shortage_penalty`, `time_limit_sec`,
`seed`, and `use_warm_start`. It returns the standard roster CSV as `text/csv`.
All HTTP responses include `X-Request-ID`. Incoming `X-Request-ID` values are
preserved; otherwise the API generates one. JSON solve routes reject request
bodies larger than 1,000,000 bytes before parsing. `POST /solve-csv` rejects
any uploaded CSV file larger than 1,000,000 bytes.

Explanation endpoints are deterministic wrappers over the Solver Evidence
Layer. They solve the canonical request with debug evidence internally, then
return manager-readable explanation payloads in the normal envelope:
`{"ok": true, "result": ...}`. They do not call an LLM and do not change solver
decisions. Detail endpoints accept `{"solve_request": ..., "target": ...}`.
For example, `/explain/assignment` target fields are `employee_id`, `day`,
`shift`, and `role`. Assignment explanations cover both assigned and
non-assigned cases. `/explain/shift` accepts `day` and `shift`, with optional
`role` filtering for one demanded role. Invalid target shapes return
`ExplanationQueryError`; valid targets with no matching evidence return
`ExplanationTargetNotFoundError`.

`POST /explain/narrate` accepts an existing deterministic explanation payload
or `{"solve_request": ..., "kind": ..., "target": ...}` and returns a
manager-facing narration payload. Supported narration kinds are `summary`,
`shortages`, `assignment`, `employee`, and `shift`. The default and only
configured provider is a fake deterministic provider for local use and tests, so
no external LLM call is made. The narration layer must treat explanation
payloads as read-only evidence: it may rewrite them, but it must not generate
rosters, change assignments, invent shortages, or alter solver reasons. Invalid
narration request shapes return `ExplanationNarrationError`. Invalid target
shapes preserve `ExplanationQueryError`; valid targets with no deterministic
evidence preserve `ExplanationTargetNotFoundError`; invalid solve requests
preserve schema/solve validation error types; provider failures use
`NarrationProviderError`. Narration responses include `source` metadata when
they are built from `solve_request`, `kind`, and `target`.

`POST /assistant/ask` is a deterministic intent router over the existing
explanation and narration helpers. It supports summary, shortage, assignment,
employee, and shift explanation questions. It uses explicit target patterns
only, plus exact case-insensitive employee name matches when one employee in the
solve request matches. Name matching is exact phrase matching, not substring or
fuzzy matching. It does not use an LLM for routing and does not generate
schedules. Explicit `target` fields override fields parsed from the question
text. Assistant responses include both `message` and `answer` with the same
text. Unsupported or under-specified questions return `ok=true` with
`status=unsupported` and no narration.

`POST /recommendations` and its `POST /recommend/what-if` alias evaluate
deterministic what-if scenarios through the same CP-SAT solver. The current
supported goal is `reduce_shortages`, the response contract version is `1`, and
the response includes `recommendation_type=what_if`. The only supported
scenario type is `set_availability`. Scenario generation is intentionally
narrow and capped: it tries availability changes for qualified unavailable
employees on shortage slots, re-solves each scenario, reports grounded
shortage/objective comparisons, and reports unsolved over-limit candidates in
`discarded_scenarios`. Returned recommendations are also capped through
`limits.max_recommendations`; positive over-limit results are reported in
`discarded_recommendations`. It does not use an LLM, does not generate
schedules outside the solver, and does not change normal `/solve` behavior.

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
