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
import threading
import time
from pathlib import Path

import config_manager
import scraper

# ── per-job generation state ──────────────────────────────────────────────────
# Each independently-triggered job gets its own entry: {proc, stopped}
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Global state for "Generate All" sequential flow
_paused = False
_stopped = False
_active = False
_state_lock = threading.Lock()


def set_paused(val: bool):
    global _paused
    _paused = val


def set_stopped(val: bool):
    global _stopped
    _stopped = val


def is_generating() -> bool:
    return _active


def stop_job(job_id: str) -> bool:
    """Terminate a per-job generation subprocess and mark it stopped."""
    with _jobs_lock:
        state = _jobs.get(job_id)
        if not state:
            return False
        state["stopped"] = True
        proc = state.get("proc")
        if proc and proc.poll() is None:
            proc.terminate()
    return True


def is_job_generating(job_id: str) -> bool:
    with _jobs_lock:
        return job_id in _jobs

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


def _save_resume_content(job_id: str, values: dict):
    """Persist Claude's raw values dict so the editor can load and modify it."""
    _ensure_dirs()
    path = RESUMES_DIR / f"{job_id}-content.json"
    path.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")


def get_resume_content(job_id: str) -> dict | None:
    path = RESUMES_DIR / f"{job_id}-content.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tag_text(html: str, tag: str, cls: str) -> str:
    """Extract plain text from first element matching tag + class."""
    m = re.search(
        rf'<{tag}[^>]*class="[^"]*{re.escape(cls)}[^"]*"[^>]*>(.*?)</{tag}>',
        html, re.DOTALL
    )
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def values_to_structured(values: dict) -> dict:
    """Parse HTML values dict → editor-friendly structured dict."""
    s: dict = {}

    if "SUMMARY_TEXT" in values:
        s["SUMMARY_TEXT"] = re.sub(r"<[^>]+>", " ", values["SUMMARY_TEXT"]).strip()

    if "COMPETENCIES" in values:
        tags = re.findall(
            r'class="competency-tag"[^>]*>(.*?)</span>', values["COMPETENCIES"], re.DOTALL
        )
        s["COMPETENCIES"] = [re.sub(r"<[^>]+>", "", t).strip() for t in tags if t.strip()]

    if "EXPERIENCE" in values:
        roles = []
        for block in re.split(r'(?=<div class="job">)', values["EXPERIENCE"]):
            if '<div class="job">' not in block:
                continue
            bullets = [
                re.sub(r"<[^>]+>", "", li).strip()
                for li in re.findall(r"<li>(.*?)</li>", block, re.DOTALL)
                if li.strip()
            ]
            company = _tag_text(block, "span", "job-company")
            period = _tag_text(block, "span", "job-period")
            role_title = _tag_text(block, "div", "job-role")
            if company or role_title:
                roles.append({"company": company, "period": period,
                               "role": role_title, "bullets": bullets})
        s["EXPERIENCE"] = roles

    if "PROJECTS" in values:
        projects = []
        for block in re.split(r'(?=<div class="project">)', values["PROJECTS"]):
            if '<div class="project">' not in block:
                continue
            name = _tag_text(block, "span", "project-title")
            if name:
                projects.append({
                    "name": name,
                    "badge": _tag_text(block, "span", "project-badge"),
                    "description": _tag_text(block, "div", "project-desc"),
                    "tech": _tag_text(block, "div", "project-tech"),
                })
        s["PROJECTS"] = projects

    if "EDUCATION" in values:
        edu_list = []
        for block in re.split(r'(?=<div class="edu-item">)', values["EDUCATION"]):
            if '<div class="edu-item">' not in block:
                continue
            degree = _tag_text(block, "span", "edu-title")
            org = _tag_text(block, "span", "edu-org")
            if degree or org:
                edu_list.append({
                    "degree": degree, "org": org,
                    "year": _tag_text(block, "span", "edu-year"),
                    "description": _tag_text(block, "div", "edu-desc"),
                })
        s["EDUCATION"] = edu_list

    if "CERTIFICATIONS" in values:
        certs = []
        for block in re.split(r'(?=<div class="cert-item">)', values["CERTIFICATIONS"]):
            if '<div class="cert-item">' not in block:
                continue
            title = _tag_text(block, "span", "cert-title")
            if title:
                certs.append({
                    "title": title,
                    "org": _tag_text(block, "span", "cert-org"),
                    "year": _tag_text(block, "span", "cert-year"),
                })
        s["CERTIFICATIONS"] = certs

    if "SKILLS" in values:
        rows = re.findall(
            r'<span class="skill-category">(.*?):</span>\s*(.*?)</span>',
            values["SKILLS"], re.DOTALL
        )
        s["SKILLS"] = [
            {"category": re.sub(r"<[^>]+>", "", cat).strip(),
             "items": re.sub(r"<[^>]+>", "", items).strip()}
            for cat, items in rows
        ]

    return s


def structured_to_values(structured: dict) -> dict:
    """Convert editor structured dict → HTML values dict for template filling."""
    import html as _html

    def esc(text) -> str:
        return _html.escape(str(text or ""))

    values: dict = {}

    if "SUMMARY_TEXT" in structured:
        values["SUMMARY_TEXT"] = esc(structured["SUMMARY_TEXT"])

    if "COMPETENCIES" in structured:
        tags = structured["COMPETENCIES"]
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        values["COMPETENCIES"] = "".join(
            f'<span class="competency-tag">{esc(t)}</span>' for t in tags if str(t).strip()
        )

    if "EXPERIENCE" in structured:
        html = ""
        for role in structured["EXPERIENCE"]:
            bullets = role.get("bullets", [])
            if isinstance(bullets, str):
                bullets = [b for b in bullets.splitlines() if b.strip()]
            bullets_html = "".join(f"<li>{esc(b)}</li>" for b in bullets if str(b).strip())
            html += (
                f'<div class="job">'
                f'<div class="job-header">'
                f'<span class="job-company">{esc(role.get("company",""))}</span>'
                f'<span class="job-period">{esc(role.get("period",""))}</span>'
                f'</div>'
                f'<div class="job-role">{esc(role.get("role",""))}</div>'
                f'<ul>{bullets_html}</ul>'
                f'</div>'
            )
        values["EXPERIENCE"] = html

    if "PROJECTS" in structured:
        html = ""
        for proj in structured["PROJECTS"]:
            badge = proj.get("badge", "")
            badge_html = f'<span class="project-badge">{esc(badge)}</span>' if badge else ""
            html += (
                f'<div class="project">'
                f'<div><span class="project-title">{esc(proj.get("name",""))}</span>{badge_html}</div>'
                f'<div class="project-desc">{esc(proj.get("description",""))}</div>'
                f'<div class="project-tech">{esc(proj.get("tech",""))}</div>'
                f'</div>'
            )
        values["PROJECTS"] = html

    if "EDUCATION" in structured:
        html = ""
        for edu in structured["EDUCATION"]:
            html += (
                f'<div class="edu-item">'
                f'<div class="edu-header">'
                f'<div><span class="edu-title">{esc(edu.get("degree",""))}</span>'
                f' — <span class="edu-org">{esc(edu.get("org",""))}</span></div>'
                f'<span class="edu-year">{esc(edu.get("year",""))}</span>'
                f'</div>'
                f'<div class="edu-desc">{esc(edu.get("description",""))}</div>'
                f'</div>'
            )
        values["EDUCATION"] = html

    if "CERTIFICATIONS" in structured:
        html = ""
        for cert in structured["CERTIFICATIONS"]:
            html += (
                f'<div class="cert-item">'
                f'<div><span class="cert-title">{esc(cert.get("title",""))}</span>'
                f' — <span class="cert-org">{esc(cert.get("org",""))}</span></div>'
                f'<span class="cert-year">{esc(cert.get("year",""))}</span>'
                f'</div>'
            )
        values["CERTIFICATIONS"] = html

    if "SKILLS" in structured:
        spans = ""
        for row in structured["SKILLS"]:
            cat = row.get("category", "")
            items = row.get("items", "")
            spans += (f'<span class="skill-item">'
                      f'<span class="skill-category">{esc(cat)}:</span> {esc(items)}'
                      f'</span>')
        values["SKILLS"] = f'<div class="skills-grid">{spans}</div>'

    return values


def render_resume_html(job: dict, settings: dict, values: dict) -> str:
    """Re-render resume HTML from a values dict — no Claude call."""
    template_html = config_manager.read_resume_template(settings)
    if not template_html:
        raise ValueError("cv-template.html not found in job-forge/templates/")
    prefilled = _prefill_from_context(job, settings)
    html = template_html
    for key, val in prefilled.items():
        html = html.replace("{{" + key + "}}", str(val) if val is not None else "")
    for key, val in values.items():
        html = html.replace("{{" + key + "}}", str(val) if val is not None else "")
    return html


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


def _call_claude_relay(system_prompt: str, user_message: str) -> str:
    """Call claude via the host relay (Docker mode — claude binary is macOS-only)."""
    import urllib.request
    import json as _j
    body = _j.dumps({"system": system_prompt, "user": user_message}).encode()
    req = urllib.request.Request(
        "http://host.docker.internal:7071",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=620) as r:
            result = _j.loads(r.read())
    except Exception as e:
        raise RuntimeError(f"Claude relay unreachable: {e}. Make sure JobForge.command is running.")
    if result.get("code", 1) != 0:
        err = result.get("err") or f"claude exited {result.get('code')}"
        raise RuntimeError(err)
    return result.get("out", "").strip()


def _call_claude_cli(system_prompt: str, user_message: str, proc_callback=None) -> str:
    """
    Run `claude -p` using Popen so callers can register the proc for stop support.
    In Docker mode, delegates to the host relay instead (binary is macOS-only).
    proc_callback(proc) is called immediately after Popen, before communicate().
    """
    if os.environ.get("JOBFORGE_DOCKER"):
        return _call_claude_relay(system_prompt, user_message)

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "claude CLI not found in PATH. Make sure Claude Code is installed."
        )

    proc = subprocess.Popen(
        [claude_bin, "--system-prompt", system_prompt, "-p"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if proc_callback:
        proc_callback(proc)

    try:
        stdout, stderr = proc.communicate(input=user_message, timeout=600)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError("Claude CLI timed out after 600s")

    # returncode < 0 means killed/terminated by signal (stop requested)
    if proc.returncode < 0:
        raise RuntimeError("__stopped__")
    if proc.returncode != 0:
        err = stderr.strip() or f"claude CLI exited with code {proc.returncode}"
        raise RuntimeError(err)

    return stdout.strip()


def generate_resume(job: dict, settings: dict, proc_callback=None) -> dict:
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
        raw_response = _call_claude_cli(system_prompt, user_message, proc_callback=proc_callback)
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

    # Save content JSON for editor
    _save_resume_content(job_id, values)

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

def stream_generate(settings: dict, job_ids=None):
    """
    Generator — yields SSE strings.
    job_ids=None  → all approved jobs not yet in resumes index (Generate All)
    job_ids=[...] → exactly those job IDs (per-job generate / regenerate)

    Checks _paused/_stopped flags between jobs.
    Yields {"type":"ping"} keepalives while paused (prevents SSE timeout).
    """
    global _active, _paused, _stopped

    with _state_lock:
        if _active:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'Generation already in progress'})}\n\n"
            return
        _active = True
        _paused = False
        _stopped = False

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    try:
        all_jobs = scraper.get_jobs()
        approved = scraper.get_approvals()
        index = get_resumes()

        if job_ids is not None:
            job_map = {j["job_id"]: j for j in all_jobs}
            target_jobs = [job_map[jid] for jid in job_ids if jid in job_map and jid in approved]
        else:
            # Generate All: skip already-done jobs
            target_jobs = [j for j in all_jobs if j["job_id"] in approved and j["job_id"] not in index]

        if not target_jobs:
            yield sse({"type": "error", "msg": "No jobs to generate. Approve jobs first, or all approved jobs already have resumes."})
            return

        yield sse({"type": "start", "total": len(target_jobs)})
        done = 0

        for job in target_jobs:
            # Stop check before each job
            with _state_lock:
                if _stopped:
                    yield sse({"type": "stopped", "done": done})
                    return

            # Pause loop — send keepalives so SSE connection stays alive
            while True:
                with _state_lock:
                    is_paused = _paused
                if not is_paused:
                    break
                yield sse({"type": "ping"})
                time.sleep(2)

            job_id = job["job_id"]
            title = job.get("title", "?")
            company = job.get("company", "?")

            yield sse({
                "type": "progress",
                "job_id": job_id,
                "msg": f'Tailoring CV for "{title}" at {company}...',
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

        yield sse({"type": "complete", "succeeded": done, "total": len(target_jobs)})

    finally:
        with _state_lock:
            _active = False
            _paused = False
            _stopped = False


def stream_generate_single(settings: dict, job_id: str):
    """
    SSE generator for a single job. Supports stop mid-generation via stop_job().
    Multiple concurrent calls with different job_ids are allowed.
    """
    with _jobs_lock:
        if job_id in _jobs:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'Already generating this job'})}\n\n"
            return
        _jobs[job_id] = {"proc": None, "stopped": False}

    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    def register_proc(proc):
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["proc"] = proc

    try:
        all_jobs = scraper.get_jobs()
        job_map = {j["job_id"]: j for j in all_jobs}
        approved = scraper.get_approvals()

        if job_id not in job_map or job_id not in approved:
            yield sse({"type": "error", "msg": "Job not found or not approved"})
            return

        job = job_map[job_id]
        title = job.get("title", "?")
        company = job.get("company", "?")

        yield sse({"type": "start", "total": 1})
        yield sse({
            "type": "progress",
            "job_id": job_id,
            "msg": f'Tailoring CV for "{title}" at {company}...',
        })

        # Run generation in daemon thread so we can yield keepalives + watch stop flag
        result_box: list = [None]
        error_box: list = [None]

        def run():
            try:
                result_box[0] = generate_resume(job, settings, proc_callback=register_proc)
            except Exception as exc:
                error_box[0] = str(exc)

        t = threading.Thread(target=run, daemon=True)
        t.start()

        while t.is_alive():
            with _jobs_lock:
                if _jobs.get(job_id, {}).get("stopped"):
                    break
            yield sse({"type": "ping"})
            time.sleep(2)

        t.join()

        # Check stopped (set by stop_job OR by proc returning -signal)
        with _jobs_lock:
            stopped = _jobs.get(job_id, {}).get("stopped", False)
        if stopped or error_box[0] == "__stopped__":
            yield sse({"type": "stopped", "job_id": job_id})
            return

        if error_box[0]:
            yield sse({"type": "done_job", "job_id": job_id, "ok": False,
                        "error": error_box[0], "title": title, "company": company})
            yield sse({"type": "complete", "succeeded": 0, "total": 1})
            return

        result = result_box[0]
        if result["ok"]:
            index = get_resumes()
            index[job_id] = result["pdf_path"]
            _save_resumes(index)
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

        yield sse({"type": "complete",
                   "succeeded": 1 if result["ok"] else 0,
                   "total": 1})

    finally:
        with _jobs_lock:
            _jobs.pop(job_id, None)
