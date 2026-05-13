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

const state = {
  activeTab: "assignments",
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
  ].forEach((button) => {
    button.disabled = isBusy;
  });
  elements.downloadCsv.disabled = isBusy || !state.csvText;
  elements.serviceDot.classList.toggle("busy", isBusy);
  if (statusText) {
    elements.serviceStatus.textContent = statusText;
  }
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
  const [headers = [], ...body] = rows.filter((row) => row.some((cell) => cell !== ""));
  return body.map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""])),
  );
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
  const request = currentSolveRequest({ applySelectedResponseMode: true });
  writeRequest(request);
  log("Response mode updated", { response_mode: elements.responseMode.value });
});
elements.loadDemoCsvs.addEventListener("click", loadDemoCsvs);
elements.solveJson.addEventListener("click", solveJson);
elements.solveCsv.addEventListener("click", solveCsv);
elements.submitJob.addEventListener("click", submitJob);
elements.downloadCsv.addEventListener("click", downloadCsv);
renderSummary();
renderTable();
checkApi();
