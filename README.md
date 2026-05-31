# JobForge

JobForge is a local web app for job searching on LinkedIn, reviewing listings, generating tailored resumes with Claude, and (eventually) automating Easy Apply submissions.

Everything runs on your machine at **http://localhost:7070** — no cloud hosting required.

## Features

- **Setup wizard** — configure API keys, LinkedIn credentials, identity, job search filters, and paths in one place
- **Job scanner** — scrape LinkedIn job listings based on your search terms and filters
- **Job review** — browse results, approve jobs you want to pursue
- **Resume generation** — tailor your CV to each approved job using Claude and export PDFs via [career-ops](https://github.com/dragonfist630/career-ops) (optional integration)
- **Apply queue** — see approved jobs with generated resumes ready to apply (automation in progress)

## Requirements

- **Python 3.11+**
- **Node.js** (for PDF generation when using career-ops)
- **Chrome / Chromium** (for LinkedIn scraping via Selenium)
- **Anthropic API key** (Claude) for resume tailoring
- **LinkedIn account** for job scanning

### Optional integrations

| Integration | Purpose |
|---|---|
| [career-ops](https://github.com/dragonfist630/career-ops) | Tailored resume PDF generation |
| Auto_job_applier_linkedIn | LinkedIn Easy Apply automation (Phase 5) |

JobForge auto-detects these folders on your Desktop if present.

## Quick start

### macOS

1. Double-click **`JobForge.command`** (first time: right-click → Open → Open)
2. The installer sets up dependencies and opens your browser

Or manually:

```bash
bash setup/install_mac.sh
bash start.sh
```

### Windows

1. Double-click **`JobForge.bat`**
2. Follow the on-screen setup, then the app opens in your browser

Or manually:

```bash
setup\install_windows.bat
start.bat
```

### Manual setup

```bash
pip install -r requirements.txt
cp config/settings.yaml.example config/settings.yaml
# Edit config/settings.yaml with your details, or use the web setup wizard
python main.py
```

On first launch, JobForge redirects you to the setup wizard at `/setup`.

## Configuration

Settings live in `config/settings.yaml`. This file is **gitignored** — it contains secrets and personal data.

Copy the example to get started:

```bash
cp config/settings.yaml.example config/settings.yaml
```

Key sections:

- `api_keys` — Anthropic (Claude) API key
- `linkedin` — email and password for scraping
- `identity` / `location` / `experience` — used in applications and resume generation
- `job_search` — search terms, location, filters (Easy Apply, job type, etc.)
- `paths` — locations of career-ops and the LinkedIn applier (auto-detected if on Desktop)
- `app` — port (default `7070`), browser auto-open, apply pacing

You can also edit everything through the web UI after starting the app.

## Project structure

```
job-forge/
├── main.py              # Flask entry point
├── config_manager.py    # Load/save settings.yaml
├── scraper.py           # Job scan orchestration
├── scraper_worker.py    # LinkedIn Selenium worker
├── resume_generator.py  # Claude + PDF pipeline
├── applier.py           # Easy Apply bridge (in progress)
├── ui/                  # Flask templates, CSS, JS
├── config/              # settings.yaml (local, not committed)
├── data/                # jobs, approvals, generated resumes (local)
└── setup/               # macOS / Windows installers
```

## Workflow

1. **Setup** — fill in credentials and job search preferences
2. **Scan** — run a LinkedIn job search from the dashboard
3. **Review** — approve jobs worth applying to
4. **Generate** — create tailored PDF resumes for approved listings
5. **Apply** — submit applications (automation coming in Phase 5)

## Security

Never commit `config/settings.yaml`. It holds API keys, LinkedIn credentials, and personal information. Use `config/settings.yaml.example` as a template only.

Scraped job data in `data/` is also kept local and not pushed to git.

## License

MIT
