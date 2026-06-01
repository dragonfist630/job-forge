"""
scraper.py -- orchestrates scraper_worker.py as a subprocess.

API used by Flask routes:
  scraper.start_scan(settings)   → launches worker, saves to data/jobs.json
  scraper.stream_scan(settings)  → generator of SSE-formatted strings
  scraper.get_jobs()             → list[dict] from data/jobs.json
  scraper.save_approvals(job_ids) → persist user-approved job ids
  scraper.get_approvals()        → set[str] of approved job_ids
"""

import json
import subprocess
import sys
import os
from datetime import datetime
from pathlib import Path
from threading import Thread
from queue import Queue, Empty

import scorer

DATA_DIR = Path(__file__).parent / "data"
JOBS_FILE = DATA_DIR / "jobs.json"
APPROVALS_FILE = DATA_DIR / "approved_jobs.json"
WORKER_SCRIPT = Path(__file__).parent / "scraper_worker.py"

_active_proc: subprocess.Popen | None = None


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_jobs() -> list[dict]:
    _ensure_data_dir()
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_jobs(jobs: list[dict]):
    _ensure_data_dir()
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_job(job_id: str) -> bool:
    jobs = get_jobs()
    new_jobs = [j for j in jobs if j["job_id"] != job_id]
    if len(new_jobs) == len(jobs):
        return False
    save_jobs(new_jobs)
    # Also remove from approvals if present
    approved = get_approvals()
    approved.discard(job_id)
    save_approvals(list(approved))
    return True


def get_approvals() -> set:
    _ensure_data_dir()
    if not APPROVALS_FILE.exists():
        return set()
    try:
        data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
        return set(data.get("approved", []))
    except Exception:
        return set()


def save_approvals(job_ids: list[str]):
    _ensure_data_dir()
    APPROVALS_FILE.write_text(
        json.dumps({"approved": list(job_ids)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def stream_scan(settings: dict):
    """
    Generator — yields SSE-formatted strings.
    Launches scraper_worker as subprocess, streams its JSON-line output,
    appends new jobs to data/jobs.json (accumulates across scans, dedupes by job_id).
    Each job is tagged with a scan_session ISO timestamp for grouping in the UI.
    """
    global _active_proc

    _ensure_data_dir()
    scan_session = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    settings_json = json.dumps(settings)
    cmd = [sys.executable, str(WORKER_SCRIPT), settings_json]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        _active_proc = proc
    except Exception as e:
        yield _sse({"type": "error", "msg": f"Failed to start scraper: {e}"})
        return

    # Load existing jobs once; accumulate new ones without overwriting prior scans
    all_jobs = get_jobs()
    existing_ids = {j["job_id"] for j in all_jobs}

    stderr_lines: list[str] = []

    def read_stderr():
        for line in proc.stderr:
            stderr_lines.append(line.rstrip())

    Thread(target=read_stderr, daemon=True).start()

    try:
        for raw_line in proc.stdout:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            if obj.get("type") == "job":
                job_id = obj.get("job_id")
                if job_id and job_id not in existing_ids:
                    obj["scan_session"] = scan_session
                    obj["score"] = scorer.score_job(obj, settings)
                    all_jobs.append(obj)
                    save_jobs(all_jobs)
                    existing_ids.add(job_id)
                    yield _sse(obj)
                # duplicate job_id — skip silently
            else:
                yield _sse(obj)

    finally:
        proc.wait()
        _active_proc = None

        if proc.returncode != 0 and stderr_lines:
            err_msg = "\n".join(stderr_lines[-10:])
            yield _sse({"type": "error", "msg": f"Worker exited {proc.returncode}: {err_msg}"})


def stop_scan():
    global _active_proc
    if _active_proc and _active_proc.poll() is None:
        _active_proc.terminate()
        _active_proc = None
        return True
    return False


def is_scanning() -> bool:
    return _active_proc is not None and _active_proc.poll() is None


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"
