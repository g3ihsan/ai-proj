# API Contract

This project exposes a deterministic workforce scheduling solver through three
thin boundaries:

- in-process JSON: `solve_payload(...)`
- HTTP: `workforce_scheduling.api`
- files: JSON request files and three-file CSV input/output
- deterministic explanations: `workforce_scheduling.explanations`
- optional narration: `workforce_scheduling.ai_explanations`
- deterministic assistant routing: `workforce_scheduling.assistant`
- deterministic scenario recommendations: `workforce_scheduling.recommendations`

The solver remains the source of truth. API, job, and CSV surfaces must not add
solver objectives, constraints, persistence, or alternate scheduling behavior.
Recommendations are grounded in scenario solves that reuse the same CP-SAT
solver; they are not LLM guesses.

## Versioning

- `schema_version`: `1`
- Solver Evidence Layer contract version: `1`
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

The optional static viewer is served by the same process at `/viewer/`. Requests
to `/viewer` redirect to `/viewer/` so relative asset paths resolve
consistently. The viewer calls the existing JSON, job, and CSV endpoints only;
it has no persistence, auth, or separate scheduling behavior. Checked-in demo
CSV files are served read-only under `/viewer/examples/`. The viewer exposes a
`compact`/`standard`/`debug` response mode selector for the canonical JSON solve
request, disables action buttons while operations are running, and shows API or
validation problems in an Issues tab.

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

Viewer support routes:

- `GET /viewer` redirects to `/viewer/`
- `GET /viewer/app.js`
- `GET /viewer/styles.css`
- `GET /viewer/examples/employees.csv`
- `GET /viewer/examples/shifts.csv`
- `GET /viewer/examples/demand.csv`

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

### Explanation Endpoints

Explanation endpoints are deterministic wrappers over the Solver Evidence
Layer. They solve the canonical request with debug evidence internally and
return a manager-readable explanation payload in the same envelope shape:

```json
{
  "ok": true,
  "result": {
    "type": "summary_explanation",
    "status": "OPTIMAL",
    "title": "Roster explanation summary",
    "message": "The solver assigned 12 shifts with 2 total shortages.",
    "evidence_contract_version": 1,
    "reason_codes": [],
    "details": {},
    "recommended_next_checks": []
  }
}
```

These endpoints do not call an LLM and do not change solver behavior,
objectives, constraints, fairness, or warm-start behavior.

Endpoints:

- `POST /explain/summary`
- `POST /explain/shortages`
- `POST /explain/assignment`
- `POST /explain/employee`
- `POST /explain/shift`

`/explain/summary` and `/explain/shortages` may accept the canonical solve
request directly. Detail endpoints accept:

```json
{
  "solve_request": {
    "schema_version": 1,
    "problem": {},
    "options": {}
  },
  "target": {
    "employee_id": 0,
    "day": 0,
    "shift": 0,
    "role": "worker"
  }
}
```

Target fields:

- `/explain/assignment`: `employee_id`, `day`, `shift`, `role`
- `/explain/employee`: `employee_id`
- `/explain/shift`: `day`, `shift`, optional `role`

`/explain/assignment` returns `type=assignment_explanation` and
`assigned=true` when the employee was assigned to the requested slot. If the
employee was considered but not assigned, it returns
`type=non_assignment_explanation`, `assigned=false`, the stable reason codes,
and any selected employee ids available from the solver evidence.

Invalid explanation targets return a normal error envelope with
`type=ExplanationQueryError`. Missing evidence for a valid target returns
`type=ExplanationTargetNotFoundError`.

### `POST /explain/narrate`

The narration endpoint is an optional language layer over deterministic
explanation payloads. It can narrate an existing explanation payload, or it can
first build a deterministic explanation from `solve_request`, `kind`, and
optional `target`. It does not change solver output and does not create new
evidence. The default and only configured provider is the deterministic `fake`
provider, so local tests and default operation make no external LLM calls.

Request using an existing explanation:

```json
{
  "explanation": {
    "type": "summary_explanation",
    "status": "OPTIMAL",
    "title": "Roster explanation summary",
    "message": "The solver assigned 2 shifts with 0 total shortages.",
    "evidence_contract_version": 1,
    "reason_codes": [],
    "details": {},
    "recommended_next_checks": []
  }
}
```

Request using a solve request and explanation kind:

```json
{
  "solve_request": {
    "schema_version": 1,
    "problem": {},
    "options": {}
  },
  "kind": "assignment",
  "target": {
    "employee_id": 0,
    "day": 0,
    "shift": 0,
    "role": "worker"
  },
  "provider": "fake"
}
```

Supported `kind` values:

- `summary`
- `shortages`
- `assignment`
- `employee`
- `shift`

`assignment`, `employee`, and `shift` use the same target fields as the
deterministic `/explain/*` endpoints. `provider` is optional and currently only
supports `fake`. Unknown providers are rejected instead of falling back to an
external service.

Success:

```json
{
  "ok": true,
  "result": {
    "type": "explanation_narration",
    "source_explanation_type": "summary_explanation",
    "status": "OPTIMAL",
    "title": "Manager-facing explanation narration",
    "message": "The solver assigned 2 shifts with 0 total shortages. Solver status: OPTIMAL.",
    "evidence_contract_version": 1,
    "reason_codes": [],
    "provider": {
      "name": "fake",
      "uses_external_llm": false
    },
    "grounding": {},
    "source": {
      "mode": "solve_request",
      "kind": "summary",
      "target": {}
    }
  }
}
```

The narration prompt explicitly instructs any future provider to use only the
provided evidence, avoid invented facts, avoid changing assignments or
shortages, avoid legal/HR advice, and avoid claiming optimality unless
`status=OPTIMAL`. `source` is included only when narration was built from
`solve_request`, `kind`, and `target`; direct explanation-payload narration does
not add source metadata.

Narration errors preserve the narrowest available error type:

- invalid narration request shape: `ExplanationNarrationError`, HTTP 400
- invalid target shape: `ExplanationQueryError`, HTTP 400
- valid target with no deterministic evidence: `ExplanationTargetNotFoundError`, HTTP 404
- invalid solve request/schema: original schema/solve validation error, HTTP 400
- provider failure: `NarrationProviderError`, HTTP 502

### `POST /assistant/ask`

The assistant endpoint is a deterministic router for manager explanation and
shortage recommendation questions. It does not use an LLM for intent detection.
For explanation intents, it builds the matching deterministic explanation,
narrates it with the configured narration provider, and returns one grounded
assistant response. For recommendation intents, it calls the deterministic
scenario recommendation engine and summarizes that returned comparison payload.

Combined request:

```json
{
  "question": "Why was employee 0 assigned to day 0 shift 0 as worker?",
  "solve_request": {
    "schema_version": 1,
    "problem": {},
    "options": {}
  },
  "target": {
    "employee_id": 0,
    "day": 0,
    "shift": 0,
    "role": "worker"
  },
  "provider": "fake"
}
```

Recommendation request with limits:

```json
{
  "question": "What should I change to reduce shortages?",
  "solve_request": {
    "schema_version": 1,
    "problem": {},
    "options": {}
  },
  "limits": {
    "max_scenarios": 5,
    "max_recommendations": 1
  }
}
```

Supported routed intents:

- `summary`
- `shortages`
- `assignment`
- `employee`
- `shift`
- `recommendations`

The router extracts only explicit target fields such as `employee 0`, `day 0`,
`shift 1`, `role worker`, `as worker`, or `for worker`. It may resolve an exact
case-insensitive employee name from the solve request when exactly one employee
matches. Employee names use exact phrase matching and are not matched as
substrings inside unrelated words. It does not fuzzy match names. Ambiguous
duplicate names are rejected.
When `target` is provided in the request, those explicit target fields override
any fields parsed from the question text.

Recommendation questions must still be shortage-fix or what-if questions for
the currently supported `reduce_shortages` goal. They do not generate rosters or
invent scenario changes; `/assistant/ask` routes them to the same deterministic
engine used by `POST /recommendations` and returns that payload in
`recommendation`. `limits.max_scenarios` and `limits.max_recommendations` are
passed through to the recommendation engine and validated there.

If the question is unsupported or lacks required target fields, the endpoint
returns `ok=true` with `status=unsupported` and no narration. Request-shape
errors return `AssistantIntentError`. Assistant responses include both
`message` and `answer`; these fields contain the same text so future clients can
use `answer` without breaking older clients that read `message`.

Recommendation errors preserve the same HTTP semantics as
`POST /recommendations`: `RecommendationError` and `ScenarioValidationError`
return HTTP 400, `ScenarioEvaluationError` returns HTTP 500, schema validation
errors return HTTP 400, and oversized JSON requests return HTTP 413.

### `POST /recommendations`

Alias:

- `POST /recommend/what-if`

The recommendation endpoint evaluates small deterministic what-if scenarios
against a baseline solve request. The response keeps
`type=scenario_recommendations` for backward compatibility and includes
`recommendation_type=what_if` plus `recommendation_contract_version=1`.
Returned recommendation entries include a deterministic `explanation` object
with `why_it_helps`, `what_changes`, `expected_improvement`, `tradeoffs`, and
`manager_next_checks`. These fields are generated from the scenario change and
solver comparison payload only; no external LLM or ungrounded planner is used.
Each returned recommendation also includes recommendation-level `grounding`
metadata with the deterministic source, scenario ID, scenario type, baseline
shortage, scenario shortage, shortage reduction, and `uses_external_llm=false`.
Version 1 supports only:

- `reduce_shortages`

Supported scenario types:

- `set_availability`
- `increase_employee_max_hours`
- `add_temporary_employee`

The first scenario generator is intentionally narrow: for shortage slots, it
tries small deterministic changes and re-solves each candidate with the existing
CP-SAT model. Version 1 scenarios can make a qualified but unavailable employee
available for one day/shift, or minimally increase a qualified and available
employee's `max_weekly_hours` when shortage evidence shows
`exceeds_weekly_hours` is the local blocker. If neither existing-employee
scenario exists for a shortage slot, it can add one synthetic temporary employee
for that slot using the canonical employee schema with `hourly_cost`. It reports
only grounded comparisons. It does not change solver objectives, constraints,
fairness behavior, warm-start behavior, or normal `/solve` requests.

Temporary employee scenarios are deterministic. The generated `employee_id` is
one greater than the maximum existing employee ID, so it does not collide with
the input roster. The generated name is `Temporary {role} day {day} shift
{shift}`. The synthetic employee has exactly one role, matching the shortage
role, and an availability matrix with the same day/shift dimensions as the
problem where only the target shortage day/shift is `true`. Its
`max_weekly_hours` is set to at least the target shift duration. Its
`hourly_cost` uses the maximum existing cost among employees with the shortage
role, falls back to the maximum existing employee cost when no employee has that
role, and falls back deterministically to `0` when no valid existing cost is
available.

Request:

```json
{
  "goal": "reduce_shortages",
  "solve_request": {
    "schema_version": 1,
    "problem": {},
    "options": {}
  },
  "limits": {
    "max_scenarios": 5,
    "max_recommendations": 5
  }
}
```

The legacy top-level `max_scenarios` field remains accepted for backward
compatibility. When `limits.max_scenarios` is present, it is authoritative.

Success:

```json
{
  "ok": true,
  "result": {
    "type": "scenario_recommendations",
    "recommendation_type": "what_if",
    "recommendation_contract_version": 1,
    "goal": "reduce_shortages",
    "baseline": {
      "status": "OPTIMAL",
      "total_shortage": 1
    },
    "recommendations": [],
    "evaluated_scenarios": [],
    "discarded_scenarios": [],
    "discarded_recommendations": [],
    "summary": {
      "baseline_total_shortage": 1,
      "generated_scenario_count": 0,
      "scenario_count": 0,
      "discarded_scenario_count": 0,
      "generated_recommendation_count": 0,
      "recommendation_count": 0,
      "discarded_recommendation_count": 0,
      "best_shortage_reduction": 0
    },
    "limits": {
      "max_scenarios": 5,
      "max_recommendations": 5,
      "scenario_limit_reached": false,
      "recommendation_limit_reached": false
    },
    "metadata": {
      "engine": "deterministic_scenario_recommendations",
      "recommendation_type": "what_if",
      "recommendation_contract_version": 1,
      "supported_scenario_types": [
        "set_availability",
        "increase_employee_max_hours",
        "add_temporary_employee"
      ],
      "uses_external_llm": false,
      "changes_solver_behavior": false
    }
  }
}
```

Recommendation entry example:

```json
{
  "scenario_id": "add_temporary_employee_3_day_0_shift_1_role_worker",
  "title": "Add temporary employee 3 for day 0 shift 1 as worker",
  "message": "This scenario reduces total shortage by 1.",
  "changes": [
    {
      "type": "add_temporary_employee",
      "employee_id": 3,
      "name": "Temporary worker day 0 shift 1",
      "role": "worker",
      "day": 0,
      "shift": 1,
      "hourly_cost": 20,
      "max_weekly_hours": 8
    }
  ],
  "comparison": {
    "shortage_reduction": 1,
    "baseline_total_shortage": 2,
    "scenario_total_shortage": 1
  },
  "grounding": {
    "source": "deterministic_scenario_solve",
    "scenario_id": "add_temporary_employee_3_day_0_shift_1_role_worker",
    "scenario_type": "add_temporary_employee",
    "baseline_total_shortage": 2,
    "scenario_total_shortage": 1,
    "shortage_reduction": 1,
    "uses_external_llm": false
  },
  "explanation": {
    "why_it_helps": "The baseline had an uncovered worker requirement on day 0 shift 1. No existing-employee scenario was available for that slot, so this scenario adds one qualified temporary employee and re-solves.",
    "what_changes": [
      "Adds temporary employee 3 with role worker.",
      "Makes the temporary employee available only for day 0 shift 1."
    ],
    "expected_improvement": "Total shortage decreases from 2 to 1.",
    "tradeoffs": [
      "May increase staffing cost because an additional employee is introduced."
    ],
    "manager_next_checks": [
      "Confirm a temporary worker is actually available.",
      "Confirm the temporary staffing cost is acceptable.",
      "Confirm the change is operationally feasible before editing the roster.",
      "Confirm this change follows local staffing policy."
    ]
  }
}
```

`max_scenarios` is optional and capped at 5 for this in-process prototype.
Candidate scenarios beyond the requested `max_scenarios` are not solved; they
are returned in `discarded_scenarios` with `status=discarded` and
`reason=MAX_SCENARIO_LIMIT`.
`max_recommendations` is optional and capped at 5. Positive scenario results
beyond that returned recommendation cap are reported in
`discarded_recommendations` with `status=discarded` and
`reason=MAX_RECOMMENDATION_LIMIT`; discarded recommendation entries preserve
the same deterministic `grounding` and `explanation` objects so clients can
show why a capped recommendation would have helped without treating it as
returned top-N advice.
Unsupported goals or invalid recommendation request shapes return
`RecommendationError` with HTTP 400. Invalid solve request/schema errors retain
their original schema error type with HTTP 400. Internal scenario solve failures
return `ScenarioEvaluationError` with HTTP 500. Scenario generation is decision
support only: managers must still confirm whether the proposed availability
change is operationally valid before using it as real input.
Scenario mutation validation is strict: required change fields must be present,
integer fields reject booleans and non-integers, boolean fields must be real
booleans, role strings must be non-empty when provided, and malformed scenario
changes return `ScenarioValidationError` with HTTP 400. Temporary employee
changes also reject duplicate employee IDs, unknown roles, unknown day/shift
targets, negative `hourly_cost`, and non-positive `max_weekly_hours`.
Recommendation evaluation deep-copies the solve request before applying
scenario changes, so the original request payload is not mutated.
Recommendations are scenario comparisons and decision-support evidence, not
guarantees that managers should apply the change without operational review.
Assistant recommendation answers summarize this deterministic recommendation
payload and include the first grounded manager next-check from the best returned
recommendation.

### `POST /csv/mapping/suggest`

Returns deterministic CSV header mapping suggestions before canonical CSV
validation. This endpoint does not parse row data, mutate uploads, run the
solver, call an external LLM/API, or replace `POST /solve-csv`.

Request:

```json
{
  "employee_headers": [
    "Employee Number",
    "Resource Name",
    "Job Title",
    "Cost Per Hour",
    "Weekly Max Hours",
    "Avail D0 S0"
  ],
  "demand_headers": [
    "Weekday",
    "Time Slot",
    "Coverage Role",
    "Workers Needed"
  ],
  "shift_headers": [
    "Shift Number",
    "Period Name",
    "From Hour",
    "To Hour"
  ]
}
```

At least one of `employee_headers`, `demand_headers`, or `shift_headers` must
be provided. Each present value must be a non-empty list of unique headers after
normalization.

Single-dataset request:

```json
{
  "csv_type": "employees",
  "headers": [
    "Staff ID",
    "Full Name",
    "Skills",
    "Hourly Rate",
    "Weekly Hours Limit",
    "Available Monday Morning"
  ]
}
```

`csv_type` must be one of `employees`, `demand`, or `shifts`. The response shape
is still `csv_mapping_report`; single-dataset requests return one entry in
`files`.

Response:

```json
{
  "ok": true,
  "result": {
    "type": "csv_mapping_report",
    "csv_mapping_contract_version": 1,
    "status": "complete",
    "uses_external_llm": false,
    "files": {
      "employees": {
        "type": "csv_column_mapping",
        "csv_type": "employees",
        "valid": true,
        "mapping": {
          "employee_id": {
            "status": "mapped",
            "source_header": "Employee Number",
            "normalized_header": "employee_number",
            "confidence": 0.95
          }
        },
        "missing_fields": [],
        "unmapped_headers": [],
        "warnings": [],
        "uses_external_llm": false
      }
    }
  }
}
```

If any file is incomplete, the endpoint still returns HTTP 200 with
`status=needs_review` and per-file `missing_fields`. Invalid request shape,
empty header lists, duplicate normalized headers, or non-string headers return
HTTP 400 with `CsvMappingValidationError` or `CsvMappingError`. Oversized JSON
requests return HTTP 413.

### `POST /csv/mapping/preview`

Returns a deterministic preview/apply plan for one dataset. This endpoint shows
how a proposed or inferred header mapping would rename columns before canonical
CSV parsing. It does not mutate uploaded files, parse row data, run the solver,
call an external LLM/API, or change `/solve-csv` behavior.

Request with explicit mapping:

```json
{
  "csv_type": "employees",
  "headers": [
    "Staff ID",
    "Full Name",
    "Skills",
    "Cost Per Hour",
    "Weekly Limit",
    "Available Day0 Shift0"
  ],
  "mapping": {
    "employee_id": "Staff ID",
    "name": "Full Name",
    "roles": "Skills",
    "hourly_cost": "Cost Per Hour",
    "max_weekly_hours": "Weekly Limit",
    "availability": ["Available Day0 Shift0"]
  }
}
```

`mapping` is optional. If omitted, the endpoint uses the deterministic mapper
suggestions. `mapping_report` may also be supplied instead of `mapping`.
Supplying both returns HTTP 400. Partial mappings are allowed and return
`status=needs_review` with `missing_fields`.

Response:

```json
{
  "ok": true,
  "result": {
    "type": "csv_mapping_preview",
    "csv_mapping_contract_version": 1,
    "status": "complete",
    "csv_type": "employees",
    "headers": ["Staff ID", "Full Name"],
    "uses_external_llm": false,
    "will_mutate_files": false,
    "will_solve": false,
    "mapping": {
      "type": "csv_column_mapping"
    },
    "apply_plan": {
      "type": "csv_mapping_apply_plan",
      "status": "complete",
      "can_apply": true,
      "reason": "ready",
      "adapter_readiness": {
        "scope": "headers_only",
        "headers_ready_for_csv_adapter": true,
        "row_data_validated": false,
        "reason": "ready"
      },
      "will_mutate_files": false,
      "will_solve": false,
      "column_renames": [
        {
          "canonical_field": "employee_id",
          "source_header": "Staff ID",
          "target_header": "employee_id",
          "normalized_source_header": "staff_id",
          "action": "rename_column"
        }
      ],
      "canonical_headers_after_apply": ["employee_id", "name"],
      "missing_fields": [],
      "unmapped_headers": [],
      "warnings": []
    }
  }
}
```

Employee availability preview distinguishes adapter-ready headers from advisory
headers. Compact `availability` remains compact and carries the existing matrix
warning. Explicit day/shift variants such as `Avail D0 S0` are previewed as
`available_day0_shift0`. Day-name variants such as `Available Monday Morning`
are recognized as availability evidence but return `action=requires_review`
because they do not encode the zero-based day/shift indexes required by
`csv_adapter.py`. `apply_plan.can_apply` is true only when the plan is complete,
has no unresolved review actions, and has no duplicate target headers;
`needs_review` plans set `can_apply=false`. `apply_plan.reason` is a stable
code: `ready`, `missing_required_fields`, `requires_review`, or
`duplicate_target_headers`. `adapter_readiness` is header-scoped only:
`headers_ready_for_csv_adapter` mirrors `can_apply`, `row_data_validated` is
always false in this phase, and the preview still does not parse or validate
row values. Supplied `mapping_report` objects are validated before preview use;
reports with mismatched CSV type, unsupported fields, inconsistent
`missing_fields`, duplicate source headers, or `uses_external_llm=true` return
HTTP 400.

### `POST /csv/mapping/rows/preview`

Returns a deterministic sample row transformation preview for one CSV dataset.
This endpoint applies a proposed mapping or validated apply plan to provided
sample rows. It does not write files, mutate uploads, parse rows through
`csv_adapter.py`, run `/solve-csv`, run the solver, or call an external
LLM/API.

Request:

```json
{
  "csv_type": "employees",
  "headers": ["Staff ID", "Full Name", "Skills", "Cost Per Hour"],
  "rows": [
    ["E1", "Asha", "worker|supervisor", "20"],
    ["E2", "Ravi", "worker", "18"]
  ],
  "mapping": {
    "employee_id": "Staff ID",
    "name": "Full Name",
    "roles": "Skills",
    "hourly_cost": "Cost Per Hour"
  }
}
```

`mapping`, `mapping_report`, or `apply_plan` may be supplied. `apply_plan`
inputs must already be complete and validated by the header preview contract.
When `mapping` is partial, row transformation can still be previewed, but the
response remains `status=needs_review` because the resulting CSV is not yet
adapter-ready. At most 20 sample rows may be previewed in one request; larger
requests return HTTP 400 with `CsvMappingValidationError` and are not truncated.

Response:

```json
{
  "ok": true,
  "result": {
    "type": "csv_row_transformation_preview",
    "csv_mapping_contract_version": 1,
    "status": "needs_review",
    "csv_type": "employees",
    "limits": {
      "max_preview_rows": 20,
      "row_limit_reached": false
    },
    "row_count": 2,
    "previewed_row_count": 2,
    "can_transform_rows": true,
    "row_shape_validated": true,
    "row_data_validated": true,
    "required_values_checked": true,
    "required_value_errors": [],
    "row_semantics_validated": false,
    "uses_external_llm": false,
    "will_mutate_files": false,
    "will_solve": false,
    "transformed_headers": ["employee_id", "name", "roles", "hourly_cost"],
    "transformed_rows": [
      {
        "row_index": 0,
        "status": "ready",
        "source": {
          "Staff ID": "E1",
          "Full Name": "Asha",
          "Skills": "worker|supervisor",
          "Cost Per Hour": "20"
        },
        "transformed": {
          "employee_id": "E1",
          "name": "Asha",
          "roles": "worker|supervisor",
          "hourly_cost": "20"
        },
        "transformed_values": ["E1", "Asha", "worker|supervisor", "20"],
        "errors": []
      }
    ],
    "errors": [],
    "warnings": []
  }
}
```

Rows must be supplied as a non-empty array of at most 20 string arrays with the
same width as `headers`. `row_shape_validated=true` means every previewed row
has the same column count as `headers` and contains only strings. Row preview
also performs lightweight required-value checks for mapped canonical columns and
explicit availability columns; blank required values are reported in both
`required_value_errors` and the top-level `errors` list, are attached to the
affected transformed row, and keep `status=needs_review`. Each transformed row
also has a row-level `status`: `ready` when that row has no preview errors, or
`needs_review` when row-specific errors are present.
`row_semantics_validated=false` means integer fields, booleans, compact
availability matrices, and role values are still validated later by the strict
CSV adapter.

### `POST /csv/mapping/export/preview`

Returns a deterministic in-memory canonical CSV export preview for one CSV
dataset. This endpoint applies the same row transformation preview contract and
then renders the previewed canonical headers and rows as CSV text. It does not
write files, mutate uploads, parse rows through `csv_adapter.py`, run
`/solve-csv`, run the solver, or call an external LLM/API.

Request:

```json
{
  "csv_type": "demand",
  "headers": ["Day Index", "Shift Name", "Required Role", "Headcount"],
  "rows": [["0", "morning", "worker", "2"]]
}
```

`mapping`, `mapping_report`, or `apply_plan` may be supplied with the same rules
as `POST /csv/mapping/rows/preview`.

Response:

```json
{
  "ok": true,
  "result": {
    "type": "csv_canonical_export_preview",
    "csv_mapping_contract_version": 1,
    "status": "complete",
    "csv_type": "demand",
    "limits": {
      "max_preview_rows": 20,
      "row_limit_reached": false
    },
    "row_count": 1,
    "previewed_row_count": 1,
    "can_export": true,
    "canonical_headers": ["day", "shift", "role", "required"],
    "canonical_rows": [["0", "morning", "worker", "2"]],
    "csv_text": "day,shift,role,required\n0,morning,worker,2\n",
    "line_count": 2,
    "row_preview": {
      "type": "csv_row_transformation_preview"
    },
    "errors": [],
    "warnings": [],
    "row_shape_validated": true,
    "row_data_validated": true,
    "required_values_checked": true,
    "row_semantics_validated": false,
    "uses_external_llm": false,
    "will_mutate_files": false,
    "will_write_files": false,
    "will_solve": false
  }
}
```

`can_export=true` only when the underlying row preview is complete, all previewed
rows are ready, and no preview errors exist. Incomplete mappings or row-level
preview errors still return deterministic `canonical_headers`, `canonical_rows`,
and `csv_text` for inspection, but set `status=needs_review` and
`can_export=false`. `will_write_files=false` and `will_mutate_files=false`
mean the endpoint only returns preview data and CSV text in the response.
`row_semantics_validated=false` still means the strict CSV adapter remains
responsible for parsing integers, booleans, roles, and availability semantics
before anything can reach the solver.

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
- `non_assignment_explanations`
- `shortage_explanations`
- `constraint_blockers`
- `decision_evidence_summary`

Changing `response_mode` changes serialization only. It does not change solver
decisions.

## Solver Evidence Layer

The Solver Evidence Layer is a debug-mode-only contract for future AI
explanation, schedule-assistant, and what-if features. It is generated after
CP-SAT solves from the final assignments, shortages, diagnostics, validation
facts, and objective breakdown. It does not change the model, objective,
constraints, warm-start behavior, or selected roster.

LLMs must consume this evidence as read-only source-of-truth context. They must
not directly generate schedules, feasibility status, shortages, objective
values, or blocker reasons.

Evidence is post-solve local explanation evidence, not a formal global
infeasibility proof. Lowercase internal blocker names are mapped deliberately to
the stable uppercase public reason codes below. A new internal hard-constraint
blocker must update that public mapping and its tests; unknown blockers should
not be silently omitted.

Stable reason codes include:

- `ASSIGNED_AVAILABLE`
- `ASSIGNED_QUALIFIED`
- `ASSIGNED_WITHIN_HOURS`
- `ASSIGNED_REST_COMPATIBLE`
- `ASSIGNED_COVERED_DEMAND`
- `ASSIGNED_COST_CONTRIBUTION`
- `BLOCKED_UNAVAILABLE`
- `BLOCKED_MISSING_ROLE`
- `BLOCKED_MAX_HOURS`
- `BLOCKED_ONE_SHIFT_PER_DAY`
- `BLOCKED_REST_RULE`
- `BLOCKED_CLOSING_TO_OPENING`
- `BLOCKED_MAX_CONSECUTIVE_DAYS`
- `BLOCKED_HIGHER_COST_THAN_SELECTED`
- `NOT_SELECTED_BY_FINAL_OBJECTIVE`
- `SHORTAGE_INSUFFICIENT_AVAILABLE_QUALIFIED`
- `SHORTAGE_REST_CONFLICT`
- `SHORTAGE_MAX_HOURS_LIMIT`
- `SHORTAGE_DEMAND_EXCEEDS_CAPACITY`
- `SHORTAGE_LOCAL_ASSIGNMENT_CONFLICT`

Example debug response snippet:

```json
{
  "assignment_explanations": [
    {
      "employee_id": 0,
      "day": 0,
      "shift": 0,
      "role": "worker",
      "reason_codes": [
        "ASSIGNED_AVAILABLE",
        "ASSIGNED_QUALIFIED",
        "ASSIGNED_WITHIN_HOURS",
        "ASSIGNED_REST_COMPATIBLE",
        "ASSIGNED_COVERED_DEMAND",
        "ASSIGNED_COST_CONTRIBUTION"
      ]
    }
  ],
  "shortage_explanations": [
    {
      "day": 0,
      "shift": 0,
      "role": "worker",
      "required_count": 3,
      "assigned_count": 1,
      "shortage_count": 2,
      "available_qualified_count": 1,
      "blocker_counts": {
        "BLOCKED_MISSING_ROLE": 1,
        "BLOCKED_UNAVAILABLE": 1
      }
    }
  ],
  "decision_evidence_summary": {
    "source": "cp_sat_solver_post_solve_evidence",
    "objective_priority": [
      "MINIMIZE_TOTAL_SHORTAGE",
      "MINIMIZE_FAIRNESS_PENALTY",
      "MINIMIZE_LABOR_COST"
    ]
  }
}
```

## CSV Input Contract

CSV input uses exactly three files.

The optional deterministic CSV schema mapper in
`workforce_scheduling.csv_mapper` can inspect messy headers before validation
and suggest mappings to the canonical CSV fields. It does not parse rows, solve
schedules, mutate CSV files, call an external LLM/API, or replace the strict CSV
adapter. Mapper reports include `uses_external_llm=false`, suggested source
headers, normalized headers, confidence scores, missing fields, unmapped
headers, warnings, and validation status. Incomplete reports are advisory and
must be reviewed before files are renamed or transformed into the canonical CSV
contract below. The optional preview/apply-plan endpoint shows deterministic
column rename actions, `canonical_headers_after_apply`, unmapped headers,
missing fields, review warnings, `can_apply`, `reason`, header-scoped
`adapter_readiness`, and explicit `will_mutate_files=false` /
`will_solve=false` flags. The row transformation preview applies those header
plans to sample rows and reports transformed row dictionaries and row-shape
errors, but it still does not parse rows through `csv_adapter.py`, write files,
or solve schedules.

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

Common employee header variants recognized by the mapper include `Staff ID`,
`Employee Name`, `Skills`, `Hourly Rate`, `Pay Rate`, `Weekly Hours Limit`,
`Max Hours`, compact `Availability`, and explicit `Available Day0 Shift0`
style availability columns. Day-name availability variants such as `Available
Monday Morning`, `Can Work Tue Evening`, and `Avail Fri Night` are also
recognized as availability headers for mapping purposes.

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

Common shift header variants recognized by the mapper include `Shift ID`,
`Shift Label`, `Start Time`, and `End Time`.

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

Common demand header variants recognized by the mapper include `Day Index`,
`Shift Name`, `Required Role`, `Demand`, and `Headcount`.

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
