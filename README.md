# CCB / Pushpay to Google Calendar Sync

This is a public, sanitized version of a Python project I built to sync event data from CCB/Pushpay into Google Calendar.

The private version is used to keep an organizational calendar up to date without having to manually recreate the same events in Google Calendar. I removed production credentials, calendar IDs, event data, and other private configuration from this repository.

The production project has handled **1,200+ event records** and has run automatically through GitHub Actions thousands of times.

## What it does

- Fetches event profiles from the CCB/Pushpay API
- Parses the XML response into Python dictionaries
- Transfers event details such as dates, locations, organizers, descriptions, and notes
- Parses supported recurrence descriptions and builds Google Calendar `RRULE` values
- Stores the CCB event ID on the matching Google Calendar event
- Checks whether an event already exists before creating a new one
- Updates existing events when a matching CCB event is found
- Tracks how many events were created, updated, skipped, or failed

## How the sync works

```text
CCB / Pushpay API
       |
       | XML event data
       v
   Python script
   ├── parse event data
   ├── transform fields
   ├── parse recurrence information
   └── check for existing events
       |
       | Google Calendar API
       v
Google Calendar
```

The script uses the Google Calendar API with OAuth 2.0 for authentication.

## Avoiding duplicate events

Each Google Calendar event created by the script stores its CCB event ID as a private extended property:

```text
ccb_event_id=<source event id>
```

Before creating an event, the script searches Google Calendar for that ID. If it finds a match, it updates the existing event. If it does not find one, it creates a new event.

This lets the sync run repeatedly without intentionally creating a new copy of every event each time.

## Recurring events

CCB provides recurrence information as text. The script parses supported patterns to extract information such as:

- frequency: daily, weekly, or monthly
- intervals, such as every 2 weeks
- selected weekdays
- an end date when one is present

It then uses those values to build a Google Calendar `RRULE` when the pattern is recognized by the parser.

For example, a description such as:

```text
Every week on Sunday until Dec 22, 2026
```

can produce a rule similar to:

```text
RRULE:FREQ=WEEKLY;BYDAY=SU;UNTIL=20261222T235959Z
```

Not every possible recurrence format is handled by the current parser.

## Technologies

- Python
- Requests
- lxml
- Google Calendar API
- OAuth 2.0
- XML
- GitHub Actions
- python-dotenv
- pytz

## Running it locally

1. Create and activate a Python virtual environment.
2. Install the dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and add your own CCB API configuration.
4. Create Google OAuth desktop credentials and save the file locally as `credentials.json`.
5. Run the script:

```bash
python script.py
```

On the first local run, Google opens an OAuth consent flow. The generated `token.json` is kept out of Git.

## Configuration

| Variable | Purpose |
| --- | --- |
| `CCB_USERNAME` | CCB API username |
| `CCB_PASSWORD` | CCB API password |
| `CCB_BASE_URL` | Base URL for the CCB API |
| `GOOGLE_CALENDAR_ID` | Destination Google Calendar ID; defaults to `primary` |
| `MINISTRY_EMAIL_MAP_JSON` | Optional JSON mapping from CCB resource/ministry names to calendar email addresses |
| `GOOGLE_CREDENTIALS_FILE` | Optional path to the Google OAuth credentials file |
| `GOOGLE_TOKEN_FILE` | Optional path to the stored OAuth token |

Example mapping:

```json
{
  "Example Ministry": "example-calendar@group.calendar.google.com"
}
```

## GitHub Actions

The private production version runs automatically through GitHub Actions. The `examples/sync.yml` file in this repository shows the general workflow structure without including production secrets.

For a real deployment, API credentials, OAuth data, and calendar configuration should be stored in GitHub Actions Secrets instead of being committed to the repository.

## Security

This public repository does not include the production credentials or private organizational event data used by the original deployment.

The following files and values should stay private:

- `.env`
- `credentials.json`
- `token.json`
- API usernames and passwords
- production calendar IDs
- private event data

## Note

This repository is meant to show the main design and implementation of the project while keeping the real deployment configuration and organizational data private.
