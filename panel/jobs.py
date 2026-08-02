"""Async job queue for provisioning operations.

A ThreadPoolExecutor(max_workers=4) runs site_create / site_delete in the
background. Each job is persisted in SQLite before submission so the UI can
poll/stream its progress. Identical queued/running (type, domain) jobs are
de-duplicated.
"""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import models
import runner_client

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="hz-job")
_dedup_lock = threading.Lock()


class JobError(Exception):
    pass


def _run_job(job_id, job_type, domain, params):
    """Worker body: flip to running, call the runner, persist outcome."""
    models.update_job(job_id, status="running")
    try:
        if job_type == "site_create":
            result, log = runner_client.site_create(
                domain,
                params.get("type", "static"),
                ssl=bool(params.get("ssl", False)),
            )
        elif job_type == "site_delete":
            result, log = runner_client.site_delete(
                domain, purge_db=bool(params.get("purge_db", True))
            )
        else:
            raise runner_client.RunnerError(
                "unsupported job type: {}".format(job_type)
            )

        status = "ok" if result.get("status") == "ok" else "error"
        models.update_job(
            job_id,
            status=status,
            result_json=json.dumps(result),
            log=log,
            finished_at=_now(),
        )
    except runner_client.RunnerError as exc:
        models.update_job(
            job_id,
            status="error",
            result_json=json.dumps({"status": "error", "message": str(exc)}),
            log=str(exc),
            finished_at=_now(),
        )
    except Exception as exc:  # defensive: never let a worker die silently
        models.update_job(
            job_id,
            status="error",
            result_json=json.dumps(
                {"status": "error", "message": "internal error: {}".format(exc)}
            ),
            log="internal error: {}".format(exc),
            finished_at=_now(),
        )


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def submit_job(job_type, domain, params=None):
    """Persist and enqueue a job. Returns (job_id, deduped_bool).

    If an identical (type, domain) job is already queued/running, returns its
    id with deduped=True and does not enqueue a new one.
    """
    params = params or {}
    if job_type not in ("site_create", "site_delete"):
        raise JobError("unknown job type: {}".format(job_type))
    if not runner_client.validate_domain(domain):
        raise JobError("invalid domain: {}".format(domain))

    with _dedup_lock:
        existing = models.find_active_job(job_type, domain)
        if existing:
            return existing["id"], True

        job_id = models.create_job(
            job_type, domain, params_json=json.dumps(params), status="queued"
        )

    _executor.submit(_run_job, job_id, job_type, domain, params)
    return job_id, False


def get_job(job_id):
    return models.get_job(job_id)


def list_jobs(limit=100):
    return models.list_jobs(limit=limit)


def shutdown(wait=True):
    _executor.shutdown(wait=wait)
