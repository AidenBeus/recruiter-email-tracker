# Recruiter Email Tracker

A privacy-conscious local data pipeline that reads Gmail metadata, stores every unique message's metadata in SQLite, retains full content only for likely recruiter emails, classifies likely application status with explainable rules, and shows the result in a local dashboard. It does **not** send email content to an LLM or other third party.

## What it does

```text
Gmail metadata → metadata filter → recruiter-email content → SQLite → Streamlit dashboard
```

The Gmail message ID is the unique key, so it is safe to run the importer repeatedly. Every email contributes only its ID, sender, subject, timestamp, labels, and filter decision to `email_metadata`. The importer requests and retains a full body only when metadata signals a recruiting candidate: a known recruiting-platform sender, a tracked company name, or a job-related subject. An email updates an application's current status only when it also matches a manually registered company and has a high-confidence classification.

## First-time setup

From this directory, create and activate a virtual environment, then install dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

In Google Cloud Console, create a project, enable **Gmail API**, then create an **OAuth client ID** of type **Desktop app**. Download its JSON file as `credentials.json` in this directory. It is ignored by Git. This project asks only for Gmail's read-only scope.

The default query is `in:all`, so it records metadata for all normal mailbox messages (excluding spam and trash). If that is broader than you want, change `GMAIL_QUERY` in `.env` to `label:Recruiting` after creating that label in Gmail.

## Run it

Register applications before importing so emails can be matched safely:

```powershell
python -m src.manage_applications add --company "Acme" --role "Data Engineer" --applied-at 2026-11-10
python -m src.manage_applications list
```

If you imported mail before adding applications, adding an application automatically matches any previously unassigned email that contains its company name. To retry matching all existing records after correcting company names, run:

```powershell
python -m src.manage_applications reconcile
```

You can also assign an individual unassigned email from the dashboard. Open the
**Unassigned emails** section, select the email and application, then click
**Assign email**. Applying the email's detected status is optional, so a manual
assignment cannot unexpectedly overwrite your current application status.

Applications can be added either with the command-line tool or through the
dashboard's **Add application** form in the left sidebar.

The first ingestion opens a browser for Google OAuth consent and creates a local `token.json`:

```powershell
python -m src.fetch_gmail --dry-run
python -m src.fetch_gmail
streamlit run src/dashboard.py
```

To scan a different set temporarily, pass a Gmail query explicitly:

```powershell
python -m src.fetch_gmail --query 'label:Recruiting newer_than:90d'
```

The importer searches the chosen mailbox scope on every run. Gmail IDs make this idempotent. It may read candidate bodies transiently to classify them, but it does not save bodies for ignored messages.

### Gmail API rate limits

An initial all-mail scan can be large. The importer waits one second between Gmail requests by default and uses exponential backoff for temporary quota errors. Leave that setting in place for the first scan; it intentionally trades speed for reliability. Set `GMAIL_REQUEST_DELAY_SECONDS` in `.env` only if you need to slow it down further.

## Schedule it hourly on Windows

After a successful manual run, register the included hourly task:

```powershell
.\install_scheduled_task.ps1
```

It runs `run_scheduled_import.ps1`, which imports `newer_than:1d` without a dry-run flag. The one-day overlap is intentional: Gmail message IDs prevent duplicates while protecting against a missed scheduled run. Open **Task Scheduler** and find `RecruiterEmailTrackerHourly` to inspect its history or run it manually. OAuth tokens can expire or be revoked; a failed run should be re-authorized locally.

## Security and limitations

- Never commit `.env`, `credentials.json`, `token.json`, or `data/`.
- The SQLite file holds sensitive email content; keep it on your encrypted personal machine and delete it when no longer needed.
- The rules are intentionally conservative. Review unassigned and `needs_review` records rather than treating automated status changes as authoritative.
- This is personal-email automation, so it may need instructor approval if used for the CS452 assignment instead of a public, high-volume historical/live data source.
