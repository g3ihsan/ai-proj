# Roster Viewer

Static frontend for the existing solver API.

Run the API:

```bash
PYTHONPATH=. uvicorn workforce_scheduling.api:app --reload
```

Open:

```txt
http://localhost:8000/viewer/
```

The viewer calls the existing JSON, job, CSV solve, and deterministic CSV
mapping preview endpoints. It has no build step and does not change solver
behavior.

The CSV Mapping Wizard is a preview-only workflow. It can request mapping
suggestions, apply-plan previews, sample row transformations, and canonical CSV
export text from the backend. It does not write files, does not call
`/solve-csv`, and does not submit mapped CSVs to the solver. Empty header cells
and sample rows that do not match the header length are rejected in the viewer
before a mapping preview request is sent. The canonical CSV copy action copies
only the in-memory export preview text from the browser. The canonical CSV
download action also runs in the browser and uses the same in-memory preview
text with deterministic `canonical-{csv_type}-preview.csv` filenames; it does
not write files on the backend. Export safety flags show whether the preview
would write files, mutate files, solve, use an external LLM, or validate row
semantics. Copy or download attempts before an export preview exists are shown
as local Issues and do not call the backend.
