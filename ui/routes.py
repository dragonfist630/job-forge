"""
Flask blueprints — setup wizard + dashboard + scan/jobs API.
"""

from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, Response, stream_with_context, send_file

import config_manager
import scraper
import resume_generator
import applier

setup_bp = Blueprint("setup", __name__, url_prefix="/setup")
dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


# ─────────────────────────────────────────────
# SETUP WIZARD
# ─────────────────────────────────────────────

def _ensure_settings_defaults(s: dict) -> dict:
    """Guarantee every nested section exists so Jinja2 can access keys safely."""
    for key in ('identity', 'location', 'linkedin', 'online_presence',
                'professional_summary', 'experience', 'compensation',
                'notice_period', 'work_authorization', 'equal_opportunity',
                'screening', 'paths', 'app', 'profile', 'api_keys'):
        s.setdefault(key, {})
    s['job_search'] = s.get('job_search') or {}
    s['job_search'].setdefault('filters', {})
    s['job_search'].setdefault('search_terms', [])
    return s


@setup_bp.route("/", methods=["GET"])
def wizard():
    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)
    settings = _ensure_settings_defaults(settings)
    health = config_manager.check_health(settings)

    # Read back CV and profile for pre-filling wizard on revisit
    cv_text = config_manager.INTERNAL_CV_PATH.read_text(encoding="utf-8") \
        if config_manager.INTERNAL_CV_PATH.exists() else ""
    profile_data = settings.get("profile", {})

    return render_template(
        "setup.html",
        settings=settings,
        health=health,
        cv_text=cv_text,
        target_roles=profile_data.get("target_roles", ""),
        superpower=profile_data.get("superpower", ""),
        work_preference=profile_data.get("work_preference", []),
    )


def _build_profile_md(target_roles: str, superpower: str, work_preference: list) -> str:
    roles_lines = "\n".join(
        f"- {r.strip()}" for r in target_roles.strip().splitlines() if r.strip()
    ) or "- (not specified)"
    pref_str = ", ".join(work_preference) if work_preference else "No preference specified"
    return f"""# User Profile — JobForge

## Target Roles
{roles_lines}

## Superpower / Unique Angle
{superpower.strip() or "(not specified)"}

## Work Preference
{pref_str}

## Bullet Formula
[Strong action verb] + [specific metric or outcome] + [method/technology used] + [business context]
Example: "Reduced API latency 40% by implementing Redis caching, enabling 10K concurrent users"

## Keyword Strategy
- Extract ALL hard and soft skill keywords from the job description
- Inject top 3 keywords into the Summary section
- Inject 1-2 keywords into the first bullet of each role
- Mirror exact phrasing from the JD wherever possible

## Adaptive Framing
Read cv.md carefully. Identify which projects and achievements are most relevant to the target role.
Adapt framing and vocabulary to match the role's priorities (e.g. scale for platform roles, speed for startups).
Lead with the superpower above — it is the candidate's differentiator.

## Location / Remote Policy
Preferred work style: {pref_str}
Score hybrid/remote opportunities highly when they match the candidate's preference.
"""


@setup_bp.route("/save", methods=["POST"])
def save():
    """Receives the wizard form, saves to settings.yaml."""
    from pathlib import Path
    form = request.form

    # Write cv.md
    cv_text = form.get("cv_text", "").strip()
    if cv_text:
        config_manager.INTERNAL_CV_PATH.write_text(cv_text, encoding="utf-8")
        # Trigger async CV structuring (Plan 3) — non-blocking
        import threading
        import cv_extractor
        threading.Thread(target=cv_extractor.extract, kwargs={"force": True}, daemon=True).start()

    # Generate and write _profile.md
    target_roles = form.get("target_roles", "")
    superpower = form.get("superpower", "")
    work_preference = form.getlist("work_preference")
    if target_roles or superpower:
        profile_md = _build_profile_md(target_roles, superpower, work_preference)
        (config_manager._ROOT / "modes" / "_profile.md").write_text(profile_md, encoding="utf-8")

    # Build the nested settings dict from flat form fields
    data = {
        "profile": {
            "target_roles": target_roles,
            "superpower": superpower,
            "work_preference": work_preference,
        },
        "linkedin": {
            "email": form.get("linkedin_email", ""),
            "password": form.get("linkedin_password", ""),
        },
        "identity": {
            "first_name": form.get("first_name", ""),
            "middle_name": form.get("middle_name", ""),
            "last_name": form.get("last_name", ""),
            "phone_number": form.get("phone_number", ""),
            "email": form.get("contact_email", ""),
        },
        "location": {
            "country": form.get("country", ""),
            "city": form.get("city", ""),
            "street": form.get("street", ""),
            "state": form.get("state", ""),
            "zipcode": form.get("zipcode", ""),
        },
        "work_authorization": {
            "require_visa_sponsorship": form.get("require_visa_sponsorship", "No"),
            "us_citizenship": form.get("us_citizenship", ""),
        },
        "equal_opportunity": {
            "ethnicity": form.get("ethnicity", "Decline"),
            "gender": form.get("gender", "Decline"),
            "disability_status": form.get("disability_status", "Decline"),
            "veteran_status": form.get("veteran_status", "Decline"),
        },
        "experience": {
            "years_of_experience": form.get("years_of_experience", "0"),
            "current_experience": int(form.get("current_experience", 0) or 0),
            "did_masters": form.get("did_masters") == "true",
            "recent_employer": form.get("recent_employer", ""),
            "confidence_level": form.get("confidence_level", "7"),
        },
        "compensation": {
            "desired_salary": int(form.get("desired_salary", 0) or 0),
            "current_ctc": int(form.get("current_ctc", 0) or 0),
            "currency": form.get("currency", "GBP"),
            "target_range": form.get("target_range", ""),
            "minimum": form.get("minimum_salary", ""),
        },
        "notice_period": {
            "days": int(form.get("notice_period_days", 30) or 30),
        },
        "online_presence": {
            "linkedin": form.get("linkedin_profile_url", ""),
            "github": form.get("github_url", ""),
            "portfolio": form.get("portfolio_url", ""),
        },
        "professional_summary": {
            "headline": form.get("headline", ""),
            "summary": form.get("summary", ""),
        },
        "job_search": {
            "search_terms": [
                t.strip()
                for t in form.get("search_terms", "").split("\n")
                if t.strip()
            ],
            "search_location": form.get("search_location", ""),
            "switch_number": int(form.get("switch_number", 30) or 30),
            "filters": {
                "sort_by": form.get("sort_by", "Most relevant"),
                "date_posted": form.get("date_posted") or "Past 24 hours",
                "salary": form.get("salary_filter", ""),
                "easy_apply_only": form.get("easy_apply_only") == "true",
                "experience_level": [
                    x for x in form.getlist("experience_level") if x
                ],
                "job_type": [
                    x for x in form.getlist("job_type") if x
                ],
                "on_site": [
                    x for x in form.getlist("on_site") if x
                ],
            },
        },
        "screening": {
            "bad_words": [
                w.strip()
                for w in form.get("bad_words", "").split(",")
                if w.strip()
            ],
            "security_clearance": form.get("security_clearance") == "true",
            "max_experience_gap": int(form.get("max_experience_gap", 2) or 2),
        },
        "paths": {
            "career_ops": form.get("career_ops_path", ""),
            "linkedin_applier": form.get("linkedin_applier_path", ""),
            "node_bin": form.get("node_bin_path", ""),
        },
        "app": {
            "port": 7070,
            "auto_open_browser": True,
            "run_in_background": form.get("run_in_background") == "true",
            "click_gap": int(form.get("click_gap", 2) or 2),
            "pause_before_apply": form.get("pause_before_apply", "true") == "true",
        },
    }

    errors = config_manager.validate_settings(data)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    config_manager.save_settings(data)
    return jsonify({"ok": True, "redirect": url_for("dashboard.home")})


@setup_bp.route("/health")
def health():
    """Returns health check JSON — called by wizard JS to show status indicators."""
    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)
    return jsonify(config_manager.check_health(settings))


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

@dashboard_bp.route("/")
def home():
    settings = config_manager.load_settings()
    return render_template("dashboard.html", settings=settings)


def _format_session_label(session_str: str) -> str:
    if not session_str:
        return "Previous scans"
    try:
        dt = datetime.fromisoformat(session_str)
        today = date.today()
        yesterday = today - timedelta(days=1)
        time_str = dt.strftime('%H:%M')
        if dt.date() == today:
            return f"Today — {time_str}"
        elif dt.date() == yesterday:
            return f"Yesterday — {time_str}"
        else:
            months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
            return f"{months[dt.month-1]} {dt.day} — {time_str}"
    except Exception:
        return session_str


@dashboard_bp.route("/jobs")
def jobs():
    all_jobs = scraper.get_jobs()
    approved = scraper.get_approvals()
    scanning = scraper.is_scanning()

    # Group by scan_session, newest first
    buckets: dict = defaultdict(list)
    for job in all_jobs:
        buckets[job.get("scan_session", "")].append(job)

    grouped_jobs = sorted(
        [{"session": k, "label": _format_session_label(k), "jobs": v}
         for k, v in buckets.items()],
        key=lambda g: g["session"],
        reverse=True,
    )

    return render_template(
        "jobs.html",
        jobs=all_jobs,
        grouped_jobs=grouped_jobs,
        approved=approved,
        scanning=scanning,
    )


# ─────────────────────────────────────────────
# SCAN API
# ─────────────────────────────────────────────

@dashboard_bp.route("/api/scan/start")
def scan_start():
    """SSE endpoint — streams scraper progress + job records."""
    if scraper.is_scanning():
        return jsonify({"error": "Scan already running"}), 409

    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)

    def generate():
        yield "data: {\"type\": \"start\"}\n\n"
        for chunk in scraper.stream_scan(settings):
            yield chunk

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@dashboard_bp.route("/api/scan/stop", methods=["POST"])
def scan_stop():
    stopped = scraper.stop_scan()
    return jsonify({"stopped": stopped})


@dashboard_bp.route("/api/scan/status")
def scan_status():
    return jsonify({
        "scanning": scraper.is_scanning(),
        "job_count": len(scraper.get_jobs()),
    })


@dashboard_bp.route("/api/jobs")
def jobs_json():
    return jsonify(scraper.get_jobs())


@dashboard_bp.route("/api/jobs/approve", methods=["POST"])
def approve_jobs():
    """Receives {"job_ids": ["id1", "id2", ...]} and saves approvals."""
    data = request.get_json(force=True) or {}
    job_ids = data.get("job_ids", [])
    scraper.save_approvals(job_ids)
    return jsonify({"ok": True, "approved": len(job_ids)})


@dashboard_bp.route("/generate")
def generate():
    approved = scraper.get_approvals()
    all_jobs = scraper.get_jobs()
    approved_jobs = [j for j in all_jobs if j["job_id"] in approved]
    resumes = resume_generator.get_resumes()
    return render_template("generate.html", jobs=approved_jobs, resumes=resumes)


@dashboard_bp.route("/api/generate/start")
def generate_start():
    """SSE endpoint — streams resume generation progress."""
    settings = config_manager.load_settings()
    settings = config_manager.auto_detect_paths(settings)

    def go():
        for chunk in resume_generator.stream_generate(settings):
            yield chunk

    return Response(
        stream_with_context(go()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@dashboard_bp.route("/api/resumes/<job_id>/download")
def download_resume(job_id):
    resumes = resume_generator.get_resumes()
    pdf_path = resumes.get(job_id)
    if not pdf_path or not Path(pdf_path).exists():
        return jsonify({"error": "PDF not found"}), 404
    return send_file(pdf_path, as_attachment=True)


@dashboard_bp.route("/apply")
def apply_page():
    queue = applier.get_apply_queue()
    return render_template("apply.html", queue=queue)


@dashboard_bp.route("/api/jobs/<job_id>/delete", methods=["POST"])
def delete_job(job_id):
    ok = scraper.delete_job(job_id)
    return jsonify({"ok": ok})


def register_blueprints(app):
    app.register_blueprint(setup_bp)
    app.register_blueprint(dashboard_bp)
