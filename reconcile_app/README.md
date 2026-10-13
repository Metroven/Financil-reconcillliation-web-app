# Tally - reconciliation system

Create an account, upload CSV or Excel files, and reconcile any two of them.

## Run it
    pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000, create an account, upload `sample_data/ledger.csv`
and `sample_data/bank_statement.csv`, then choose "New reconciliation".

## How matching works
1. Same reference: matched if amounts agree within the tolerance, otherwise a discrepancy.
2. No reference match: same amount (within tolerance) and dates within the date window.
3. Optional: similar description and date, amount within 10%, reported as a discrepancy to review.
Each record is used once. Anything left over is listed as "only in A" or "only in B".
Results can be downloaded as CSV and are kept under "Past reconciliations".

## Notes
- Data lives in `data/` (SQLite database, uploaded files, generated secret key). Back it up; keep it private.
- Passwords are hashed (werkzeug). Forms use CSRF tokens. Every file and result is scoped to its owner.
- For real deployment set SECRET_KEY, serve behind HTTPS with a WSGI server (e.g. `gunicorn app:app`),
  and add login rate limiting.
