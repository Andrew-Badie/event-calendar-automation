# CCB / Pushpay → Google Calendar Sync

A sanitized portfolio version of a production Python automation that synchronizes event records from CCB/Pushpay into Google Calendar.

The original deployment keeps an organizational calendar current without requiring staff to manually recreate events. This public version intentionally excludes production credentials, private calendar IDs, event data, and deployment history.

## Highlights

- Fetches event profiles from the CCB/Pushpay API
- Parses XML event data into Python dictionaries
- Transfers schedules, locations, organizers, notes, and other event metadata
- Parses recurring-event descriptions and builds Google Calendar `RRULE` values
- Stores each CCB event ID as a private Google Calendar extended property
- Checks for an existing Google Calendar event before deciding whether to create or update it
- Tracks created, updated, skipped, and failed records during synchronization
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
   ├── event transformation
   ├── recurrence parsing
   └── create/update reconciliation
       |
       | Google Calendar API + OAuth 2.0
       v
Google Calendar
```

## Event reconciliation

Each Google Calendar event stores its corresponding CCB event ID in a private extended property:

```text
ccb_event_id=<source event id>
```

During synchronization, the script searches Google Calendar for that CCB event ID. If a matching event is found, the event is updated. If no match is found, a new event is created.

This allows repeated synchronization runs to keep existing calendar entries current instead of blindly creating a new event every time.

## Recurring events

The script reads CCB recurrence descriptions and extracts fields such as:

- recurrence frequency (`DAILY`, `WEEKLY`, or `MONTHLY`)
- interval values such as every 2 weeks
- selected weekdays
- recurrence end dates

Those values are then used to build Google Calendar `RRULE` strings when the recurrence pattern is recognized by the parser.

For example:

```text
Every week on Sunday until Dec 22, 2026
```

can be translated to a rule similar to:

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
| `GOOGLE_CALENDAR_ID` | Destination Google Calendar ID; defaults to `primary` |
| `MINISTRY_EMAIL_MAP_JSON` | Optional JSON mapping from source resource/ministry names to calendar email addresses |
| `GOOGLE_CREDENTIALS_FILE` | Optional OAuth client-credentials path |
| `GOOGLE_TOKEN_FILE` | Optional stored-token path |

Example mapping:

```json
{
  "Example Ministry": "example-calendar@group.calendar.google.com"
}
```

## GitHub Actions

`examples/sync.yml` demonstrates the scheduled automation structure without including production secrets. For an actual deployment, credentials should be stored in GitHub Actions Secrets rather than committed to the repository.

## Security

This repository intentionally contains **no production credentials or private event data**.

Files such as the following should remain private and are excluded from version control:

- `.env`
- `credentials.json`
- `token.json`
- production API credentials
- production calendar identifiers and configuration

## Portfolio note

This repository is a sanitized version of a real automation project. Production-specific configuration, credentials, and organizational data remain private.
