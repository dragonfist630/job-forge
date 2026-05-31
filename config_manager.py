"""
Loads, saves, and validates the unified settings.yaml.
Provides resolved config dicts for downstream modules.
"""

import os
import shutil
import subprocess
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "settings.yaml"
REQUIRED_FIELDS = [
    ("linkedin", "email"),
    ("linkedin", "password"),
    ("identity", "first_name"),
    ("identity", "last_name"),
    ("api_keys", "claude"),
]


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_settings(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Merge into existing to preserve comments structure
    existing = load_settings()
    merged = _deep_merge(existing, data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def is_first_run() -> bool:
    s = load_settings()
    if not s:
        return True
    # Consider first run if any required field is empty
    for section, key in REQUIRED_FIELDS:
        if not s.get(section, {}).get(key, ""):
            return True
    return False


def validate_settings(data: dict) -> list[str]:
    errors = []
    for section, key in REQUIRED_FIELDS:
        val = data.get(section, {}).get(key, "")
        if not val:
            errors.append(f"{section}.{key} is required")
    return errors


def auto_detect_paths(settings: dict) -> dict:
    """Tries to find career-ops and linkedin-applier dirs automatically."""
    paths = settings.get("paths", {})

    # career-ops: look relative to this file, then common locations
    if not paths.get("career_ops"):
        candidates = [
            Path(__file__).parent.parent / "career-ops",
            Path.home() / "Desktop" / "career-ops",
        ]
        for c in candidates:
            if (c / "generate-pdf.mjs").exists():
                paths["career_ops"] = str(c)
                break

    # linkedin-applier
    if not paths.get("linkedin_applier"):
        candidates = [
            Path(__file__).parent.parent / "Auto_job_applier_linkedIn",
            Path.home() / "Desktop" / "Auto_job_applier_linkedIn",
        ]
        for c in candidates:
            if (c / "runAiBot.py").exists():
                paths["linkedin_applier"] = str(c)
                break

    # node binary
    if not paths.get("node_bin"):
        node = shutil.which("node")
        if node:
            paths["node_bin"] = node

    settings["paths"] = paths
    return settings


def get_career_ops_dir(settings: dict) -> Path:
    p = settings.get("paths", {}).get("career_ops", "")
    return Path(p) if p else None


def get_linkedin_applier_dir(settings: dict) -> Path:
    p = settings.get("paths", {}).get("linkedin_applier", "")
    return Path(p) if p else None


def get_node_bin(settings: dict) -> str:
    return settings.get("paths", {}).get("node_bin") or shutil.which("node") or "node"


def read_cv_md(settings: dict) -> str:
    """Returns cv.md content from the career-ops project."""
    career_ops = get_career_ops_dir(settings)
    if not career_ops:
        return ""
    cv_path = career_ops / "cv.md"
    if cv_path.exists():
        return cv_path.read_text(encoding="utf-8")
    return ""


def read_resume_template(settings: dict) -> str:
    """Returns the HTML resume template from career-ops."""
    career_ops = get_career_ops_dir(settings)
    if not career_ops:
        return ""
    tmpl = career_ops / "templates" / "cv-template.html"
    if tmpl.exists():
        return tmpl.read_text(encoding="utf-8")
    return ""


def check_health(settings: dict) -> dict:
    """Returns a dict of health checks for the setup wizard."""
    status = {}

    career_ops = get_career_ops_dir(settings)
    status["career_ops_found"] = career_ops and (career_ops / "generate-pdf.mjs").exists()
    status["cv_md_found"] = career_ops and (career_ops / "cv.md").exists()
    status["template_found"] = career_ops and (career_ops / "templates" / "cv-template.html").exists()

    applier = get_linkedin_applier_dir(settings)
    status["linkedin_applier_found"] = applier and (applier / "runAiBot.py").exists()

    node = get_node_bin(settings)
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        status["node_found"] = result.returncode == 0
        status["node_version"] = result.stdout.strip()
    except Exception:
        status["node_found"] = False
        status["node_version"] = ""

    try:
        import anthropic  # noqa: F401
        status["anthropic_sdk"] = True
    except ImportError:
        status["anthropic_sdk"] = False

    try:
        import selenium  # noqa: F401
        status["selenium"] = True
    except ImportError:
        status["selenium"] = False

    return status


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
