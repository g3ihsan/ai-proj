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

`workforce_scheduling.csv_mapper` is a deterministic pre-validation helper for
messy CSV headers. It suggests employee, shift, and demand column mappings,
reports confidence, missing fields, unmapped headers, warnings, and
`uses_external_llm=false`. It does not replace `csv_adapter.py`; tests assert it
only produces mapping reports and validation errors before canonical CSV
parsing.
`POST /csv/mapping/suggest` exposes that same mapper through the API. It accepts
combined JSON header arrays or a single `csv_type` plus `headers` dataset,
returns `complete` or `needs_review` reports without solving, rejects invalid
header shapes with HTTP 400, and preserves the normal JSON request-size limit.
Availability header tests cover canonical day/shift columns and common
day-name variants such as `Available Monday Morning`.
`POST /csv/mapping/preview` returns a deterministic preview/apply plan for one
dataset. Tests cover explicit mappings, inferred mappings, day-name
availability headers that require review, invalid preview requests, JSON
serializability, deterministic output, `can_apply=true` for complete plans,
`can_apply=false` for plans that need review, stable `reason` codes,
header-scoped adapter readiness metadata, supplied `mapping_report` validation,
and the fact that preview calls do not change `/solve-csv` behavior.
`POST /csv/mapping/rows/preview` covers deterministic sample row transformation
from explicit mappings or validated apply plans. Tests assert transformed row
objects, transformed value order, invalid row-shape rejection, duplicate target
reporting, row-level `ready` and `needs_review` statuses, blank
required-value reporting for mapped canonical fields, row-level error
attachment, the 20-row preview limit, JSON serializability, no file mutation,
no solving, and `row_semantics_validated=false` because `csv_adapter.py`
remains the strict parser.
`POST /csv/mapping/export/preview` covers deterministic in-memory canonical CSV
rendering from that row preview. Tests assert canonical headers, canonical row
values, CSV quoting for commas, quotes, and embedded newlines, deterministic
output, row-error propagation, stable `export_ready_reason` values,
`will_write_files=false`, `can_export=false` for incomplete or invalid previews,
JSON serializability, no file mutation, no solving, and
`row_semantics_validated=false`.

`POST /forecast/demand` covers the deterministic demand forecasting foundation.
Tests assert strict historical demand validation, no bool-as-int acceptance,
duplicate historical slot rejection, deterministic historical-average forecasts,
missing-horizon-slot diagnostics, the 1000-record historical demand cap, the
100-slot forecast horizon cap, JSON serializability, `uses_external_ml=false`,
`uses_external_llm=false`, `will_solve=false`, and
`will_mutate_solver_request=false`. Forecasts are planning evidence only and do
not call `/solve`, `/solve-csv`, or mutate canonical solve requests.

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
- `POST /csv/mapping/suggest`
- `POST /csv/mapping/preview`
- `POST /csv/mapping/rows/preview`
- `POST /csv/mapping/export/preview`
- `POST /forecast/demand`
- `POST /solve-csv`
- `POST /solve-jobs`
- `GET /solve-jobs/{job_id}`

`GET /viewer` redirects to `GET /viewer/`. `GET /viewer/` serves a static
roster viewer for the existing JSON, job, CSV solve, and CSV mapping preview
endpoints. It has no separate build step and does not change solver behavior.
Demo CSV files are available at `/viewer/examples/employees.csv`,
`/viewer/examples/shifts.csv`, and `/viewer/examples/demand.csv` for local
viewer demos. The JSON solve panel includes a `compact`/`standard`/`debug`
response mode selector that updates the canonical request
`options.response_mode` before solving. The CSV Mapping Wizard calls only
`/csv/mapping/suggest`, `/csv/mapping/preview`,
`/csv/mapping/rows/preview`, and `/csv/mapping/export/preview`; it does not
write files, call `/solve-csv`, or submit mapped CSVs to the solver. The viewer
disables action buttons while checks, solves, uploads, previews, or job polling
are running, and uses the Issues tab for API errors, validation rows, and
missing-input messages.
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
explanation, narration, and recommendation helpers. It supports summary,
shortage, assignment, employee, and shift explanation questions, plus
shortage-fix/what-if recommendation questions for the existing
`reduce_shortages` recommendation goal. It uses explicit target patterns only,
plus exact case-insensitive employee name matches when one employee in the solve
request matches. Name matching is exact phrase matching, not substring or fuzzy
matching. It does not use an LLM for routing and does not generate schedules.
For recommendation intents, it calls the deterministic scenario recommendation
engine and summarizes the returned comparison payload. Assistant recommendation
requests may include `limits.max_scenarios` and `limits.max_recommendations`;
those limits pass through to the same validation and capping path used by
`POST /recommendations`. Recommendation errors preserve recommendation endpoint
HTTP semantics through `/assistant/ask`: recommendation and scenario validation
errors return 400, scenario evaluation failures return 500, schema errors
return 400, and oversized JSON requests return 413. Explicit `target` fields
override fields parsed from the question text. Assistant responses include both
`message` and `answer` with the same text. Unsupported or under-specified
questions return `ok=true` with `status=unsupported` and no narration.

`POST /recommendations` and its `POST /recommend/what-if` alias evaluate
deterministic what-if scenarios through the same CP-SAT solver. The current
supported goal is `reduce_shortages`, the response contract version is `1`, and
the response includes `recommendation_type=what_if`. Supported scenario types
are `set_availability`, `increase_employee_max_hours`, and
`add_temporary_employee`. Scenario generation is intentionally narrow and
capped: it tries availability changes for qualified unavailable employees on
shortage slots, minimal max-weekly-hours increases when an otherwise useful
employee is blocked by `exceeds_weekly_hours`, and one synthetic temporary
employee for a shortage slot when no existing-employee scenario is available. It
re-solves each scenario, reports grounded shortage/objective comparisons, and
reports unsolved over-limit candidates in `discarded_scenarios`. Returned
recommendations are also capped through `limits.max_recommendations`; positive
over-limit results are reported in `discarded_recommendations`. It does not use
an LLM, does not generate schedules outside the solver, does not mutate the
original solve request, and does not change normal `/solve` behavior.
Scenario mutation validation rejects missing fields, booleans passed as
integers, non-integer numeric fields, non-boolean availability targets, empty
role strings, and malformed employee max-hours baselines with
`ScenarioValidationError`.

Recommendation contract tests now assert each returned positive recommendation
includes deterministic manager-facing explanation fields: why the scenario
helps, the concrete scenario changes, expected shortage improvement, possible
tradeoffs, and manager next checks. Assistant recommendation tests assert the
assistant summary remains deterministic, JSON-serializable, and uses the same
grounded recommendation payload rather than a separate narration provider.
Grounding contract tests assert returned and discarded recommendations include
`grounding.source=deterministic_scenario_solve`, the evaluated scenario ID,
scenario type, baseline/scenario shortage totals, shortage reduction, and
`uses_external_llm=false`.

Temporary employee hardening tests pin deterministic generation details:
non-colliding `employee_id`, deterministic name, exactly one role matching the
shortage role, availability dimensions matching problem days/shifts with only
the target slot available, `max_weekly_hours` covering the target shift
duration, deterministic `hourly_cost` selection, no temporary scenario when an
availability or max-hours scenario exists for the same slot, and at most one
temporary scenario per day/shift/role shortage slot. Mutation tests also cover
duplicate IDs, missing fields, booleans-as-integers, unknown role/day/shift
targets, negative costs, non-positive hours, valid append behavior, and original
request immutability.

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
