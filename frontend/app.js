const sampleRequest = {
  schema_version: 1,
  problem: {
    employees: [
      {
        employee_id: 0,
        name: "E0",
        roles: ["worker"],
        hourly_cost: 20,
        max_weekly_hours: 40,
        availability: [[true], [true]],
      },
      {
        employee_id: 1,
        name: "E1",
        roles: ["worker"],
        hourly_cost: 20,
        max_weekly_hours: 40,
        availability: [[true], [true]],
      },
    ],
    roles: ["worker"],
    days: [0, 1],
    shifts: ["shift_0"],
    shift_start_hours: [8],
    shift_end_hours: [16],
    min_rest_hours: 8,
    max_consecutive_days: 5,
    shortage_penalty: 1000,
    demand: [
      { day: 0, shift: 0, role: "worker", required: 1 },
      { day: 1, shift: 0, role: "worker", required: 1 },
    ],
    hint_assignments: [],
  },
  options: {
    time_limit_sec: 5.0,
    seed: 1,
    use_warm_start: false,
    response_mode: "standard",
  },
};

const mappingSamples = {
  employees: {
    headers: "Staff ID,Full Name,Skills,Cost Per Hour,Weekly Hours Limit,Available Day0 Shift0",
    rows: 'E1,"Asha, Lead",worker|supervisor,20,40,yes\nE2,Ravi,worker,18,40,no',
    mapping: {
      employee_id: "Staff ID",
      name: "Full Name",
      roles: "Skills",
      hourly_cost: "Cost Per Hour",
      max_weekly_hours: "Weekly Hours Limit",
      availability: ["Available Day0 Shift0"],
    },
  },
  demand: {
    headers: "Day Index,Shift Name,Required Role,Headcount",
    rows: "0,morning,worker,2\n1,evening,supervisor,1",
    mapping: {
      day: "Day Index",
      shift: "Shift Name",
      role: "Required Role",
      required: "Headcount",
    },
  },
  shifts: {
    headers: "Shift No,Shift Label,Starts At,Ends At",
    rows: "0,morning,8,16\n1,evening,16,24",
    mapping: {
      shift: "Shift No",
      shift_name: "Shift Label",
      start_hour: "Starts At",
      end_hour: "Ends At",
    },
  },
};

const exportPreviewEmptyState =
  "No canonical CSV export preview yet. Use Preview Export after headers, rows, and mapping are ready.";
const missingCanonicalCsvPreviewMessages = {
  copy: "No canonical CSV export preview is available to copy.",
  download: "No canonical CSV export preview is available to download.",
};
const clipboardUnavailableMessage =
  "Browser clipboard API is unavailable. Copy the canonical CSV text manually.";

const state = {
  activeTab: "assignments",
  canonicalCsvText: "",
  csvText: "",
  demoCsvFiles: {},
  isBusy: false,
  rows: {
    assignments: [],
    shortages: [],
    metrics: [],
    issues: [],
  },
};

const elements = {
  apiBase: document.querySelector("#api-base"),
  checkApi: document.querySelector("#check-api"),
  serviceDot: document.querySelector("#service-dot"),
  serviceStatus: document.querySelector("#service-status"),
  metadataGrid: document.querySelector("#metadata-grid"),
  jsonRequest: document.querySelector("#json-request"),
  responseMode: document.querySelector("#response-mode"),
  loadSampleJson: document.querySelector("#load-sample-json"),
  loadDemoCsvs: document.querySelector("#load-demo-csvs"),
  csvDemoStatus: document.querySelector("#csv-demo-status"),
  solveJson: document.querySelector("#solve-json"),
  solveCsv: document.querySelector("#solve-csv"),
  submitJob: document.querySelector("#submit-job"),
  downloadCsv: document.querySelector("#download-csv"),
  mappingCsvType: document.querySelector("#mapping-csv-type"),
  mappingHeaders: document.querySelector("#mapping-headers"),
  mappingRows: document.querySelector("#mapping-rows"),
  mappingJson: document.querySelector("#mapping-json"),
  loadMappingSample: document.querySelector("#load-mapping-sample"),
  suggestMapping: document.querySelector("#suggest-mapping"),
  clearMappingWizard: document.querySelector("#clear-mapping-wizard"),
  previewApplyPlan: document.querySelector("#preview-apply-plan"),
  previewRowTransform: document.querySelector("#preview-row-transform"),
  previewExport: document.querySelector("#preview-export"),
  copyCanonicalCsv: document.querySelector("#copy-canonical-csv"),
  downloadCanonicalCsv: document.querySelector("#download-canonical-csv"),
  mappingSummary: document.querySelector("#mapping-summary"),
  mappingOutput: document.querySelector("#mapping-output"),
  exportOutput: document.querySelector("#export-output"),
  exportSafetyFlags: document.querySelector("#export-safety-flags"),
  summaryStrip: document.querySelector("#summary-strip"),
  resultHead: document.querySelector("#result-head"),
  resultBody: document.querySelector("#result-body"),
  messageLog: document.querySelector("#message-log"),
};

function defaultApiBase() {
  if (window.location.protocol === "file:") {
    return "http://localhost:8000";
  }
  return window.location.origin;
}

function requestId() {
  return `viewer-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function log(message, payload) {
  const suffix = payload ? `\n${JSON.stringify(payload, null, 2)}` : "";
  elements.messageLog.textContent = `${new Date().toLocaleTimeString()} ${message}${suffix}`;
}

function logError(prefix, error, context = {}) {
  const payload = {
    type: error.type || error.name || "Error",
    message: error.message,
    status: error.status || "",
    request_id: error.request_id || "",
    ...context,
  };
  log(prefix, payload);
}

function setBusy(isBusy, statusText = "") {
  state.isBusy = isBusy;
  [
    elements.checkApi,
    elements.loadDemoCsvs,
    elements.solveJson,
    elements.solveCsv,
    elements.submitJob,
    elements.loadMappingSample,
    elements.suggestMapping,
    elements.clearMappingWizard,
    elements.previewApplyPlan,
    elements.previewRowTransform,
    elements.previewExport,
  ].forEach((button) => {
    button.disabled = isBusy;
  });
  elements.downloadCsv.disabled = isBusy || !state.csvText;
  updateCanonicalCsvActions();
  elements.serviceDot.classList.toggle("busy", isBusy);
  if (statusText) {
    elements.serviceStatus.textContent = statusText;
  }
}

function updateCanonicalCsvActions() {
  elements.copyCanonicalCsv.disabled = state.isBusy;
  elements.downloadCanonicalCsv.disabled = state.isBusy;
}

async function withBusy(statusText, operation) {
  if (state.isBusy) {
    return undefined;
  }
  setBusy(true, statusText);
  try {
    return await operation();
  } finally {
    setBusy(false);
  }
}

function apiUrl(path) {
  return `${elements.apiBase.value.replace(/\/$/, "")}${path}`;
}

async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Request-ID", requestId());
  return fetch(apiUrl(path), { ...options, headers });
}

function setServiceStatus(ok, text) {
  elements.serviceDot.classList.toggle("ok", ok);
  elements.serviceDot.classList.toggle("error", !ok);
  elements.serviceStatus.textContent = text;
}

function metricCard(label, value) {
  return `<div class="metric-card"><span>${escapeHtml(String(label))}</span><b>${escapeHtml(String(value ?? ""))}</b></div>`;
}

function renderSummary(summary = {}) {
  elements.summaryStrip.innerHTML = [
    metricCard("Status", summary.status || "Not solved"),
    metricCard("Assignments", summary.assignmentCount ?? 0),
    metricCard("Total shortage", summary.totalShortage ?? 0),
    metricCard("Labor cost", summary.laborCost ?? ""),
  ].join("");
}

function renderTable() {
  const rows = state.rows[state.activeTab] || [];
  const columns = {
    assignments: ["employee_id", "name", "day", "shift", "shift_name", "role", "status"],
    shortages: ["day", "shift", "shift_name", "role", "status", "value", "message"],
    metrics: ["status", "value", "message"],
    issues: ["type", "status", "message", "request_id"],
  }[state.activeTab];

  elements.resultHead.innerHTML = `<tr>${columns.map((column) => `<th>${column}</th>`).join("")}</tr>`;
  elements.resultBody.innerHTML = rows.length
    ? rows
        .map(
          (row) =>
            `<tr>${columns
              .map((column) => `<td>${escapeHtml(String(row[column] ?? ""))}</td>`)
              .join("")}</tr>`,
        )
        .join("")
    : `<tr><td colspan="${columns.length}">No ${state.activeTab} to show.</td></tr>`;
}

function setRowsFromSolveResult(result) {
  const request = currentSolveRequest({ fallbackToSample: true });
  const employeeNames = new Map(
    (request.problem?.employees || []).map((employee) => [
      employee.employee_id,
      employee.name,
    ]),
  );
  const shiftNames = request.problem?.shifts || [];
  state.rows.assignments = (result.assignments || []).map((assignment) => ({
    ...assignment,
    name: employeeNames.get(assignment.employee_id) || "",
    shift_name: shiftNames[assignment.shift] || "",
    status: "assigned",
  }));
  state.rows.shortages = (result.shortages || []).map((shortage) => ({
    ...shortage,
    shift_name: shiftNames[shortage.shift] || "",
    status: "unfilled",
    value: shortage.shortage_count,
    message:
      shortage.shortage_count > 0
        ? `Unfilled demand for ${shortage.shortage_count} ${shortage.role} slot(s)`
        : "",
  }));
  state.rows.metrics = Object.entries(result.metrics || {}).map(([key, value]) => ({
    status: key,
    value,
    message: "Solver metric",
  }));
  state.rows.issues = (result.violations || []).map((violation) => ({
    type: "validation",
    status: "violation",
    message: String(violation),
    request_id: "",
  }));
  const objective = result.objective_breakdown || {};
  if ("total_shortage" in objective) {
    state.rows.metrics.push({
      status: "total_shortage",
      value: objective.total_shortage,
      message: "Business metric",
    });
  }
  if ("labor_cost_value" in objective) {
    state.rows.metrics.push({
      status: "labor_cost_value",
      value: objective.labor_cost_value,
      message: "Business metric",
    });
  }
  renderSummary({
    status: result.metrics?.status,
    assignmentCount: state.rows.assignments.length,
    totalShortage: objective.total_shortage,
    laborCost: objective.labor_cost_value,
  });
  if (state.rows.issues.length > 0) {
    activateTab("issues");
    return;
  }
  renderTable();
}

function setIssue(error, status = "error") {
  state.rows.issues = [
    {
      type: error.type || error.name || "Error",
      status,
      message: error.message,
      request_id: error.request_id || "",
    },
  ];
  activateTab("issues");
}

function invalidJsonError(error) {
  const invalidJson = new Error(`Invalid JSON: ${error.message}`);
  invalidJson.type = "InvalidJson";
  return invalidJson;
}

function currentSolveRequest({
  applySelectedResponseMode = false,
  fallbackToSample = false,
} = {}) {
  try {
    const request = JSON.parse(elements.jsonRequest.value);
    if (applySelectedResponseMode) {
      request.options = request.options || {};
      request.options.response_mode = elements.responseMode.value;
    }
    return request;
  } catch (error) {
    if (fallbackToSample) {
      return JSON.parse(JSON.stringify(sampleRequest));
    }
    throw error;
  }
}

function syncResponseModeFromRequest() {
  const request = currentSolveRequest();
  const mode = request.options?.response_mode;
  if (["compact", "standard", "debug"].includes(mode)) {
    elements.responseMode.value = mode;
  }
}

function writeRequest(request) {
  elements.jsonRequest.value = JSON.stringify(request, null, 2);
  syncResponseModeFromRequest();
}

function setRowsFromCsv(csvText) {
  const records = parseCsv(csvText);
  state.csvText = csvText;
  state.rows.assignments = records.filter((row) => row.record_type === "assignment");
  state.rows.shortages = records.filter((row) => row.record_type === "shortage");
  state.rows.metrics = records.filter((row) => row.record_type === "metric");
  state.rows.issues = records
    .filter((row) => ["validation", "error"].includes(row.record_type))
    .map((row) => ({
      type: row.record_type,
      status: row.status,
      message: row.message,
      request_id: "",
    }));
  const metricValue = (name) =>
    state.rows.metrics.find((row) => row.status === name)?.value ?? "";
  renderSummary({
    status: metricValue("status"),
    assignmentCount: state.rows.assignments.length,
    totalShortage: metricValue("total_shortage"),
    laborCost: metricValue("labor_cost_value"),
  });
  elements.downloadCsv.disabled = false;
  if (state.rows.issues.length > 0) {
    activateTab("issues");
    return;
  }
  renderTable();
}

function parseCsv(text) {
  const [headers = [], ...body] = parseCsvRecords(text).filter((row) =>
    row.some((cell) => cell !== ""),
  );
  return body.map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])),
  );
}

function parseCsvRecords(text) {
  const rows = [];
  let current = "";
  let record = [];
  let inQuotes = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && inQuotes && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      record.push(current);
      current = "";
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && next === "\n") index += 1;
      record.push(current);
      rows.push(record);
      record = [];
      current = "";
    } else {
      current += char;
    }
  }
  if (current || record.length) {
    record.push(current);
    rows.push(record);
  }
  return rows;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function checkApi() {
  await withBusy("Checking API...", async () => {
    try {
      const response = await apiFetch("/metadata");
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error?.message || "API check failed");
      setServiceStatus(true, "API ready");
      elements.metadataGrid.innerHTML = [
        metricCard("Schema", payload.schema_version),
        metricCard("Max JSON bytes", payload.request_limits?.max_json_request_bytes),
        metricCard("Max CSV bytes", payload.request_limits?.max_csv_upload_bytes),
        metricCard("Workers", payload.job_execution?.max_workers),
      ].join("");
      state.rows.issues = [];
      renderTable();
      log("Metadata loaded", payload.endpoints);
    } catch (error) {
      setServiceStatus(false, "API unavailable");
      setIssue(error);
      logError("API check failed", error);
    }
  });
}

async function solveJson() {
  await withBusy("Solving JSON...", async () => {
    try {
      const payload = currentSolveRequest({ applySelectedResponseMode: true });
      writeRequest(payload);
      const response = await apiFetch("/solve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const envelope = await response.json();
      if (!response.ok || !envelope.ok) {
        throw responseError(response, envelope);
      }
      state.csvText = "";
      setRowsFromSolveResult(envelope.result);
      elements.serviceStatus.textContent = "JSON solve complete";
      log("JSON solve completed", {
        response_mode: payload.options.response_mode,
        metrics: envelope.result.metrics,
      });
    } catch (error) {
      setIssue(error);
      logError("JSON solve failed", error);
    }
  });
}

async function solveCsv() {
  await withBusy("Uploading CSV...", async () => {
    const csvFiles = selectedCsvFiles();
    if (!csvFiles.employees || !csvFiles.shifts || !csvFiles.demand) {
      const error = new Error("CSV solve requires uploaded files or loaded demo CSVs.");
      setIssue(error, "missing_input");
      logError("CSV solve failed", error);
      return;
    }
    const formData = new FormData();
    formData.append("employees_csv", csvFiles.employees);
    formData.append("shifts_csv", csvFiles.shifts);
    formData.append("demand_csv", csvFiles.demand);
    formData.append("min_rest_hours", document.querySelector("#min-rest-hours").value);
    formData.append("max_consecutive_days", document.querySelector("#max-consecutive-days").value);
    formData.append("shortage_penalty", document.querySelector("#shortage-penalty").value);
    formData.append("time_limit_sec", document.querySelector("#time-limit").value);
    formData.append("seed", document.querySelector("#seed").value);
    formData.append("use_warm_start", document.querySelector("#use-warm-start").checked);

    try {
      const response = await apiFetch("/solve-csv", { method: "POST", body: formData });
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok) {
        if (contentType.includes("application/json")) {
          const envelope = await response.json();
          throw responseError(response, envelope);
        }
        throw new Error("CSV solve failed");
      }
      const csvText = await response.text();
      setRowsFromCsv(csvText);
      elements.serviceStatus.textContent = "CSV solve complete";
      log("CSV solve completed.");
    } catch (error) {
      setIssue(error);
      logError("CSV solve failed", error);
    }
  });
}

function selectedCsvFiles() {
  return {
    employees:
      document.querySelector("#employees-csv").files[0] || state.demoCsvFiles.employees,
    shifts:
      document.querySelector("#shifts-csv").files[0] || state.demoCsvFiles.shifts,
    demand:
      document.querySelector("#demand-csv").files[0] || state.demoCsvFiles.demand,
  };
}

async function loadDemoCsvs() {
  await withBusy("Loading demo CSVs...", async () => {
    try {
      state.demoCsvFiles = {
        employees: await fetchDemoCsv("employees.csv"),
        shifts: await fetchDemoCsv("shifts.csv"),
        demand: await fetchDemoCsv("demand.csv"),
      };
      elements.csvDemoStatus.textContent =
        "Demo CSVs loaded from /viewer/examples. Uploaded files still take precedence.";
      state.rows.issues = [];
      elements.serviceStatus.textContent = "Demo CSVs loaded";
      renderTable();
      log("Demo CSV files loaded.");
    } catch (error) {
      state.demoCsvFiles = {};
      elements.csvDemoStatus.textContent = "Demo CSV load failed.";
      setIssue(error);
      logError("Demo CSV load failed", error);
    }
  });
}

async function fetchDemoCsv(filename) {
  const response = await apiFetch(`/viewer/examples/${filename}`);
  if (!response.ok) {
    throw new Error(`${filename} returned ${response.status}`);
  }
  const text = await response.text();
  return new File([text], filename, { type: "text/csv" });
}

function loadMappingSample() {
  const sample = mappingSamples[elements.mappingCsvType.value] || mappingSamples.employees;
  elements.mappingHeaders.value = sample.headers;
  elements.mappingRows.value = sample.rows;
  elements.mappingJson.value = JSON.stringify(sample.mapping, null, 2);
  renderMappingResult("Sample loaded", {
    csv_type: elements.mappingCsvType.value,
    headers: mappingHeadersFromInput(),
    rows: mappingRowsFromInput(),
  });
  log("CSV mapping sample loaded", { csv_type: elements.mappingCsvType.value });
}

function clearMappingWizard() {
  elements.mappingHeaders.value = "";
  elements.mappingRows.value = "";
  elements.mappingJson.value = "";
  elements.mappingSummary.innerHTML = "";
  elements.mappingOutput.textContent = "";
  setCanonicalCsvText("");
  setExportSafetyFlags({});
  log("CSV mapping wizard cleared.");
}

function setCanonicalCsvText(csvText) {
  state.canonicalCsvText = csvText || "";
  elements.exportOutput.textContent = state.canonicalCsvText || exportPreviewEmptyState;
  updateCanonicalCsvActions();
}

function setExportSafetyFlags(result = {}) {
  const willWriteFiles = result.will_write_files ?? false;
  const willMutateFiles = result.will_mutate_files ?? false;
  const willSolve = result.will_solve ?? false;
  const usesExternalLlm = result.uses_external_llm ?? false;
  const rowSemanticsValidated = result.row_semantics_validated ?? false;
  elements.exportSafetyFlags.textContent = [
    `Will write files: ${willWriteFiles}`,
    `Will mutate files: ${willMutateFiles}`,
    `Will solve: ${willSolve}`,
    `Uses external LLM: ${usesExternalLlm}`,
    `Row semantics validated: ${rowSemanticsValidated}`,
  ].join(" | ");
}

function validateMappingHeaders(headers) {
  const emptyHeaderIndex = headers.findIndex((header) => header.trim() === "");
  if (emptyHeaderIndex !== -1) {
    throw new Error(
      `CSV mapping wizard header ${emptyHeaderIndex + 1} is empty. Name every header cell before previewing.`,
    );
  }
  return headers;
}

function mappingHeadersFromInput() {
  const records = parseCsvRecords(elements.mappingHeaders.value).filter((row) =>
    row.some((cell) => cell.trim() !== ""),
  );
  const headers = records[0] || [];
  if (!headers.length) {
    throw new Error("CSV mapping wizard requires at least one header.");
  }
  return validateMappingHeaders(headers);
}

function mappingRowsFromInput() {
  return parseCsvRecords(elements.mappingRows.value).filter((row) =>
    row.some((cell) => cell.trim() !== ""),
  );
}

function validateMappingRows(rows, headerCount) {
  const mismatchedRowIndex = rows.findIndex((row) => row.length !== headerCount);
  if (mismatchedRowIndex !== -1) {
    const row = rows[mismatchedRowIndex];
    throw new Error(
      `CSV mapping wizard row ${mismatchedRowIndex + 1} has ${row.length} cell(s); expected ${headerCount}. Fix row length before previewing rows or export.`,
    );
  }
  return rows;
}

function optionalMappingFromInput() {
  const value = elements.mappingJson.value.trim();
  if (!value) return undefined;
  try {
    return JSON.parse(value);
  } catch (error) {
    throw invalidJsonError(error);
  }
}

function mappingBasePayload() {
  const payload = {
    csv_type: elements.mappingCsvType.value,
    headers: mappingHeadersFromInput(),
  };
  const mapping = optionalMappingFromInput();
  if (mapping !== undefined) {
    payload.mapping = mapping;
  }
  return payload;
}

function mappingRowPayload() {
  const payload = mappingBasePayload();
  const rows = mappingRowsFromInput();
  if (!rows.length) {
    throw new Error("CSV mapping wizard requires at least one sample row.");
  }
  payload.rows = validateMappingRows(rows, payload.headers.length);
  return payload;
}

function renderMappingResult(label, payload) {
  const result = payload?.result || payload || {};
  const status = result.status || "ready";
  const canApply = result.apply_plan?.can_apply ?? result.can_apply ?? "";
  const canTransform = result.can_transform_rows ?? "";
  const canExport = result.can_export ?? "";
  const reason =
    result.export_ready_reason ||
    result.apply_plan?.reason ||
    result.reason ||
    "";
  elements.mappingSummary.innerHTML = [
    metricCard("Status", status),
    metricCard("Can apply", canApply),
    metricCard("Can transform", canTransform),
    metricCard("Can export", canExport),
    metricCard("Reason", reason),
  ].join("");
  elements.mappingOutput.textContent = JSON.stringify(payload, null, 2);
  setCanonicalCsvText(result.csv_text || "");
  setExportSafetyFlags(result);
  log(label, {
    type: result.type || "",
    status,
    reason,
    will_solve: result.will_solve ?? false,
    will_write_files: result.will_write_files ?? false,
  });
}

async function copyCanonicalCsv() {
  if (!state.canonicalCsvText) {
    const error = missingCanonicalCsvPreviewError("copy");
    setIssue(error, "missing_input");
    logError("Canonical CSV copy unavailable", error);
    return;
  }
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    const error = clipboardUnavailableError();
    setIssue(error, "unsupported");
    logError("Canonical CSV clipboard unavailable", error);
    return;
  }
  try {
    await navigator.clipboard.writeText(state.canonicalCsvText);
    log("Canonical CSV copied.");
  } catch (error) {
    logError("Canonical CSV copy failed", error);
  }
}

function downloadCanonicalCsv() {
  if (!state.canonicalCsvText) {
    const error = missingCanonicalCsvPreviewError("download");
    setIssue(error, "missing_input");
    logError("Canonical CSV download unavailable", error);
    return;
  }
  const blob = new Blob([state.canonicalCsvText], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = canonicalCsvDownloadFilename();
  anchor.click();
  URL.revokeObjectURL(url);
  log("Canonical CSV downloaded.", { csv_type: elements.mappingCsvType.value });
}

function canonicalCsvDownloadFilename() {
  const safeCsvType =
    elements.mappingCsvType.value.toLowerCase().replace(/[^a-z0-9-]+/g, "-") ||
    "csv";
  return `canonical-${safeCsvType}-preview.csv`;
}

function missingCanonicalCsvPreviewError(action) {
  const error = new Error(missingCanonicalCsvPreviewMessages[action]);
  error.type = "MissingCanonicalCsvPreview";
  return error;
}

function clipboardUnavailableError() {
  const error = new Error(clipboardUnavailableMessage);
  error.type = "ClipboardUnavailable";
  return error;
}

async function postJson(path, payload) {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const envelope = await response.json();
  if (!response.ok || !envelope.ok) {
    throw responseError(response, envelope);
  }
  return envelope;
}

async function suggestCsvMapping() {
  await withBusy("Suggesting mapping...", async () => {
    try {
      const payload = {
        csv_type: elements.mappingCsvType.value,
        headers: mappingHeadersFromInput(),
      };
      const envelope = await postJson("/csv/mapping/suggest", payload);
      renderMappingResult("CSV mapping suggestion loaded", envelope);
    } catch (error) {
      setIssue(error);
      logError("CSV mapping suggestion failed", error);
    }
  });
}

async function previewApplyPlan() {
  await withBusy("Previewing apply plan...", async () => {
    try {
      const envelope = await postJson("/csv/mapping/preview", mappingBasePayload());
      renderMappingResult("CSV apply plan preview loaded", envelope);
    } catch (error) {
      setIssue(error);
      logError("CSV apply plan preview failed", error);
    }
  });
}

async function previewRowTransform() {
  await withBusy("Previewing rows...", async () => {
    try {
      const envelope = await postJson("/csv/mapping/rows/preview", mappingRowPayload());
      renderMappingResult("CSV row preview loaded", envelope);
    } catch (error) {
      setIssue(error);
      logError("CSV row preview failed", error);
    }
  });
}

async function previewCanonicalExport() {
  await withBusy("Previewing export...", async () => {
    try {
      const envelope = await postJson(
        "/csv/mapping/export/preview",
        mappingRowPayload(),
      );
      renderMappingResult("CSV export preview loaded", envelope);
    } catch (error) {
      setIssue(error);
      logError("CSV export preview failed", error);
    }
  });
}

async function submitJob() {
  await withBusy("Submitting job...", async () => {
    try {
      const payload = currentSolveRequest({ applySelectedResponseMode: true });
      writeRequest(payload);
      const submitResponse = await apiFetch("/solve-jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const submitEnvelope = await submitResponse.json();
      if (!submitResponse.ok || !submitEnvelope.ok) {
        throw responseError(submitResponse, submitEnvelope);
      }
      log("Job submitted", {
        response_mode: payload.options.response_mode,
        job: submitEnvelope.job,
      });
      elements.serviceStatus.textContent = "Polling job...";
      await pollJob(submitEnvelope.status_url);
    } catch (error) {
      setIssue(error);
      logError("Job failed", error);
    }
  });
}

async function pollJob(statusUrl) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    const response = await apiFetch(statusUrl);
    const envelope = await response.json();
    const job = envelope.job;
    if (job.status === "succeeded") {
      setRowsFromSolveResult(job.result);
      elements.serviceStatus.textContent = "Job succeeded";
      log("Job succeeded", { job_id: job.job_id, duration_sec: job.duration_sec });
      return;
    }
    if (job.status === "failed") {
      throw new Error(job.error?.message || "Job failed");
    }
  }
  log("Job is still running. Use the status URL from the API response to continue polling.");
}

function responseError(response, envelope) {
  const apiError = envelope?.error || {};
  const error = new Error(apiError.message || `Request failed with ${response.status}`);
  error.type = apiError.type || "HttpError";
  error.status = response.status;
  error.request_id = apiError.request_id || response.headers.get("x-request-id") || "";
  return error;
}

function downloadCsv() {
  if (!state.csvText) return;
  const blob = new Blob([state.csvText], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "roster.csv";
  anchor.click();
  URL.revokeObjectURL(url);
}

function activateTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const isActive = tab.dataset.tab === tabName;
    tab.classList.toggle("active", isActive);
    if (isActive) {
      state.activeTab = tabName;
    }
  });
  renderTable();
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});

elements.apiBase.value = defaultApiBase();
writeRequest(sampleRequest);
elements.checkApi.addEventListener("click", checkApi);
elements.loadSampleJson.addEventListener("click", () => {
  writeRequest(sampleRequest);
  log("Sample JSON request loaded.");
});
elements.responseMode.addEventListener("change", () => {
  try {
    const request = currentSolveRequest({ applySelectedResponseMode: true });
    writeRequest(request);
    log("Response mode updated", { response_mode: elements.responseMode.value });
  } catch (error) {
    const issue = invalidJsonError(error);
    setIssue(issue);
    logError("Response mode update failed", issue);
  }
});
elements.loadDemoCsvs.addEventListener("click", loadDemoCsvs);
elements.loadMappingSample.addEventListener("click", loadMappingSample);
elements.mappingCsvType.addEventListener("change", loadMappingSample);
elements.suggestMapping.addEventListener("click", suggestCsvMapping);
elements.clearMappingWizard.addEventListener("click", clearMappingWizard);
elements.previewApplyPlan.addEventListener("click", previewApplyPlan);
elements.previewRowTransform.addEventListener("click", previewRowTransform);
elements.previewExport.addEventListener("click", previewCanonicalExport);
elements.copyCanonicalCsv.addEventListener("click", copyCanonicalCsv);
elements.downloadCanonicalCsv.addEventListener("click", downloadCanonicalCsv);
elements.solveJson.addEventListener("click", solveJson);
elements.solveCsv.addEventListener("click", solveCsv);
elements.submitJob.addEventListener("click", submitJob);
elements.downloadCsv.addEventListener("click", downloadCsv);
loadMappingSample();
renderSummary();
renderTable();
checkApi();
