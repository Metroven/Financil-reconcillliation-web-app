# Tally — Financial Reconciliation Tool

Match two sets of financial records and instantly see what doesn't agree.
Upload a bank statement and a ledger export, and Tally tells you exactly which rows match, which don't, and what the net difference is.

## Features

- **Drag-and-drop upload** — CSV and Excel (.xlsx) files up to 10 MB
- **Smart column detection** — auto-guesses which column is the amount, date, reference, and description
- **Flexible matching** — exact, date-range, and fuzzy description matching
- **Clear results** — plain-English verdict, colored discrepancy highlights, and a downloadable CSV report
- **Multi-user** — each account has its own private files and history
- **Dark mode** — follows your system preference

## Tech stack

- **Backend:** Python 3.12 + Flask
- **Database:** SQLite (file-based, no external DB needed)
- **Server:** Gunicorn
- **Deployment:** Docker / Railway

---

## Running locally

```bash
# 1. Clone and enter the project
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO

# 2. Create a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the dev server
python app.py
```

Open **http://127.0.0.1:5000** in your browser.

---

## Deploying to Railway

See the [Railway deployment guide](DEPLOY.md) for full step-by-step instructions.

**Quick summary:**
1. Push this repo to GitHub
2. Create a new Railway project → Deploy from GitHub
3. Add a persistent **Volume** mounted at `/data` (keeps the database across deploys)
4. Set `SECRET_KEY` in Railway Variables
5. Railway auto-deploys on every `git push`

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Flask session key — set a fixed value in production |
| `ALLOW_REGISTRATION` | `1` | Set to `0` to close public sign-ups |
| `COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |
| `TRUST_PROXY` | `0` | Set to `1` when behind a reverse proxy (Railway, nginx) |
| `RECON_DATA_DIR` | `./data` | Where to store the DB and uploads |
| `PORT` | `5000` | Port to listen on (Railway sets this automatically) |

---

## Project structure

```
reconcile_app/
├── app.py               # Flask routes and auth
├── reconcile.py         # Matching engine (no Flask dependency)
├── requirements.txt
├── Dockerfile
├── railway.toml         # Railway deployment config
├── static/
│   └── style.css
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── register.html
│   ├── dashboard.html
│   ├── files.html
│   ├── preview.html
│   ├── reconcile_new.html
│   ├── reconcile_configure.html
│   ├── result.html
│   └── history.html
└── sample_data/         # Example CSV files for testing
    ├── ledger.csv
    └── bank_statement.csv


Sample data

The `sample_data/` folder contains two small CSV files you can upload to try the app right away without using real data.
