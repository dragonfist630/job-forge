#!/usr/bin/env python3
"""
scraper_worker.py -- standalone LinkedIn job scraper.
No external project dependency. Uses Selenium + webdriver_manager directly.

Receives job-forge settings as JSON via sys.argv[1].
Outputs JSON lines to stdout:
  {"type": "progress", "msg": "..."}
  {"type": "job",      "job_id": "...", "title": "...", ...}
  {"type": "done",     "count": N}
  {"type": "error",    "msg": "..."}
"""

import sys
import os
import csv
import json
import re
import time
import glob
import traceback
from urllib.parse import urlencode


# ── Emit helpers ──────────────────────────────────────────────────────────────
def emit(obj: dict):
    print(json.dumps(obj), flush=True)

def emit_progress(msg: str):
    emit({"type": "progress", "msg": msg})

def emit_error(msg: str):
    emit({"type": "error", "msg": msg})


# ── Visa sponsor CSV (optional) ───────────────────────────────────────────────
def load_visa_sponsors() -> set:
    sponsors: set = set()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for base in [script_dir, os.path.join(script_dir, "data")]:
        pattern = os.path.join(base, "*Worker_and_Temporary_Worker*.csv")
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            try:
                with open(matches[0], "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        name = row.get("Organisation Name", "").strip().strip('"').lower()
                        if name:
                            sponsors.add(name)
                emit_progress(f"Loaded {len(sponsors)} visa-sponsoring companies")
                return sponsors
            except Exception:
                pass
    emit_progress("Visa sponsor CSV not found — skipping visa check")
    return sponsors


def is_visa_sponsor(company: str, sponsors: set) -> bool:
    if not sponsors:
        return False
    c = company.strip().lower()
    return c in sponsors or any(c in s or s in c for s in sponsors)


# ── LinkedIn search URL builder ───────────────────────────────────────────────
_DATE_POSTED = {
    "Past 24 hours": "r86400",
    "Past week":     "r604800",
    "Past month":    "r2592000",
    "Any time":      None,
}
_SORT_BY    = {"Most recent": "DD", "Most relevant": "R"}
_JOB_TYPE   = {"Full-time": "F", "Part-time": "P", "Contract": "C",
               "Temporary": "T", "Volunteer": "V", "Internship": "I", "Other": "O"}
_ON_SITE    = {"On-site": "1", "Remote": "2", "Hybrid": "3"}
_EXP_LEVEL  = {"Internship": "1", "Entry level": "2", "Associate": "3",
               "Mid-Senior level": "4", "Director": "5", "Executive": "6"}


def build_search_url(term: str, settings: dict) -> str:
    job_search = settings.get("job_search", {})
    filters    = job_search.get("filters", {})
    params     = {"keywords": term}

    location = job_search.get("search_location", "").strip()
    if location:
        params["location"] = location

    tpr = _DATE_POSTED.get(filters.get("date_posted") or "Past 24 hours")
    if tpr:
        params["f_TPR"] = tpr

    params["sortBy"] = _SORT_BY.get(filters.get("sort_by", "Most relevant"), "R")

    job_types = [_JOB_TYPE[t] for t in filters.get("job_type", []) if t in _JOB_TYPE]
    if job_types:
        params["f_JT"] = ",".join(job_types)

    on_site = [_ON_SITE[s] for s in filters.get("on_site", []) if s in _ON_SITE]
    if on_site:
        params["f_WT"] = ",".join(on_site)

    exp_levels = [_EXP_LEVEL[e] for e in filters.get("experience_level", []) if e in _EXP_LEVEL]
    if exp_levels:
        params["f_E"] = ",".join(exp_levels)

    if filters.get("easy_apply_only"):
        params["f_LF"] = "f_AL"

    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)


# ── Browser setup ─────────────────────────────────────────────────────────────
def _cdp_chrome_version(host: str = "host.docker.internal", port: int = 9222) -> str:
    """Return Chrome browser string from CDP endpoint, e.g. 'Chrome/148.0.7778.178'."""
    import urllib.request
    import json as _j
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=5) as r:
            return _j.loads(r.read()).get("Browser", "")
    except Exception:
        return ""


def setup_driver(run_in_background: bool = False, profile_dir: str = None):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    # Docker mode: attach to host Chrome launched by JobForge.bat/.command
    # with --remote-debugging-port=9222. No new Chrome launched.
    if os.environ.get("JOBFORGE_DOCKER"):
        browser_str = _cdp_chrome_version()
        if not browser_str:
            raise RuntimeError(
                "Cannot reach Chrome at host.docker.internal:9222. "
                "Make sure you launched JobForge via JobForge.command "
                "(not 'python main.py' directly) — the launcher starts Chrome "
                "with the required remote-debugging port."
            )
        try:
            chrome_ver = browser_str.split("/")[1]  # "148.0.7778.178"
        except Exception:
            chrome_ver = None

        options = Options()
        options.debugger_address = "host.docker.internal:9222"
        try:
            mgr_path = (
                ChromeDriverManager(driver_version=chrome_ver).install()
                if chrome_ver
                else ChromeDriverManager().install()
            )
        except Exception:
            mgr_path = ChromeDriverManager().install()
        driver = webdriver.Chrome(service=Service(mgr_path), options=options)
        return driver, WebDriverWait(driver, 15)

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    options.add_argument("--disable-notifications")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    )
    if profile_dir:
        options.add_argument(f"--user-data-dir={profile_dir}")
    ext_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extensions", "uk_visa_sponsor.crx")
    if os.path.exists(ext_path):
        options.add_extension(ext_path)
    if run_in_background:
        options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options,
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver, WebDriverWait(driver, 15)


# ── LinkedIn login ────────────────────────────────────────────────────────────
def _is_logged_in(driver) -> bool:
    url = driver.current_url
    return "linkedin.com/feed" in url or "linkedin.com/jobs" in url or "linkedin.com/in/" in url


def login_linkedin(driver, wait, email: str, password: str):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    # Check if already logged in via saved profile
    driver.get("https://www.linkedin.com/feed/")
    time.sleep(3)
    if _is_logged_in(driver):
        return

    driver.get("https://www.linkedin.com/login")
    wait.until(EC.presence_of_element_located((By.ID, "username")))

    # Dismiss cookie/GDPR banner if present
    try:
        consent_btn = driver.find_element(
            By.XPATH, "//button[@action-type='DENY' or @action-type='ACCEPT']"
        )
        consent_btn.click()
        time.sleep(1)
    except Exception:
        pass

    driver.find_element(By.ID, "username").clear()
    driver.find_element(By.ID, "username").send_keys(email)
    driver.find_element(By.ID, "password").clear()
    driver.find_element(By.ID, "password").send_keys(password)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    time.sleep(5)

    url = driver.current_url
    if "/login" in url or "/checkpoint" in url or "/authwall" in url or "/uas/" in url:
        raise RuntimeError(
            "LinkedIn login failed. Check your email/password in Settings, "
            "or complete the security challenge in the browser window first."
        )


# ── Job card parsing ──────────────────────────────────────────────────────────
def _text(el, css):
    try:
        return el.find_element("css selector", css).text.strip()
    except Exception:
        return ""


def parse_job_card(job_el) -> dict:
    """Extract metadata visible on the job card without clicking."""
    job_id = job_el.get_attribute("data-occludable-job-id") or ""

    title = ""
    for sel in [
        ".job-card-list__title--link",
        ".job-card-container__link",
        ".job-card-list__title",
    ]:
        title = _text(job_el, sel)
        if title:
            break

    company = _text(job_el, ".job-card-container__primary-description")
    if not company:
        company = _text(job_el, ".artdeco-entity-lockup__subtitle")

    work_location = ""
    work_style    = ""
    try:
        for item in job_el.find_elements("css selector", ".job-card-container__metadata-item"):
            txt = item.text.strip()
            if not txt:
                continue
            lower = txt.lower()
            if any(s in lower for s in ("remote", "hybrid", "on-site", "onsite")):
                work_style = txt
            elif not work_location:
                work_location = txt
    except Exception:
        pass

    return {"job_id": job_id, "title": title, "company": company,
            "work_location": work_location, "work_style": work_style}


# ── Job description ───────────────────────────────────────────────────────────
def fetch_description(driver, wait, job_id: str) -> tuple:
    """Click the job card to load description panel. Returns (text, exp_years)."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    description = ""
    experience  = "Unknown"

    try:
        job_el = driver.find_element(
            By.XPATH, f"//li[@data-occludable-job-id='{job_id}']"
        )
        driver.execute_script("arguments[0].click();", job_el)
        time.sleep(2)

        for sel in [
            ".jobs-description-content__text",
            ".jobs-description__content",
            ".jobs-box__html-content",
        ]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                txt = el.text.strip()
                if len(txt) > 80:
                    description = txt
                    break
            except Exception:
                continue

        if description:
            m = re.search(
                r"(\d+)\+?\s*(?:to\s*\d+\s*)?years?\s*(?:of\s*)?(?:experience|exp)",
                description, re.IGNORECASE
            )
            if m:
                experience = m.group(1)

    except Exception:
        pass

    return description, experience


# ── Bad-word filter ───────────────────────────────────────────────────────────
def has_bad_word(text: str, bad_words: list) -> bool:
    low = text.lower()
    return any(w.strip().lower() in low for w in bad_words if w.strip())


# ── Chrome profile selection ──────────────────────────────────────────────────
def _pick_profile_dir() -> str:
    """
    Prefer the auto_job_applier profile if it exists — it already has a LinkedIn
    session. Fall back to job-forge's own chrome-profile (empty on first run).
    """
    import pathlib
    home = pathlib.Path.home()

    candidates = []
    if sys.platform == "darwin":
        candidates = [
            home / "Library" / "Application Support" / "Google" / "Chrome" / "auto-job-apply-profile",
        ]
    elif sys.platform.startswith("win"):
        import os as _os
        local = _os.environ.get("LOCALAPPDATA", "")
        if local:
            candidates = [
                pathlib.Path(local) / "Google" / "Chrome" / "User Data" / "auto-job-apply-profile",
            ]

    for c in candidates:
        if c.exists():
            return str(c)

    # Fall back to our own profile dir (user must log in once manually)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.join(script_dir, "chrome-profile")
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        emit_error("No settings JSON provided")
        sys.exit(1)

    try:
        settings = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        emit_error(f"Invalid settings JSON: {e}")
        sys.exit(1)

    linkedin       = settings.get("linkedin", {})
    email          = linkedin.get("email", "")
    password       = linkedin.get("password", "")
    job_search     = settings.get("job_search", {})
    search_terms   = job_search.get("search_terms", [])
    switch_number  = job_search.get("switch_number", 30)
    bad_words      = settings.get("screening", {}).get("bad_words", [])
    run_bg         = settings.get("app", {}).get("run_in_background", False)

    if not email or not password:
        emit_error(
            "LinkedIn email/password not set. "
            "Go to Settings → LinkedIn Credentials and fill them in."
        )
        sys.exit(1)

    if not search_terms:
        emit_error(
            "No search terms configured. "
            "Go to Settings → Job Search and add job titles to search."
        )
        sys.exit(1)

    visa_sponsors = load_visa_sponsors()

    profile_dir = _pick_profile_dir()
    emit_progress(f"Using Chrome profile: {profile_dir}")

    emit_progress("Launching browser (downloading ChromeDriver if needed)...")
    try:
        driver, wait = setup_driver(run_bg, profile_dir)
    except Exception as e:
        emit_error(
            f"Browser launch failed: {e}\n"
            "Make sure Google Chrome is installed on this machine."
        )
        sys.exit(1)

    from selenium.webdriver.common.by import By

    jobs_collected: list = []
    seen_ids: set = set()

    try:
        emit_progress("Logging into LinkedIn...")
        try:
            login_linkedin(driver, wait, email, password)
            emit_progress("Login successful")
        except RuntimeError as e:
            emit_error(str(e))
            driver.quit()
            sys.exit(1)

        for term in search_terms:
            url = build_search_url(term, settings)
            emit_progress(f'Searching: "{term}"')
            driver.get(url)
            time.sleep(3)

            # Detect session expiry / bot wall redirect
            cur = driver.current_url
            if "/login" in cur or "/authwall" in cur or "/checkpoint" in cur:
                emit_error(
                    "LinkedIn redirected to login during scraping. "
                    "Your session may have expired. Try: (1) run with Chrome visible "
                    "(disable 'Run in background'), (2) restart the scan to log in again."
                )
                break

            current_count = 0

            while current_count < switch_number:
                try:
                    job_els = driver.find_elements(
                        By.XPATH, "//li[@data-occludable-job-id]"
                    )
                except Exception:
                    break

                if not job_els:
                    emit_progress(f'No job listings found for "{term}" — LinkedIn may have changed its layout or session expired')
                    break

                emit_progress(f'Page — {len(job_els)} listings visible')

                for job_el in job_els:
                    if current_count >= switch_number:
                        break

                    card = parse_job_card(job_el)
                    job_id  = card["job_id"]
                    title   = card["title"]
                    company = card["company"]

                    if not job_id or job_id in seen_ids or not title:
                        continue

                    if has_bad_word(title + " " + company, bad_words):
                        emit_progress(f'Skipped "{title}" (bad word in title/company)')
                        continue

                    description, experience = fetch_description(driver, wait, job_id)

                    if has_bad_word(description, bad_words):
                        emit_progress(f'Skipped "{title}" (bad word in description)')
                        continue

                    seen_ids.add(job_id)
                    record = {
                        "type":                "job",
                        "job_id":              job_id,
                        "title":               title,
                        "company":             company,
                        "work_location":       card["work_location"],
                        "work_style":          card["work_style"],
                        "job_link":            f"https://www.linkedin.com/jobs/view/{job_id}",
                        "job_description":     description,
                        "experience_required": experience,
                        "search_term":         term,
                        "visa_sponsored":      is_visa_sponsor(company, visa_sponsors),
                    }
                    emit(record)
                    jobs_collected.append(record)
                    current_count += 1

                # Next page
                try:
                    next_btn = driver.find_element(
                        By.XPATH, "//button[@aria-label='View next page']"
                    )
                    next_btn.click()
                    time.sleep(3)
                except Exception:
                    break

        emit({"type": "done", "count": len(jobs_collected)})

    except Exception as e:
        emit_error(f"Scrape error: {e}\n{traceback.format_exc()}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
