"""
resume_generator.py -- Claude-powered resume tailoring + PDF generation.

Uses career-ops' full mode files as the system prompt:
  modes/_shared.md  → archetype detection, scoring rules, writing standards
  modes/_profile.md → user's bullet formula, keyword rules, target archetypes
  modes/pdf.md      → PDF generation pipeline (keyword injection, template filling)
  config/profile.yml → candidate identity
  article-digest.md  → proof points (if exists)

For each approved job:
  1. Assemble system prompt from career-ops mode files
  2. Call claude CLI (uses Claude Code subscription, no API key needed)
  3. Write tailored HTML to data/resumes/{job_id}.html
  4. Run `node generate-pdf.mjs` → PDF at data/resumes/{job_id}-{company}-{title}.pdf

SSE stream API:
  resume_generator.stream_generate(settings)  → generator of SSE strings
  resume_generator.get_resumes()              → dict {job_id: pdf_path}
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import config_manager
import scraper

# Use .resolve() so paths are always absolute regardless of how the module is imported
DATA_DIR = Path(__file__).resolve().parent / "data"
RESUMES_DIR = DATA_DIR / "resumes"
RESUMES_INDEX = DATA_DIR / "resumes.json"


# ── career-ops mode file readers ─────────────────────────────────────────────

def _read_career_ops_file(career_ops: Path, relative: str) -> str:
    p = career_ops / relative
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _build_system_prompt(career_ops: Path) -> str:
    """
    Minimal system prompt for PDF generation only.
    _shared.md is intentionally excluded — it contains evaluation/scoring rules
    irrelevant to PDF generation and adds ~8KB that causes CLI timeouts.
    """
    parts = []

    # User profile: target roles, superpower, work preference, bullet formula
    profile_md = _read_career_ops_file(career_ops, "modes/_profile.md")
    if profile_md:
        parts.append("# Candidate Profile\n\n" + profile_md)

    # PDF generation rules: layout, keyword injection, ATS rules, template placeholders
    pdf_mode = _read_career_ops_file(career_ops, "modes/pdf.md")
    if pdf_mode:
        parts.append("# PDF Generation Rules\n\n" + pdf_mode)

    # Optional: article-digest proof points
    article_digest = _read_career_ops_file(career_ops, "article-digest.md")
    if article_digest:
        parts.append("# Proof Points\n\n" + article_digest)

    parts.append(
        "# Task\n\n"
        "Generate a complete ATS-optimized tailored HTML resume.\n\n"
        "Rules:\n"
        "- Extract ALL hard and soft skill keywords from the JD\n"
        "- Inject top 3 keywords into Summary; 1-2 into first bullet of each role\n"
        "- Apply bullet formula: [Action verb] + [metric] + [method] + [context]\n"
        "- 3-5 bullets per role, strictly\n"
        "- Fill EVERY {{PLACEHOLDER}} in the HTML template with real content\n"
        "- US/Canada job location → page width 8.5in, else 210mm\n"
        "- Return ONLY the complete HTML. No markdown fences, no explanation.\n"
        "- NEVER invent experience or metrics not in the CV."
    )

    return "\n\n---\n\n".join(parts)


def _build_user_message(cv_md: str, jd_text: str, template_html: str) -> str:
    return (
        "## Candidate CV (source of truth — do NOT invent beyond this)\n\n"
        + cv_md
        + "\n\n---\n\n"
        "## Job Description\n\n"
        + jd_text
        + "\n\n---\n\n"
        "## HTML Template (fill every {{PLACEHOLDER}} with real tailored content)\n\n"
        + template_html
        + "\n\n---\n\n"
        "Generate the complete tailored HTML resume now. Return only the HTML."
    )


# ── core generation ───────────────────────────────────────────────────────────

def _ensure_dirs():
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)


def get_resumes() -> dict:
    _ensure_dirs()
    if not RESUMES_INDEX.exists():
        return {}
    try:
        return json.loads(RESUMES_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_resumes(index: dict):
    _ensure_dirs()
    RESUMES_INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _slug(text: str, max_len: int = 30) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text[:max_len]


def _call_claude_cli(system_prompt: str, user_message: str) -> str:
    """
    Run `claude -p` (Claude Code CLI) with the given prompts.
    Uses the active Claude Code subscription — no separate API key needed.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "claude CLI not found in PATH. Make sure Claude Code is installed."
        )

    result = subprocess.run(
        [claude_bin, "--system-prompt", system_prompt, "-p"],
        input=user_message,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )

    if result.returncode != 0:
        err = result.stderr.strip() or f"claude CLI exited with code {result.returncode}"
        raise RuntimeError(err)

    return result.stdout.strip()


def generate_resume(job: dict, settings: dict) -> dict:
    """
    Generate one tailored PDF resume for a job using career-ops' full mode files.
    Returns {"job_id": ..., "pdf_path": ..., "ok": True/False, "error": "..."}
    """
    _ensure_dirs()

    job_id = job["job_id"]
    title = job.get("title", "role")
    company = job.get("company", "company")
    jd_text = job.get("job_description", "")

    cv_md = config_manager.read_cv_md(settings)
    template_html = config_manager.read_resume_template(settings)

    if not cv_md:
        return {"job_id": job_id, "ok": False, "error": "cv.md not found — add your CV to job-forge/cv.md"}
    if not template_html:
        return {"job_id": job_id, "ok": False, "error": "cv-template.html not found in job-forge/templates/"}

    system_prompt = _build_system_prompt(config_manager.get_career_ops_dir(settings))
    user_message = _build_user_message(cv_md, jd_text, template_html)

    try:
        tailored_html = _call_claude_cli(system_prompt, user_message)
    except Exception as e:
        return {"job_id": job_id, "ok": False, "error": f"Claude CLI error: {e}"}

    # Strip any accidental markdown fence
    tailored_html = re.sub(r"^```html?\s*", "", tailored_html.strip(), flags=re.IGNORECASE)
    tailored_html = re.sub(r"\s*```$", "", tailored_html.strip())

    # Detect unfilled placeholders — means Claude was truncated or skipped them
    unfilled = re.findall(r"\{\{[A-Z_]+\}\}", tailored_html)
    if unfilled:
        return {
            "job_id": job_id,
            "ok": False,
            "error": f"Claude left {len(unfilled)} unfilled placeholder(s): {', '.join(set(unfilled))}. "
                     "Try regenerating — this usually means the output was truncated.",
        }

    # Write tailored HTML
    html_path = RESUMES_DIR / f"{job_id}.html"
    html_path.write_text(tailored_html, encoding="utf-8")

    # Paper format from job location
    work_location = job.get("work_location", "")
    paper = "letter" if any(
        x in work_location.lower() for x in ["united states", " us,", "canada"]
    ) else "a4"

    # PDF path
    company_slug = _slug(company)
    title_slug = _slug(title)
    pdf_name = f"{job_id}-{company_slug}-{title_slug}.pdf"
    pdf_path = RESUMES_DIR / pdf_name

    node_bin = config_manager.get_node_bin(settings)
    gen_pdf_script = config_manager.INTERNAL_PDF_SCRIPT

    if not gen_pdf_script.exists():
        return {
            "job_id": job_id,
            "ok": False,
            "error": f"generate-pdf.mjs not found at {gen_pdf_script}",
        }

    try:
        result = subprocess.run(
            [node_bin, str(gen_pdf_script), str(html_path), str(pdf_path), f"--format={paper}"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(config_manager._ROOT),
        )
        if result.returncode != 0:
            return {
                "job_id": job_id,
                "ok": False,
                "error": f"PDF generation failed: {result.stderr[:300]}",
            }
    except subprocess.TimeoutExpired:
        return {"job_id": job_id, "ok": False, "error": "PDF generation timed out"}
    except Exception as e:
        return {"job_id": job_id, "ok": False, "error": f"PDF subprocess error: {e}"}

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "pdf_path": str(pdf_path),
        "pdf_name": pdf_name,
        "ok": True,
    }


# ── SSE stream ────────────────────────────────────────────────────────────────

def stream_generate(settings: dict):
    """
    Generator — yields SSE strings.
    Processes all approved jobs, generates one PDF per job.
    """

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    all_jobs = scraper.get_jobs()
    approved = scraper.get_approvals()
    approved_jobs = [j for j in all_jobs if j["job_id"] in approved]

    if not approved_jobs:
        yield sse({"type": "error", "msg": "No approved jobs. Go back and approve some jobs first."})
        return

    yield sse({"type": "start", "total": len(approved_jobs)})

    index = get_resumes()
    done = 0

    for job in approved_jobs:
        job_id = job["job_id"]
        title = job.get("title", "?")
        company = job.get("company", "?")

        yield sse({
            "type": "progress",
            "job_id": job_id,
            "msg": f'Tailoring CV for "{title}" at {company} (archetype detection + keyword injection)...',
        })

        result = generate_resume(job, settings)

        if result["ok"]:
            index[job_id] = result["pdf_path"]
            _save_resumes(index)
            done += 1
            yield sse({
                "type": "done_job",
                "job_id": job_id,
                "title": title,
                "company": company,
                "pdf_name": result["pdf_name"],
                "ok": True,
            })
        else:
            yield sse({
                "type": "done_job",
                "job_id": job_id,
                "title": title,
                "company": company,
                "ok": False,
                "error": result["error"],
            })

    yield sse({"type": "complete", "succeeded": done, "total": len(approved_jobs)})
