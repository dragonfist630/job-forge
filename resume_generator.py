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


_EXPERIENCE_FORMAT = (
    '<div class="job">'
    '<div class="job-header">'
    '<span class="job-company">Company Name</span>'
    '<span class="job-period">Jan 2022 – Mar 2024</span>'
    '</div>'
    '<div class="job-role">Job Title</div>'
    '<ul>'
    '<li><strong>JD keyword phrase</strong> — metric + method + context</li>'
    '<li>Achievement with metric and method</li>'
    '<li>Achievement with metric and method</li>'
    '</ul>'
    '</div>'
)

_PROJECTS_FORMAT = (
    '<div class="project">'
    '<div><span class="project-title">Project Name</span>'
    '<span class="project-badge">Live</span></div>'
    '<div class="project-desc">One sentence description with impact metric.</div>'
    '<div class="project-tech">Python · React · PostgreSQL</div>'
    '</div>'
)

_EDUCATION_FORMAT = (
    '<div class="edu-item">'
    '<div class="edu-header">'
    '<div><span class="edu-title">Degree</span> — <span class="edu-org">University</span></div>'
    '<span class="edu-year">2020 – 2022</span>'
    '</div>'
    '<div class="edu-desc">Relevant modules or dissertation note.</div>'
    '</div>'
)

_SKILLS_FORMAT = (
    '<div class="skills-grid">'
    '<span class="skill-item"><span class="skill-category">Languages:</span> Python, JavaScript</span>'
    '<span class="skill-item"><span class="skill-category">Frameworks:</span> React, Node.js</span>'
    '</div>'
)

# Only content placeholders — identity/contact/labels are pre-filled from settings+job data
_PLACEHOLDER_DESCRIPTIONS = {
    "SUMMARY_TEXT":   "keyword-dense summary — 60 words MAX, open with total years of experience calculated from CV dates, inject top 3 JD keywords (mix hard+soft), no filler phrases",
    "COMPETENCIES":   '6-8 items mixing hard + soft skills: <span class="competency-tag">keyword</span> — use exact JD phrases, include at least 2 soft skill tags',
    "EXPERIENCE":     f"all roles as HTML. Use EXACTLY this structure per role. Each <li> MUST follow: \"Accomplished [X] as measured by [Y], by doing [Z]\" — X=outcome, Y=metric, Z=method. First li injects JD keyword into X or Z. Last li surfaces a soft skill (collaboration/ownership/initiative). 3-5 li per role: {_EXPERIENCE_FORMAT}",
    "PROJECTS":       f"top 3 most JD-relevant projects. Use EXACTLY this structure per project: {_PROJECTS_FORMAT}",
    "EDUCATION":      f"education entries. Use EXACTLY this structure per entry: {_EDUCATION_FORMAT}",
    "CERTIFICATIONS": 'certifications as <div class="cert-item"><div><span class="cert-title">Name</span> — <span class="cert-org">Org</span></div><span class="cert-year">2023</span></div> per cert. Empty string if none in CV.',
    "SKILLS":         f"skills grouped by category. Use EXACTLY this structure: {_SKILLS_FORMAT}",
}


def _extract_placeholders(template_html: str) -> list:
    return sorted(set(re.findall(r"\{\{([A-Z_]+)\}\}", template_html)))


def _prefill_from_context(job: dict, settings: dict) -> dict:
    """
    Build all placeholder values we know without Claude:
    identity, contact info, job title, section labels, page size.
    Pre-filling these means Claude never sees them — no hallucination risk.
    """
    identity = settings.get("identity", {})
    location = settings.get("location", {})
    online = settings.get("online_presence", {})

    first = identity.get("first_name", "")
    last = identity.get("last_name", "")
    name = f"{first} {last}".strip()

    city = location.get("city", "")
    country = location.get("country", "")
    loc = ", ".join(x for x in [city, country] if x)

    linkedin_url = (online.get("linkedin") or "").rstrip("/")
    linkedin_display = linkedin_url.replace("https://", "").replace("http://", "")

    portfolio_url = (online.get("portfolio") or "").rstrip("/")
    portfolio_display = portfolio_url.replace("https://", "").replace("http://", "") if portfolio_url else ""

    work_location = job.get("work_location", "")
    page_width = "8.5in" if any(
        x in work_location.lower() for x in ["united states", " us,", "canada"]
    ) else "210mm"

    return {
        "LANG":                   "en",
        "PAGE_WIDTH":             page_width,
        "NAME":                   name,
        "TARGET_ROLE":            job.get("title", ""),
        "PHONE":                  identity.get("phone_number", ""),
        "EMAIL":                  identity.get("email", ""),
        "LINKEDIN_URL":           linkedin_url,
        "LINKEDIN_DISPLAY":       linkedin_display,
        "PORTFOLIO_URL":          portfolio_url,
        "PORTFOLIO_DISPLAY":      portfolio_display,
        "LOCATION":               loc,
        "SECTION_SUMMARY":        "Professional Summary",
        "SECTION_COMPETENCIES":   "Core Competencies",
        "SECTION_EXPERIENCE":     "Work Experience",
        "SECTION_PROJECTS":       "Projects",
        "SECTION_EDUCATION":      "Education",
        "SECTION_CERTIFICATIONS": "Certifications",
        "SECTION_SKILLS":         "Skills",
    }


def _build_system_prompt(career_ops: Path) -> str:
    """
    Lean system prompt: candidate profile + task rules only.
    _shared.md (evaluation/scoring) and pdf.md (Canva workflow, Spanish pipeline)
    are excluded — irrelevant for resume generation, save ~2,500 tokens.
    """
    parts = []

    profile_md = _read_career_ops_file(career_ops, "modes/_profile.md")
    if profile_md:
        parts.append("# Candidate Profile\n\n" + profile_md)

    article_digest = _read_career_ops_file(career_ops, "article-digest.md")
    if article_digest:
        parts.append("# Proof Points\n\n" + article_digest)

    parts.append(
        "# Task\n\n"
        "You are a resume writer. Given a CV and a job description, fill the template placeholders "
        "and return a single JSON object — nothing else.\n\n"
        "Rules:\n"
        "- Extract ALL hard AND soft keywords from the JD (collaboration, communication, ownership, etc.)\n"
        "- Also extract soft skills directly from the CV bullets and surface them in the summary and experience\n"
        "- Inject top 3 JD keywords (mix of hard + soft) into SUMMARY_TEXT\n"
        "- SUMMARY_TEXT must be 60 words maximum — open with total years of experience calculated from CV dates (e.g. '3+ years of experience in...')\n"
        "- Inject 1-2 JD keywords into the first bullet of each role in EXPERIENCE\n"
        "- Every bullet MUST follow this exact formula: \"Accomplished [X] as measured by [Y], by doing [Z]\"\n"
        "  X = the positive outcome or improvement (what was achieved)\n"
        "  Y = quantifiable metric proving impact (number, %, $ amount, time saved) — NEVER omit this\n"
        "  Z = specific actions, methods, or tools used to get the result\n"
        "  Example: \"Reduced API response time by 44% as measured by p95 latency, by migrating Flask monolith to FastAPI microservices\"\n"
        "- 3-5 bullets per role. Never use vague bullets without a metric.\n"
        "- Do NOT require explicit years of experience — match skills and responsibilities to the role regardless\n"
        "- NEVER invent metrics or experience not in the CV\n"
        "- Return ONLY a valid JSON object {\"PLACEHOLDER\": \"value\"}. No explanation, no markdown."
    )

    return "\n\n---\n\n".join(parts)


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode basic entities for audit comparison."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _format_structured_cv(structured: dict) -> str:
    """
    Convert cv-structured.json to compact readable text for Claude.
    Header makes clear: use ONLY these verified facts.
    """
    lines = ["## Verified CV Data — use ONLY these facts. Do not add, infer, or invent anything not listed here.\n"]

    for role in structured.get("roles", []):
        lines.append(f"### {role.get('company','')} | {role.get('title','')} | {role.get('dates','')}")
        for b in role.get("bullets", []):
            lines.append(f"• {b}")
        if role.get("technologies"):
            lines.append(f"Technologies: {', '.join(role['technologies'])}")
        if role.get("metrics"):
            lines.append(f"Metrics: {', '.join(role['metrics'])}")
        lines.append("")

    if structured.get("projects"):
        lines.append("## Verified Projects\n")
        for p in structured["projects"]:
            lines.append(f"### {p.get('name','')}")
            if p.get("description"):
                lines.append(p["description"])
            if p.get("technologies"):
                lines.append(f"Technologies: {', '.join(p['technologies'])}")
            if p.get("metrics"):
                lines.append(f"Metrics: {', '.join(p['metrics'])}")
            lines.append("")

    if structured.get("technologies"):
        lines.append(f"## Verified Technologies\n{', '.join(structured['technologies'])}\n")

    combined_skills = structured.get("skills", []) + structured.get("soft_skills", [])
    if combined_skills:
        lines.append(f"## Verified Skills & Soft Skills\n{', '.join(combined_skills)}\n")

    if structured.get("education"):
        lines.append("## Verified Education")
        for e in structured["education"]:
            lines.append(f"• {e.get('degree','')} — {e.get('institution','')} ({e.get('dates','')})")
            if e.get("description"):
                lines.append(f"  {e['description']}")
        lines.append("")

    if structured.get("certifications"):
        lines.append("## Verified Certifications")
        for c in structured["certifications"]:
            lines.append(f"• {c}")

    return "\n".join(lines)


def _audit_generated_content(generated_json: dict, structured_cv: dict) -> dict:
    """
    Plan 4: lightweight Claude call to verify generated content against structured CV.
    Returns {"pass": True} or {"pass": False, "violations": ["short description", ...]}.
    Never raises — on any error returns {"pass": True} to avoid blocking generation.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return {"pass": True}

    # Strip HTML from content fields before auditing
    content_to_audit = {
        k: _strip_html(str(generated_json[k]))
        for k in ("SUMMARY_TEXT", "EXPERIENCE", "PROJECTS", "COMPETENCIES", "SKILLS")
        if k in generated_json
    }

    audit_system = (
        "You are a resume content auditor. Compare generated resume content against verified CV data.\n\n"
        "Flag ONLY items that are clearly fabricated:\n"
        "- Specific metrics (numbers, %) not present in any form in the verified CV\n"
        "- Technologies or tools not mentioned in the verified CV\n"
        "- Job responsibilities or achievements with no basis in the verified CV\n\n"
        "Do NOT flag:\n"
        "- Rephrased/reworded bullets that express the same verified fact\n"
        "- Skills reasonably implied by listed technologies\n"
        "- Soft skills visible in CV bullets even if not in soft_skills[]\n\n"
        "Return ONLY JSON: {\"pass\": true} if clean, "
        "or {\"pass\": false, \"violations\": [\"short description\"]} if fabricated items found. "
        "No explanation, no markdown."
    )

    audit_user = (
        "## Verified CV Data\n\n"
        + json.dumps(structured_cv, indent=2, ensure_ascii=False)
        + "\n\n## Generated Resume Content (HTML stripped)\n\n"
        + json.dumps(content_to_audit, indent=2, ensure_ascii=False)
        + "\n\nReturn ONLY JSON."
    )

    try:
        result = subprocess.run(
            [claude_bin, "--system-prompt", audit_system, "-p"],
            input=audit_user,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        if result.returncode != 0:
            return {"pass": True}
        raw = result.stdout.strip()
        raw = re.sub(r"^```json?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"pass": True}
        return json.loads(m.group())
    except Exception:
        return {"pass": True}


def _build_user_message(cv_text: str, jd_text: str, template_html: str, skip_keys: set) -> str:
    # Truncate JD — boilerplate after ~4000 chars rarely adds signal
    jd_trimmed = jd_text[:4000] if len(jd_text) > 4000 else jd_text

    placeholders = [p for p in _extract_placeholders(template_html) if p not in skip_keys]
    manifest_lines = ["Fill these placeholders (return as JSON):"]
    for p in placeholders:
        desc = _PLACEHOLDER_DESCRIPTIONS.get(p, "appropriate content")
        manifest_lines.append(f"  {p}: {desc}")
    manifest = "\n".join(manifest_lines)

    return (
        "## Candidate CV\n\n" + cv_text
        + "\n\n---\n\n## Job Description\n\n" + jd_trimmed
        + "\n\n---\n\n## " + manifest
        + "\n\nReturn ONLY a JSON object."
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

    # Pre-fill all identity/contact/title/label placeholders from settings + job data
    prefilled = _prefill_from_context(job, settings)
    paper = "letter" if prefilled["PAGE_WIDTH"] == "8.5in" else "a4"

    # Apply pre-filled values to template
    partial_html = template_html
    for key, val in prefilled.items():
        partial_html = partial_html.replace("{{" + key + "}}", str(val) if val is not None else "")

    # Plan 3: load structured CV (auto-extract if stale)
    import cv_extractor
    structured_cv = cv_extractor.extract()
    if structured_cv:
        cv_text = _format_structured_cv(structured_cv)
    else:
        # fallback to raw cv.md if extraction unavailable
        cv_text = cv_md

    system_prompt = _build_system_prompt(config_manager.get_career_ops_dir(settings))
    user_message = _build_user_message(cv_text, jd_text, partial_html, skip_keys=set(prefilled))

    try:
        raw_response = _call_claude_cli(system_prompt, user_message)
    except Exception as e:
        return {"job_id": job_id, "ok": False, "error": f"Claude CLI error: {e}"}

    # Parse JSON response — strip any accidental markdown fences first
    raw = raw_response.strip()
    raw = re.sub(r"^```json?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    # Extract first {...} block in case there's surrounding text
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {"job_id": job_id, "ok": False, "error": "Claude returned no JSON object"}
    try:
        values = json.loads(m.group())
    except json.JSONDecodeError as e:
        return {"job_id": job_id, "ok": False, "error": f"Claude returned invalid JSON: {e}"}

    # Plan 4: audit generated content against structured CV
    audit_warnings = []
    if structured_cv:
        audit_result = _audit_generated_content(values, structured_cv)
        if not audit_result.get("pass", True):
            audit_warnings = audit_result.get("violations", [])

    # Fill Claude's content into the partially-filled template
    tailored_html = partial_html
    for key, val in values.items():
        tailored_html = tailored_html.replace("{{" + key + "}}", str(val) if val is not None else "")

    # Detect unfilled placeholders
    unfilled = re.findall(r"\{\{[A-Z_]+\}\}", tailored_html)
    if unfilled:
        return {
            "job_id": job_id,
            "ok": False,
            "error": f"Claude skipped {len(unfilled)} placeholder(s): {', '.join(set(unfilled))}. Try regenerating.",
        }

    # Write tailored HTML
    html_path = RESUMES_DIR / f"{job_id}.html"
    html_path.write_text(tailored_html, encoding="utf-8")

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
        "audit_warnings": audit_warnings,
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
                "audit_warnings": result.get("audit_warnings", []),
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
