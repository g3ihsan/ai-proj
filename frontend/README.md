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
`/solve-csv`, and does not submit mapped CSVs to the solver.
