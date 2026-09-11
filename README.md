# CCB / Pushpay → Google Calendar Sync

A sanitized portfolio version of a production Python automation that synchronizes event records from CCB/Pushpay into Google Calendar.

The original deployment keeps an organizational calendar current without requiring staff to manually recreate events. This public version intentionally excludes production credentials, private calendar IDs, event data, and deployment history.

## Highlights

- Fetches event profiles from the CCB/Pushpay API
- Parses XML event data into normalized Python dictionaries
- Transfers schedules, locations, organizers, notes, and event metadata
- Converts CCB recurrence descriptions into Google Calendar `RRULE` recurrence rules
- Stores each CCB event ID as a private Google Calendar extended property
- Reconciles source and destination records so existing events are updated instead of duplicated
- Tracks created, updated, skipped, and failed records
- Designed for scheduled execution with GitHub Actions

The production deployment has handled **1,200+ event records** and accumulated **thousands of scheduled GitHub Actions workflow runs**.

## Architecture

```text
CCB / Pushpay API
       |
       | HTTP + XML
       v
   Python Sync
   ├── XML parsing
   ├── data transformation
   ├── recurrence parsing
   └── event reconciliation
       |
       | Google Calendar API + OAuth 2.0
       v
Google Calendar
```

## Preventing duplicates

Each Google Calendar event stores its corresponding CCB event ID as a private extended property. Before creating an event, the sync searches Google Calendar for that source ID. If it exists, the event is updated; otherwise, a new event is created.

This makes repeated synchronization runs safe and prevents duplicate calendar entries.

## Recurring events

CCB recurrence descriptions are parsed and translated into Google Calendar recurrence rules. The implementation handles daily, weekly, and monthly patterns, intervals, selected weekdays, ordinal monthly patterns, and end dates.

For example:

```text
Every week on Sunday until Dec 22, 2026
```

can be converted to a recurrence rule similar to:

```text
RRULE:FREQ=WEEKLY;BYDAY=SU;UNTIL=20261222T235959Z
```

## Technologies

Python · Requests · lxml · Google Calendar API · OAuth 2.0 · XML · GitHub Actions · python-dotenv · pytz

## Local setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and provide your own development credentials.
4. Create a Google OAuth desktop application and save its client credentials locally as `credentials.json`.
5. Run:

```bash
python script.py
```

The first local run opens the Google OAuth consent flow. The generated `token.json` is intentionally excluded from Git.

## Configuration

| Variable | Purpose |
| --- | --- |
| `CCB_USERNAME` | CCB API username |
| `CCB_PASSWORD` | CCB API password |
| `CCB_BASE_URL` | Base URL for the CCB API |
| `GOOGLE_CALENDAR_ID` | Destination calendar; defaults to `primary` |
| `MINISTRY_EMAIL_MAP_JSON` | Optional JSON mapping from source resource names to calendar/email addresses |
| `GOOGLE_CREDENTIALS_FILE` | Optional OAuth client-credentials path |
| `GOOGLE_TOKEN_FILE` | Optional stored-token path |

## GitHub Actions

`examples/sync.yml` demonstrates the scheduled automation structure without including production secrets. For an actual deployment, store credentials in GitHub Actions Secrets and never commit `.env`, `credentials.json`, or `token.json`.

## Security

This repository intentionally contains **no production credentials or private event data**. If a secret is ever committed, deleting the file in a later commit is not enough: revoke/rotate it and remove it from Git history before publishing the repository.

## Portfolio note

This is a sanitized version of a real automation project. Production-specific configuration and organizational data remain private.
