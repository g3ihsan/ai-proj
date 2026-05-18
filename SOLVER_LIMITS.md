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
The deterministic CSV mapper is a pre-validation reporting layer only. It can
suggest mappings from messy headers to canonical employee, shift, and demand
fields, but it does not parse rows, infer staffing demand, mutate files, call an
external LLM/API, or bypass `csv_adapter.py` validation.
`POST /csv/mapping/suggest` exposes only that deterministic report; it does not
run the solver or change `/solve-csv` behavior. It accepts both combined
multi-file header requests and single-dataset `csv_type` plus `headers`
requests.
`POST /csv/mapping/preview` exposes a deterministic single-dataset apply plan
for proposed or inferred header mappings. It reports column rename actions,
canonical headers after apply, missing fields, unmapped headers, and review
warnings with `can_apply`, stable `reason` codes, header-scoped
`adapter_readiness`, `will_mutate_files=false`, and `will_solve=false`. Only
complete plans without unresolved review actions set `can_apply=true`; adapter
readiness only means the previewed headers are ready after the described rename
actions, not that row values have been parsed or validated. Clients must still
apply any file transformation outside the solver and then pass canonical CSV
files through `csv_adapter.py`.
`POST /csv/mapping/rows/preview` transforms supplied sample rows in memory for
inspection only. It accepts at most 20 sample rows per request, validates row
shape and string cells, reports blank required values for mapped canonical
columns with row-level `ready` or `needs_review` status, returns
`row_semantics_validated=false`, does not write files, does not call
`/solve-csv`, and does not parse rows into `ProblemData`; the strict CSV
adapter remains the only path into the solver.
`POST /csv/mapping/export/preview` renders those previewed canonical headers and
rows as in-memory CSV text only. It uses the same 20-row preview limit, reports
`can_export=false` with a stable `export_ready_reason` when the mapping or rows
need review, returns `will_write_files=false`, does not write files, does not
call `/solve-csv`, and does not parse rows into `ProblemData`; the strict CSV
adapter remains the only path into the solver.
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
Response-payload-based CSV helpers render the canonical `solve_payload(...)`
envelope directly, including metric, assignment, shortage, validation, and error
records where present. Metric rows use `status` for the metric name and `value`
for the metric value. They include solver metrics plus business metrics such as
total shortage, labor cost, workload spread, and validation violation count when
those fields are present in the response. All shortage records from the response
are emitted, including zero-shortage records, so downstream readers can
distinguish covered demand from missing rows.
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
Solve responses can be shaped with `response_mode`: `debug` returns the full
solver and explainability payload, `standard` omits debug-level constraint
records and demanded-slot/assignment detail, and `compact` returns only core
metrics, assignments, shortages, violations, and objective breakdown. Response
mode changes serialization only; it does not change solver decisions.
The Solver Evidence Layer is included only in debug responses. It formalizes
post-solve evidence for future AI explanation and recommendation features:
assignment explanations, non-assignment explanations, shortage explanations,
constraint blocker summaries, and a decision evidence summary. This evidence is
computed from the final CP-SAT solution and existing diagnostics after solving.
It does not add constraints, objectives, heuristics, or alternate scheduling
behavior. LLMs must treat this evidence as read-only solver output; they must
not invent assignments, shortages, feasibility status, objective values, or
blocker reasons.
The optional narration layer in `workforce_scheduling.ai_explanations` sits
above deterministic explanation payloads. It can narrate an existing explanation
or build one from `solve_request`, `kind`, and `target` using the deterministic
explanation helpers. Its default and only configured API provider is fake and
deterministic, so no external LLM call is made unless a future integration
explicitly adds and configures one. Narration may rewrite explanation payloads
into clearer language only; it must not generate schedules, change solver
results, provide legal/HR advice, or infer facts absent from solver evidence.
When narration composes deterministic explanations internally, target and schema
errors preserve their original error types instead of being collapsed into a
generic narration error. Narration responses built from `solve_request` include
source metadata with the mode, kind, and normalized target.
The assistant router in `workforce_scheduling.assistant` is deterministic. It
routes only supported manager explanation questions to the existing explanation
and narration helpers, and routes shortage-fix/what-if recommendation questions
to the deterministic recommendation engine. It does not use an LLM for intent
detection, does not perform fuzzy or substring employee-name matching, and
returns an unsupported response rather than guessing missing assignment,
employee, or shift targets. Recommendation answers summarize the returned
scenario comparison payload only; they do not invent changes or generate
rosters. Assistant recommendation limits are passed to the existing
recommendation engine so capping and validation remain centralized at the
deterministic scenario boundary. Recommendation errors keep the same HTTP
status semantics through `/assistant/ask` as they do through
`/recommendations`. Explicit request targets override text-derived targets to
keep caller-provided structured intent authoritative.
The recommendation engine in `workforce_scheduling.recommendations` is also
deterministic. The current goal is limited to `reduce_shortages`; it evaluates
small availability-change scenarios for qualified unavailable employees on
shortage slots, and minimal max-weekly-hours increases for qualified available
employees blocked only by `exceeds_weekly_hours`. When no existing-employee
scenario is available for a shortage slot, it can add one synthetic temporary
employee for that slot. Every scenario re-solves with the existing CP-SAT model.
The recommendation contract version is `1`, and the current scenario types are
`set_availability`, `increase_employee_max_hours`, and
`add_temporary_employee`. Responses include `recommendation_type=what_if`. It is
capped at 5 solved scenarios per request in the in-process prototype;
additional candidates are reported as discarded instead of being solved.
Returned recommendations are capped at 5 as well; positive over-limit results
are reported in `discarded_recommendations`. Recommendations are
decision-support evidence, not automatic roster edits, and they do not add
objectives, constraints, forecasting, or LLM-generated schedule changes.
Recommendation explanation fields are deterministic text derived from scenario
changes and solver comparison numbers. They describe why the scenario helped,
what changed, expected shortage improvement, possible operational tradeoffs,
and manager next checks. They do not introduce an LLM, change solver behavior,
or alter scenario ranking.
Recommendation grounding fields are deterministic metadata derived from the
evaluated scenario and comparison: source, scenario ID, scenario type,
baseline/scenario shortage totals, shortage reduction, and
`uses_external_llm=false`. Grounding is copied onto discarded positive
recommendations as well, without solving or ranking anything differently.
Scenario mutation validation is intentionally strict so malformed scenario
changes fail at the recommendation boundary.

Temporary employee recommendations are deliberately bounded. They generate a
single synthetic employee per shortage day/shift/role only after existing
availability and max-hours candidates are unavailable for that slot. The
temporary employee ID is deterministic and non-colliding, the name is
deterministic, the employee has only the shortage role, and the availability
matrix mirrors the problem dimensions with only the target slot available.
Hourly cost is deterministic: maximum cost among existing employees with the
shortage role, then maximum existing employee cost, then `0` when no valid cost
exists. Applying a scenario uses a copied solve request, so recommendation
evaluation does not mutate the caller's original payload. These scenarios are
comparisons under the current CP-SAT model, not guarantees that the staffing
change is operationally approved. Malformed scenario changes fail as
`ScenarioValidationError` instead of leaking raw Python casting or indexing
errors.
The current evidence contract version is `1`. Public evidence uses stable
uppercase reason codes. Internal lowercase blocker names are deliberately mapped
to those public codes; unknown internal blocker names should fail tests instead
of being silently omitted from evidence. At present, evidence is computed as
part of `solve(...)` before response-mode shaping, so compact and standard
responses hide evidence fields but do not avoid the post-solve evidence
computation cost. That is acceptable for the current sandbox scale. If debug
evidence becomes a measurable bottleneck in larger workloads, the next safe
optimization is an explicit lazy or mode-aware evidence construction path that
preserves the same solved roster and objective values.

`workforce_scheduling.api` is a thin FastAPI wrapper over `solve_payload(...)`.
It exposes `GET /health`, `GET /metadata`, `POST /solve`, deterministic
`POST /explain/*` endpoints, `POST /explain/narrate`, `POST /assistant/ask`,
`POST /csv/mapping/suggest`, `POST /csv/mapping/preview`,
`POST /csv/mapping/rows/preview`, `POST /csv/mapping/export/preview`,
`POST /solve-csv`, `POST /solve-jobs`, `GET /solve-jobs/{job_id}`, and the
static `GET /viewer/` roster viewer. The
explanation endpoints format existing Solver Evidence Layer fields into
manager-readable JSON payloads. They are not LLM endpoints and do not generate
or modify schedules. The narration endpoint formats an existing deterministic
explanation payload using the default fake provider unless a future provider is
explicitly injected. `GET /viewer` redirects to `/viewer/`, and read-only demo
CSV files are available below `/viewer/examples/`. The synchronous solve
endpoint preserves the existing success/error envelope. The CSV upload endpoint
is a thin multipart wrapper around the same three-file CSV adapter and returns
the standard roster CSV as `text/csv`. Every HTTP response includes
`X-Request-ID`; incoming request IDs are preserved, and missing values are
generated per request. Error envelopes include the request ID where practical.
The API logs request method, path, status code, request ID, duration, and
solve-route success/error facts without logging request bodies, uploaded CSV
content, or full solver responses. JSON solve routes reject bodies larger than
1,000,000 bytes before parsing. The CSV upload endpoint rejects any one uploaded
CSV file larger than 1,000,000 bytes before parsing it as solver input. The job
endpoints are an in-process, in-memory prototype for the future async contract
only; jobs disappear when the process restarts and are not a durable queue.
Submitted jobs run in the current process through a bounded
`ThreadPoolExecutor(max_workers=2)`, not a production worker system. The
in-memory store retains at most 100 job records. When full, it prunes oldest
terminal jobs first; if all retained jobs are still active, new job submissions
are rejected instead of evicting queued or running work. It also allows at most
10 active jobs across queued and running states, so API callers cannot create an
unbounded in-process backlog. Job payloads include
`created_at`, `updated_at`, `started_at`, `finished_at`, and `duration_sec`;
queued jobs report `null` for fields that do not exist yet. The API does not add
persistence, auth, websocket delivery, or any new solver behavior. The static
viewer is a local API demonstration surface only and does not add persistent
frontend state, storage, or alternate optimization behavior. Its response mode
selector changes only `options.response_mode` serialization and does not change
optimization decisions. Busy states and the Issues tab are viewer-only
presentation behavior. Viewer-side malformed JSON handling is also presentation
only: it reports an `InvalidJson` issue and does not call the solver.
