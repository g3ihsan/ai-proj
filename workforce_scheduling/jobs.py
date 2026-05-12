from __future__ import annotations

from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Mapping
from uuid import uuid4

from .schemas import solve_payload


JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
SOLVE_JOB_MAX_WORKERS = 2
solve_job_executor = ThreadPoolExecutor(
    max_workers=SOLVE_JOB_MAX_WORKERS,
    thread_name_prefix="solve-job",
)


class JobNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class SolveJob:
    job_id: str
    status: str
    created_at: str
    updated_at: str
    result: Dict[str, Any] | None = None
    error: Dict[str, Any] | None = None


class InMemorySolveJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, SolveJob] = {}
        self._lock = Lock()

    def create(self) -> SolveJob:
        now = _utc_now()
        job = SolveJob(
            job_id=uuid4().hex,
            status=JOB_QUEUED,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> SolveJob:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(f"Unknown solve job {job_id}")
        return job

    def mark_running(self, job_id: str) -> SolveJob:
        return self._replace(job_id, status=JOB_RUNNING)

    def mark_succeeded(self, job_id: str, result: Mapping[str, Any]) -> SolveJob:
        return self._replace(
            job_id,
            status=JOB_SUCCEEDED,
            result=deepcopy(dict(result)),
            error=None,
        )

    def mark_failed(self, job_id: str, error: Mapping[str, Any]) -> SolveJob:
        return self._replace(
            job_id,
            status=JOB_FAILED,
            result=None,
            error=deepcopy(dict(error)),
        )

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    def _replace(self, job_id: str, **changes: Any) -> SolveJob:
        with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise JobNotFoundError(f"Unknown solve job {job_id}")
            updated = SolveJob(
                job_id=current.job_id,
                status=changes.get("status", current.status),
                created_at=current.created_at,
                updated_at=_utc_now(),
                result=changes.get("result", current.result),
                error=changes.get("error", current.error),
            )
            self._jobs[job_id] = updated
        return updated


def run_solve_job(
    store: InMemorySolveJobStore,
    job_id: str,
    request_payload: Mapping[str, Any],
) -> None:
    try:
        store.mark_running(job_id)
        response_payload = solve_payload(request_payload)
        if response_payload["ok"]:
            store.mark_succeeded(job_id, response_payload["result"])
        else:
            store.mark_failed(job_id, response_payload["error"])
    except Exception as exc:
        store.mark_failed(
            job_id,
            {
                "type": exc.__class__.__name__,
                "message": str(exc),
            },
        )


def submit_solve_job(
    store: InMemorySolveJobStore,
    job_id: str,
    request_payload: Mapping[str, Any],
) -> Future[None]:
    return solve_job_executor.submit(
        run_solve_job,
        store,
        job_id,
        request_payload,
    )


def job_payload(job: SolveJob) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
    if job.result is not None:
        payload["result"] = deepcopy(job.result)
    if job.error is not None:
        payload["error"] = deepcopy(job.error)
    return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
