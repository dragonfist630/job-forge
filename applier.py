"""
applier.py -- LinkedIn Easy Apply automation bridge (Phase 5).

Wraps the LinkedIn applier's upload_resume() and apply flow.
Currently a stub — Phase 5 implements the full apply loop.
"""

import json
import sys
import os
from pathlib import Path

import config_manager
import scraper
import resume_generator


def get_apply_queue() -> list[dict]:
    """Return approved jobs that have a generated PDF resume."""
    all_jobs = scraper.get_jobs()
    approved = scraper.get_approvals()
    resumes = resume_generator.get_resumes()

    queue = []
    for job in all_jobs:
        job_id = job["job_id"]
        if job_id in approved and job_id in resumes:
            pdf = resumes[job_id]
            if Path(pdf).exists():
                queue.append({**job, "pdf_path": pdf})
    return queue


def stream_apply(settings: dict):
    """
    Generator — yields SSE strings.
    Phase 5 placeholder: lists what would be applied.
    """

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    queue = get_apply_queue()
    if not queue:
        yield sse({"type": "error", "msg": "No jobs ready to apply. Generate resumes first."})
        return

    yield sse({"type": "start", "total": len(queue), "msg": "Phase 5 coming soon — auto-apply not yet implemented."})

    for job in queue:
        yield sse({
            "type": "info",
            "job_id": job["job_id"],
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "pdf_path": job["pdf_path"],
            "msg": f"Would apply to: {job.get('title')} at {job.get('company')}",
        })

    yield sse({"type": "complete", "total": len(queue), "msg": "Auto-apply coming in Phase 5."})
