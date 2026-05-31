#!/usr/bin/env python3
"""
scraper_worker.py -- subprocess script, run by scraper.py.

Receives job-forge settings as JSON via sys.argv[1].
Patches LinkedIn applier config globals, runs scrape-only loop (no apply).
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
import types
import traceback
import glob
from urllib.parse import urlencode


def emit(obj: dict):
    print(json.dumps(obj), flush=True)


def emit_progress(msg: str):
    emit({"type": "progress", "msg": msg})


def emit_error(msg: str):
    emit({"type": "error", "msg": msg})


def load_visa_sponsors(linkedin_dir: str) -> set:
    """
    Load UK gov visa sponsor register from the linkedin applier directory.
    Finds the most recent *Worker_and_Temporary_Worker*.csv file.
    Returns a set of lowercased company names.
    """
    sponsors: set = set()
    pattern = os.path.join(linkedin_dir, "*Worker_and_Temporary_Worker*.csv")
    matches = sorted(glob.glob(pattern), reverse=True)  # most recent first
    if not matches:
        emit_progress("Visa sponsor CSV not found — skipping visa check")
        return sponsors
    csv_path = matches[0]
    emit_progress(f"Loading visa sponsors from {os.path.basename(csv_path)}...")
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("Organisation Name", "").strip().strip('"').lower()
                if name:
                    sponsors.add(name)
        emit_progress(f"Loaded {len(sponsors)} visa-sponsoring companies")
    except Exception as e:
        emit_progress(f"Warning: could not load visa CSV: {e}")
    return sponsors


_DATE_POSTED = {
    "Past 24 hours": "r86400",
    "Past week":     "r604800",
    "Past month":    "r2592000",
    "Any time":      None,
}
_SORT_BY = {
    "Most recent":   "DD",
    "Most relevant": "R",
}
_JOB_TYPE = {
    "Full-time":  "F",
    "Part-time":  "P",
    "Contract":   "C",
    "Temporary":  "T",
    "Volunteer":  "V",
    "Internship": "I",
    "Other":      "O",
}
_ON_SITE = {
    "On-site": "1",
    "Remote":  "2",
    "Hybrid":  "3",
}
_EXP_LEVEL = {
    "Internship":      "1",
    "Entry level":     "2",
    "Associate":       "3",
    "Mid-Senior level":"4",
    "Director":        "5",
    "Executive":       "6",
}


def build_search_url(term: str, settings: dict) -> str:
    """Build a LinkedIn job search URL with all filters encoded as URL params.
    This is more reliable than clicking through the 'All filters' modal UI."""
    job_search = settings.get("job_search", {})
    filters = job_search.get("filters", {})

    params = {"keywords": term}

    location = job_search.get("search_location", "").strip()
    if location:
        params["location"] = location

    date_posted = filters.get("date_posted") or "Past 24 hours"
    tpr = _DATE_POSTED.get(date_posted)
    if tpr:
        params["f_TPR"] = tpr

    sort_by = filters.get("sort_by", "Most relevant")
    params["sortBy"] = _SORT_BY.get(sort_by, "R")

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


def is_visa_sponsor(company: str, sponsors: set) -> bool:
    """Fuzzy check — exact match or either name contains the other."""
    if not sponsors:
        return False
    company_lower = company.strip().lower()
    if company_lower in sponsors:
        return True
    for name in sponsors:
        if company_lower in name or name in company_lower:
            return True
    return False


def build_mock_modules(settings: dict):
    """Inject fake config modules into sys.modules before any applier import."""

    li = settings.get("linkedin", {})
    identity = settings.get("identity", {})
    loc = settings.get("location", {})
    eqo = settings.get("equal_opportunity", {})
    exp = settings.get("experience", {})
    comp = settings.get("compensation", {})
    np_days = settings.get("notice_period", {}).get("days", 30)
    online = settings.get("online_presence", {})
    work_auth = settings.get("work_authorization", {})
    job_search = settings.get("job_search", {})
    filters = job_search.get("filters", {})
    screening = settings.get("screening", {})
    app_cfg = settings.get("app", {})

    desired_salary = comp.get("desired_salary", 80000)
    current_ctc_val = comp.get("current_ctc", 0)
    current_experience = exp.get("current_experience", 5)
    did_masters = exp.get("did_masters", False)

    # --- config package stub ---
    config_pkg = types.ModuleType("config")
    sys.modules["config"] = config_pkg

    # --- config.personals ---
    p = types.ModuleType("config.personals")
    p.first_name = identity.get("first_name", "")
    p.middle_name = identity.get("middle_name", "")
    p.last_name = identity.get("last_name", "")
    p.phone_number = str(identity.get("phone_number", ""))
    p.current_city = loc.get("city", "")
    p.street = loc.get("street", "")
    p.state = loc.get("state", "")
    p.zipcode = str(loc.get("zipcode", ""))
    p.country = loc.get("country", "United States")
    p.ethnicity = eqo.get("ethnicity", "Decline")
    p.gender = eqo.get("gender", "Decline")
    p.disability_status = eqo.get("disability_status", "Decline")
    p.veteran_status = eqo.get("veteran_status", "Decline")
    sys.modules["config.personals"] = p
    config_pkg.personals = p

    # --- config.questions ---
    q = types.ModuleType("config.questions")
    q.default_resume_path = "all resumes/resume.pdf"
    q.years_of_experience = str(exp.get("years_of_experience", "3"))
    q.require_visa = "Yes" if work_auth.get("require_visa_sponsorship") else "No"
    q.website = online.get("portfolio", "")
    q.linkedIn = online.get("linkedin", "")
    q.us_citizenship = work_auth.get("us_citizenship", "")
    q.desired_salary = desired_salary
    q.current_ctc = current_ctc_val
    q.notice_period = np_days
    q.current_experience = current_experience
    q.did_masters = did_masters
    q.confidence_level = str(exp.get("confidence_level", "8"))
    q.recent_employer = exp.get("recent_employer", "")
    q.linkedin_headline = settings.get("professional_summary", {}).get("headline", "")
    q.linkedin_summary = settings.get("professional_summary", {}).get("summary", "")
    q.cover_letter = ""
    q.pause_before_submit = False
    q.pause_at_failed_question = False
    q.overwrite_previous_answers = False
    sys.modules["config.questions"] = q
    config_pkg.questions = q

    # --- config.search ---
    s = types.ModuleType("config.search")
    s.search_terms = job_search.get("search_terms", [])
    s.search_location = job_search.get("search_location", "")
    s.switch_number = job_search.get("switch_number", 30)
    s.randomize_search_order = False
    s.sort_by = filters.get("sort_by", "Most recent")
    s.date_posted = filters.get("date_posted") or "Past 24 hours"
    s.salary = filters.get("salary", "")
    s.easy_apply_only = filters.get("easy_apply_only", True)
    s.experience_level = filters.get("experience_level", [])
    s.job_type = filters.get("job_type", [])
    s.on_site = filters.get("on_site", [])
    s.companies = []
    s.location = []
    s.industry = []
    s.job_function = []
    s.job_titles = []
    s.benefits = []
    s.commitments = []
    s.under_10_applicants = False
    s.in_your_network = False
    s.fair_chance_employer = False
    s.pause_after_filters = False
    s.about_company_bad_words = []
    s.about_company_good_words = []
    s.bad_words = screening.get("bad_words", [])
    s.security_clearance = screening.get("security_clearance", False)
    s.did_masters = did_masters
    s.current_experience = current_experience
    sys.modules["config.search"] = s
    config_pkg.search = s

    # --- config.secrets ---
    sec = types.ModuleType("config.secrets")
    sec.username = li.get("email", "")
    sec.password = li.get("password", "")
    sec.use_AI = False
    sec.ai_provider = "openai"
    sys.modules["config.secrets"] = sec
    config_pkg.secrets = sec

    # --- config.settings ---
    cs = types.ModuleType("config.settings")
    cs.close_tabs = False
    cs.follow_companies = False
    cs.run_non_stop = False
    cs.alternate_sortby = False
    cs.cycle_date_posted = False
    cs.stop_date_cycle_at_24hr = False
    cs.generated_resume_path = "all resumes"
    cs.file_name = "all excels/all_applied_applications_history.csv"
    cs.failed_file_name = "all excels/all_failed_applications_history.csv"
    cs.logs_folder_path = "logs/"
    cs.click_gap = app_cfg.get("click_gap", 1)
    cs.run_in_background = app_cfg.get("run_in_background", False)
    cs.disable_extensions = False
    cs.safe_mode = True
    cs.smooth_scroll = False
    cs.keep_screen_awake = False
    cs.stealth_mode = False
    cs.showAiErrorAlerts = False
    cs.pause_before_submit = False
    cs.pause_at_failed_question = False
    sys.modules["config.settings"] = cs
    config_pkg.settings = cs


def suppress_pyautogui():
    """Mock pyautogui so no GUI dialogs block the headless worker."""
    try:
        import pyautogui
        pyautogui.alert = lambda *a, **kw: None
        pyautogui.confirm = lambda *a, **kw: "Look's good, Continue"
        pyautogui.press = lambda *a, **kw: None
    except Exception:
        pass


def main():
    if len(sys.argv) < 2:
        emit_error("No settings JSON provided as argv[1]")
        sys.exit(1)

    try:
        settings = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        emit_error(f"Invalid settings JSON: {e}")
        sys.exit(1)

    linkedin_dir = settings.get("paths", {}).get("linkedin_applier", "")
    if not linkedin_dir or not os.path.isdir(linkedin_dir):
        emit_error(f"LinkedIn applier directory not found: {linkedin_dir}")
        sys.exit(1)

    # Change to linkedin dir — all relative file paths inside it depend on this
    os.chdir(linkedin_dir)
    sys.path.insert(0, linkedin_dir)

    # Inject mock config modules before any applier import
    build_mock_modules(settings)
    suppress_pyautogui()

    emit_progress("Launching browser...")

    try:
        import modules.open_chrome as oc
        driver = oc.driver
        wait = oc.wait
        actions = oc.actions
    except Exception as e:
        emit_error(f"Browser launch failed: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    try:
        import runAiBot as bot
    except Exception as e:
        emit_error(f"Failed to load LinkedIn applier: {e}\n{traceback.format_exc()}")
        try:
            driver.quit()
        except Exception:
            pass
        sys.exit(1)

    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    import time

    job_search = settings.get("job_search", {})
    search_terms = job_search.get("search_terms", [])
    switch_number = job_search.get("switch_number", 30)

    # Load visa sponsor register
    visa_sponsors = load_visa_sponsors(linkedin_dir)

    try:
        emit_progress("Logging into LinkedIn...")
        bot.login_LN()

        jobs_collected = []
        rejected_jobs: set = set()
        blacklisted_companies: set = set()

        for term in search_terms:
            url = build_search_url(term, settings)
            emit_progress(f'Searching: "{term}" (filters encoded in URL)')
            driver.get(url)
            time.sleep(3)  # let filtered results load

            current_count = 0

            while current_count < switch_number:
                try:
                    wait.until(
                        EC.presence_of_all_elements_located(
                            (By.XPATH, "//li[@data-occludable-job-id]")
                        )
                    )
                except Exception:
                    break

                pagination_element, current_page = bot.get_page_info()
                time.sleep(2)

                job_listings = driver.find_elements(
                    By.XPATH, "//li[@data-occludable-job-id]"
                )
                emit_progress(
                    f'Page {current_page} — found {len(job_listings)} listings'
                )

                for job in job_listings:
                    if current_count >= switch_number:
                        break

                    job_id, title, company, work_location, work_style, skip = (
                        bot.get_job_main_details(job, blacklisted_companies, rejected_jobs)
                    )

                    if skip:
                        continue

                    # Skip if already collected this job_id
                    if any(j["job_id"] == job_id for j in jobs_collected):
                        continue

                    job_link = f"https://www.linkedin.com/jobs/view/{job_id}"

                    try:
                        rejected_jobs, blacklisted_companies, _ = bot.check_blacklist(
                            rejected_jobs, job_id, company, blacklisted_companies
                        )
                    except ValueError as e:
                        emit_progress(f"Skipped {company} (blacklisted): {e}")
                        continue
                    except Exception:
                        pass

                    description, experience_required, skip, reason, _ = (
                        bot.get_job_description()
                    )

                    if skip:
                        emit_progress(f'Skipped "{title}" at {company}: {reason}')
                        continue

                    record = {
                        "type": "job",
                        "job_id": job_id,
                        "title": title,
                        "company": company,
                        "work_location": work_location,
                        "work_style": work_style,
                        "job_link": job_link,
                        "job_description": description,
                        "experience_required": str(experience_required),
                        "search_term": term,
                        "visa_sponsored": is_visa_sponsor(company, visa_sponsors),
                    }
                    emit(record)
                    jobs_collected.append(record)
                    current_count += 1

                # Try next page
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
