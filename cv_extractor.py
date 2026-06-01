"""
cv_extractor.py -- One-time CV structuring (Plan 3).
cv.md → cv-structured.json via Claude CLI.
Cached — re-runs only when cv.md is newer than the JSON.
"""

import json
import re
import shutil
import subprocess

import config_manager

CV_STRUCTURED_PATH = config_manager.INTERNAL_CV_STRUCTURED_PATH

_EXTRACTION_SYSTEM_PROMPT = """\
You are a CV parser. Extract structured data from the CV text and return ONLY valid JSON.

Schema:
{
  "roles": [{"company": "", "title": "", "dates": "", "location": "", "bullets": [], "technologies": [], "metrics": []}],
  "projects": [{"name": "", "description": "", "technologies": [], "metrics": [], "url": ""}],
  "education": [{"degree": "", "institution": "", "dates": "", "description": ""}],
  "certifications": [],
  "skills": [],
  "technologies": [],
  "soft_skills": []
}

Rules:
- metrics[]: extract ALL numbers, percentages, amounts, durations found in bullets (e.g. "65ms", "40%", "10K")
- technologies[]: all tools, languages, frameworks, platforms mentioned anywhere
- skills[]: professional/domain skills (not technologies)
- soft_skills[]: interpersonal skills found in bullets (collaboration, cross-functional, stakeholder, agile, etc.)
- Extract EXACTLY what is written — do not rephrase, do not infer, do not add
- Include ALL roles, ALL projects
- Return ONLY the JSON object. No explanation, no markdown.\
"""


def is_stale() -> bool:
    """True if cv-structured.json is missing or older than cv.md."""
    cv_path = config_manager.INTERNAL_CV_PATH
    if not CV_STRUCTURED_PATH.exists():
        return True
    if not cv_path.exists():
        return False  # no cv.md at all — nothing to extract
    return cv_path.stat().st_mtime > CV_STRUCTURED_PATH.stat().st_mtime


def extract(force: bool = False) -> dict:
    """
    Load cached cv-structured.json if fresh, else run Claude CLI extraction.
    Returns empty dict on any failure — never raises.
    """
    try:
        cv_path = config_manager.INTERNAL_CV_PATH
        if not cv_path.exists():
            return {}

        # Return cached version if still fresh
        if not force and not is_stale():
            try:
                return json.loads(CV_STRUCTURED_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass  # fall through to re-extract

        cv_text = cv_path.read_text(encoding="utf-8")
        if not cv_text.strip():
            return {}

        claude_bin = shutil.which("claude")
        if not claude_bin:
            return {}

        result = subprocess.run(
            [claude_bin, "--system-prompt", _EXTRACTION_SYSTEM_PROMPT, "-p"],
            input=cv_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )

        if result.returncode != 0:
            return {}

        raw = result.stdout.strip()
        # Strip accidental markdown fences
        raw = re.sub(r"^```json?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
        # Extract first {...} block in case there's surrounding text
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {}

        structured = json.loads(m.group())

        # Cache to disk
        CV_STRUCTURED_PATH.write_text(
            json.dumps(structured, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return structured

    except Exception:
        return {}
