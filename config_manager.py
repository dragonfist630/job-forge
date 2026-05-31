"""
Loads, saves, and validates the unified settings.yaml.
Provides resolved config dicts for downstream modules.

job-forge is self-contained: CV, mode files, template, and PDF script
all live inside this project directory. No external career-ops needed.
"""

import shutil
import subprocess
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "settings.yaml"

# Internal paths — everything lives inside job-forge
_ROOT = Path(__file__).resolve().parent
INTERNAL_CV_PATH      = _ROOT / "cv.md"
INTERNAL_MODES_DIR    = _ROOT / "modes"
INTERNAL_TEMPLATES_DIR = _ROOT / "templates"
INTERNAL_PDF_SCRIPT   = _ROOT / "generate-pdf.mjs"

REQUIRED_FIELDS = [
    ("linkedin", "email"),
    ("linkedin", "password"),
    ("identity", "first_name"),
    ("identity", "last_name"),
]


def load_settings() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_settings(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = load_settings()
    merged = _deep_merge(existing, data)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(merged, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def is_first_run() -> bool:
    s = load_settings()
    if not s:
        return True
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
    """Detect linkedin-applier dir and node binary. career-ops is now internal."""
    paths = settings.get("paths", {})

    if not paths.get("linkedin_applier"):
        candidates = [
            _ROOT.parent / "Auto_job_applier_linkedIn",
            Path.home() / "Desktop" / "Auto_job_applier_linkedIn",
        ]
        for c in candidates:
            if (c / "runAiBot.py").exists():
                paths["linkedin_applier"] = str(c)
                break

    if not paths.get("node_bin"):
        node = shutil.which("node")
        if node:
            paths["node_bin"] = node

    settings["paths"] = paths
    return settings


def get_career_ops_dir(settings: dict) -> Path:
    """Always returns the internal job-forge root (self-contained)."""
    return _ROOT


def get_linkedin_applier_dir(settings: dict) -> Path:
    p = settings.get("paths", {}).get("linkedin_applier", "")
    return Path(p) if p else None


def get_node_bin(settings: dict) -> str:
    return settings.get("paths", {}).get("node_bin") or shutil.which("node") or "node"


def read_cv_md(settings: dict) -> str:
    if INTERNAL_CV_PATH.exists():
        return INTERNAL_CV_PATH.read_text(encoding="utf-8")
    return ""


def read_resume_template(settings: dict) -> str:
    tmpl = INTERNAL_TEMPLATES_DIR / "cv-template.html"
    if tmpl.exists():
        return tmpl.read_text(encoding="utf-8")
    return ""


def check_health(settings: dict) -> dict:
    status = {}

    status["career_ops_found"] = INTERNAL_PDF_SCRIPT.exists()
    status["cv_md_found"] = INTERNAL_CV_PATH.exists()
    status["template_found"] = (INTERNAL_TEMPLATES_DIR / "cv-template.html").exists()

    applier = get_linkedin_applier_dir(settings)
    status["linkedin_applier_found"] = bool(applier and (applier / "runAiBot.py").exists())

    node = get_node_bin(settings)
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        status["node_found"] = result.returncode == 0
        status["node_version"] = result.stdout.strip()
    except Exception:
        status["node_found"] = False
        status["node_version"] = ""

    claude_bin = shutil.which("claude")
    status["claude_cli"] = bool(claude_bin)

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
