"""
scorer.py -- Zero-token heuristic job scoring.

Score 0-10 computed from:
  Title match vs target_roles     → 0-3 pts
  Skill keyword overlap (JD/cv.md) → 0-4 pts
  Work style match                 → 0-2 pts
  Location match                   → 0-1 pt
  Bad word penalty                 → -2 pts
"""

import re
from pathlib import Path

import config_manager

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "will",
    "your", "their", "our", "they", "them", "are", "you", "not", "but",
    "all", "can", "been", "more", "also", "about", "into", "such", "other",
    "we're", "we'll", "don't", "its", "per", "via", "new", "use", "used",
    "work", "work", "role", "team", "join", "help", "build", "strong",
    "experience", "skills", "ability", "proven", "across", "within",
    "including", "ensure", "manage", "looking", "based", "highly",
}


def _tokenize(text: str) -> set[str]:
    """Lower-case alphabetic tokens, min 4 chars, no stopwords."""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _title_score(job_title: str, target_roles: str) -> int:
    """0-3: how well job title matches user's target roles."""
    if not target_roles or not job_title:
        return 0
    title_lower = job_title.lower()
    role_lines = [r.strip().lower() for r in target_roles.splitlines() if r.strip()]
    best = 0
    for role in role_lines:
        role_words = set(re.findall(r"[a-zA-Z]{3,}", role))
        title_words = set(re.findall(r"[a-zA-Z]{3,}", title_lower))
        if not role_words:
            continue
        overlap = role_words & title_words
        ratio = len(overlap) / len(role_words)
        if ratio == 1.0:
            best = max(best, 3)
        elif ratio >= 0.5:
            best = max(best, 2)
        elif overlap:
            best = max(best, 1)
    return best


def _keyword_score(jd_text: str, cv_text: str) -> int:
    """0-4: how many JD skill keywords appear in cv.md."""
    if not jd_text or not cv_text:
        return 0
    jd_tokens = _tokenize(jd_text[:3000])
    cv_tokens = _tokenize(cv_text)
    overlap = len(jd_tokens & cv_tokens)
    if overlap >= 12:
        return 4
    if overlap >= 8:
        return 3
    if overlap >= 4:
        return 2
    if overlap >= 2:
        return 1
    return 0


def _work_style_score(job_work_style: str, user_preferences: list[str]) -> int:
    """0-2: work style alignment."""
    if not user_preferences:
        return 1  # no preference = neutral
    style = (job_work_style or "").lower()
    if not style or style in ("unknown", "t", "n/a", ""):
        return 1  # unknown = neutral
    prefs_lower = [p.lower() for p in user_preferences]
    for pref in prefs_lower:
        if pref in style or style in pref:
            return 2
    return 0


def _location_score(job_work_location: str, user_city: str, user_country: str) -> int:
    """0-1: location match."""
    loc = (job_work_location or "").lower()
    if not loc or loc in ("t", "unknown", "remote", ""):
        return 0
    city = (user_city or "").lower()
    country = (user_country or "").lower()
    if (city and city in loc) or (country and country in loc):
        return 1
    return 0


def _bad_word_penalty(jd_text: str, bad_words: list[str]) -> int:
    """0 or -2: bad word hit in JD."""
    if not bad_words or not jd_text:
        return 0
    jd_lower = jd_text.lower()
    for word in bad_words:
        if word.strip().lower() in jd_lower:
            return -2
    return 0


def score_job(job: dict, settings: dict) -> int:
    """
    Compute heuristic score 0-10 for a job.
    Reads cv.md from internal path — no LLM, no API call.
    """
    jd_text = job.get("job_description", "")
    job_title = job.get("title", "")
    work_style = job.get("work_style", "")
    work_location = job.get("work_location", "")

    profile = settings.get("profile", {})
    target_roles = profile.get("target_roles", "")
    work_preference = profile.get("work_preference", [])

    location = settings.get("location", {})
    user_city = location.get("city", "")
    user_country = location.get("country", "")

    screening = settings.get("screening", {})
    bad_words = screening.get("bad_words", [])

    cv_text = ""
    if config_manager.INTERNAL_CV_PATH.exists():
        cv_text = config_manager.INTERNAL_CV_PATH.read_text(encoding="utf-8")

    pts = (
        _title_score(job_title, target_roles)
        + _keyword_score(jd_text, cv_text)
        + _work_style_score(work_style, work_preference)
        + _location_score(work_location, user_city, user_country)
        + _bad_word_penalty(jd_text, bad_words)
    )
    return max(0, min(10, pts))
