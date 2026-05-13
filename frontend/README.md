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

The viewer calls the existing JSON, job, and CSV endpoints. It has no build step
and does not change solver behavior.
