import json
import os
import re
from datetime import datetime

import pytz
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from lxml import etree

DAY_MAPPING = {
    "monday": "MO",
    "tuesday": "TU",
    "wednesday": "WE",
    "thursday": "TH",
    "friday": "FR",
    "saturday": "SA",
    "sunday": "SU",
}

ORDINAL_MAPPING = {
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "last": "-1",
}

DEFAULT_TIMEZONE = "America/Toronto"
SCOPES = ["https://www.googleapis.com/auth/calendar"]

load_dotenv()

CCB_USERNAME = os.getenv("CCB_USERNAME")
CCB_PASSWORD = os.getenv("CCB_PASSWORD")
CCB_BASE_URL = os.getenv("CCB_BASE_URL")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")


def load_ministry_mapping():
    """Load an optional resource/ministry -> calendar-email mapping from JSON."""
    raw = os.getenv("MINISTRY_EMAIL_MAP_JSON", "{}")

    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MINISTRY_EMAIL_MAP_JSON must be valid JSON.") from exc

    if not isinstance(mapping, dict):
        raise ValueError("MINISTRY_EMAIL_MAP_JSON must contain a JSON object.")

    return mapping


MINISTRY_MAPPING = load_ministry_mapping()


def require_environment():
    """Fail early when required CCB configuration is missing."""
    required = {
        "CCB_USERNAME": CCB_USERNAME,
        "CCB_PASSWORD": CCB_PASSWORD,
        "CCB_BASE_URL": CCB_BASE_URL,
    }
    missing = [name for name, value in required.items() if not value]

    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


def fetch_events_from_ccb():
    """Fetch event profiles from the CCB/Pushpay API."""
    require_environment()

    response = requests.get(
        f"{CCB_BASE_URL.rstrip('/')}/api.php",
        params={"srv": "event_profiles"},
        auth=(CCB_USERNAME, CCB_PASSWORD),
        timeout=30,
    )
    response.raise_for_status()

    # Deliberately do not print response.text because production responses can
    # contain event names, organizer details, notes, and location information.
    print(f"CCB API status: {response.status_code}")
    return response


def parse_events(response):
    """Parse the CCB XML response into normalized event dictionaries."""
    root = etree.fromstring(response.content)

    # CCB can return HTTP 200 while reporting an application-level error in XML.
    api_errors = root.findall(".//errors/error")
    if api_errors:
        messages = [
            error.text.strip()
            for error in api_errors
            if error.text and error.text.strip()
        ]
        message = "; ".join(messages) if messages else "Unknown CCB API error"
        raise RuntimeError(f"CCB API returned an application-level error: {message}")

    events = []

    for event_element in root.findall(".//event"):
        location_elem = event_element.find("location")
        resources_elem = event_element.find("resources")

        ministry_name = None
        if resources_elem is not None:
            resources = resources_elem.findall("resource")

            for resource in resources:
                resource_name = resource.findtext("name")
                if resource_name in MINISTRY_MAPPING:
                    ministry_name = resource_name
                    break

            if not ministry_name and resources:
                ministry_name = resources[0].findtext("name")

        events.append(
            {
                "id": event_element.get("id"),
                "name": event_element.findtext("name"),
                "description": event_element.findtext("description"),
                "start_datetime": event_element.findtext("start_datetime"),
                "end_datetime": event_element.findtext("end_datetime"),
                "timezone": event_element.findtext("timezone") or DEFAULT_TIMEZONE,
                "leader_notes": event_element.findtext("leader_notes"),
                "recurrence_description": event_element.findtext(
                    "recurrence_description"
                ),
                "organizer": event_element.findtext("organizer"),
                "location_name": (
                    location_elem.findtext("name")
                    if location_elem is not None
                    else None
                ),
                "address": (
                    location_elem.findtext("street_address")
                    if location_elem is not None
                    else None
                ),
                "city": (
                    location_elem.findtext("city")
                    if location_elem is not None
                    else None
                ),
                "state": (
                    location_elem.findtext("state")
                    if location_elem is not None
                    else None
                ),
                "zip": (
                    location_elem.findtext("zip")
                    if location_elem is not None
                    else None
                ),
                "ministry": ministry_name,
            }
        )

    return events


def is_recurring_event(recurrence_description):
    """Return True when a CCB recurrence description describes repetition."""
    if not recurrence_description:
        return False
    return "every" in recurrence_description.lower()


def extract_frequency(recurrence_description):
    """Return DAILY, WEEKLY, MONTHLY, or None for a recurrence description."""
    text = recurrence_description.lower()

    if "week" in text:
        return "WEEKLY"
    if "month" in text:
        return "MONTHLY"
    if "every day" in text or "daily" in text:
        return "DAILY"
    return None


def extract_interval(recurrence_description):
    """Extract an interval such as every 2 weeks; defaults to 1."""
    text = recurrence_description.lower()

    if "every" not in text:
        return 1

    after_every = text[text.find("every") + len("every ") :]
    words = after_every.split()

    if words and words[0].isdigit():
        return int(words[0])
    return 1


def extract_day(recurrence_description):
    """Extract weekday codes/names from weekly or ordinal-monthly recurrences."""
    text = recurrence_description.lower()

    # Example: "every month on the first Sunday"
    if "month" in text and " on the " in text:
        after_on_the = text[text.find(" on the ") + len(" on the ") :]

        for ordinal, ordinal_code in ORDINAL_MAPPING.items():
            if ordinal in after_on_the:
                for day, day_code in DAY_MAPPING.items():
                    if day in after_on_the:
                        return [f"{ordinal_code}{day_code}"]

    if " on " not in text:
        return None

    after_on = text[text.find(" on ") + len(" on ") :]
    endings = [
        after_on.find(" until"),
        after_on.find(" from"),
        after_on.find(" and is"),
    ]
    valid_endings = [position for position in endings if position != -1]

    if valid_endings:
        after_on = after_on[: min(valid_endings)]

    day_text = after_on.replace(" and ", ", ")
    days = [day.strip().capitalize() for day in day_text.split(",") if day.strip()]
    return days or None


def extract_until_date(recurrence_description):
    """Extract an end date and return it in YYYY-MM-DD format."""
    if "until" not in recurrence_description:
        return None

    after_until = recurrence_description[
        recurrence_description.find("until") + len("until ") :
    ]

    endings = [after_until.find(" from"), after_until.find(" and")]
    valid_endings = [position for position in endings if position != -1]

    date_string = (
        after_until[: min(valid_endings)].strip()
        if valid_endings
        else after_until.strip()
    )

    try:
        return datetime.strptime(date_string, "%b %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_recurrence_description(recurrence_description):
    """Convert a CCB recurrence description into normalized recurrence fields."""
    if not is_recurring_event(recurrence_description):
        return None

    return {
        "frequency": extract_frequency(recurrence_description),
        "interval": extract_interval(recurrence_description),
        "days": extract_day(recurrence_description),
        "until_date": extract_until_date(recurrence_description),
    }


def convert_days_to_google_format(day_names):
    """Convert weekdays and ordinal weekdays to Google Calendar BYDAY values."""
    if not day_names:
        return None

    codes = []

    for day in day_names:
        normalized = day.strip().upper()

        # Monthly ordinal codes are already in Google format, e.g. 1SU or -1FR.
        if re.fullmatch(r"-?[1-4](MO|TU|WE|TH|FR|SA|SU)|-1(MO|TU|WE|TH|FR|SA|SU)", normalized):
            codes.append(normalized)
            continue

        day_code = DAY_MAPPING.get(day.lower())
        if day_code:
            codes.append(day_code)

    return ",".join(codes) if codes else None


def convert_until_to_google_format(parsed_until):
    """Convert YYYY-MM-DD to the UTC UNTIL representation used in an RRULE."""
    if not parsed_until:
        return None
    return parsed_until.replace("-", "") + "T235959Z"


def build_google_rrule(parsed_recurrence):
    """Build a Google Calendar RRULE list from normalized recurrence fields."""
    if not parsed_recurrence:
        return None

    frequency = parsed_recurrence.get("frequency")
    if not frequency:
        return None

    parts = [f"FREQ={frequency}"]

    interval = parsed_recurrence.get("interval", 1)
    if interval > 1:
        parts.append(f"INTERVAL={interval}")

    days = parsed_recurrence.get("days")
    if days and frequency in {"WEEKLY", "MONTHLY"}:
        byday = convert_days_to_google_format(days)
        if byday:
            parts.append(f"BYDAY={byday}")

    until_date = parsed_recurrence.get("until_date")
    if until_date:
        parts.append(f"UNTIL={convert_until_to_google_format(until_date)}")

    return ["RRULE:" + ";".join(parts)]


def get_google_calendar_service():
    """Authenticate and return a Google Calendar API service."""
    credentials = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(
            GOOGLE_TOKEN_FILE, SCOPES
        )

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0)

        with open(GOOGLE_TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write(credentials.to_json())

    return build("calendar", "v3", credentials=credentials)


def format_datetime_for_google(datetime_string, timezone_string):
    """Convert a CCB datetime string to timezone-aware ISO 8601 format."""
    if not datetime_string:
        raise ValueError("Missing event datetime.")

    parsed = datetime.strptime(datetime_string, "%Y-%m-%d %H:%M:%S")
    timezone = pytz.timezone(timezone_string)
    return timezone.localize(parsed).isoformat()


def format_location_for_google(ccb_event):
    """Combine location fields into a Google Calendar location string."""
    parts = [
        ccb_event.get("location_name"),
        ccb_event.get("address"),
        ccb_event.get("city"),
        ccb_event.get("state"),
        ccb_event.get("zip"),
    ]
    return ", ".join(part for part in parts if part) or None


def transform_ccb_event_to_google(ccb_event, ministry_email=None):
    """Transform one normalized CCB event into Google Calendar event data."""
    if not ccb_event.get("start_datetime") or not ccb_event.get("end_datetime"):
        return None

    google_event = {
        "summary": ccb_event.get("name") or "Untitled Event",
        "start": {
            "dateTime": format_datetime_for_google(
                ccb_event["start_datetime"], ccb_event["timezone"]
            ),
            "timeZone": ccb_event["timezone"],
        },
        "end": {
            "dateTime": format_datetime_for_google(
                ccb_event["end_datetime"], ccb_event["timezone"]
            ),
            "timeZone": ccb_event["timezone"],
        },
        "extendedProperties": {
            "private": {"ccb_event_id": ccb_event.get("id")}
        },
    }

    location = format_location_for_google(ccb_event)
    if location:
        google_event["location"] = location

    description_parts = [
        value
        for value in [
            ccb_event.get("description"),
            ccb_event.get("organizer"),
            ccb_event.get("leader_notes"),
        ]
        if value
    ]
    if description_parts:
        google_event["description"] = "\n".join(description_parts)

    if ministry_email:
        google_event["attendees"] = [
            {"email": ministry_email, "responseStatus": "accepted"}
        ]

    recurrence = build_google_rrule(
        parse_recurrence_description(ccb_event.get("recurrence_description"))
    )
    if recurrence:
        google_event["recurrence"] = recurrence

    return google_event


def find_existing_event(service, ccb_event_id, calendar_id):
    """Find an existing Google event using the private source-event ID."""
    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            privateExtendedProperty=f"ccb_event_id={ccb_event_id}",
            maxResults=1,
        )
        .execute()
    )
    events = result.get("items", [])
    return events[0] if events else None


def create_event(service, google_event, calendar_id):
    """Create a Google Calendar event."""
    return (
        service.events()
        .insert(
            calendarId=calendar_id,
            body=google_event,
            sendUpdates="none",
        )
        .execute()
    )


def update_existing_event(service, event_id, google_event, calendar_id):
    """Update an existing Google Calendar event."""
    return (
        service.events()
        .update(
            calendarId=calendar_id,
            eventId=event_id,
            body=google_event,
            sendUpdates="none",
        )
        .execute()
    )


def sync_calendar(service, ccb_events, calendar_id):
    """Create/update destination events and return synchronization statistics."""
    stats = {
        "total": len(ccb_events),
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
    }

    print(f"Starting sync of {stats['total']} CCB events")

    for index, ccb_event in enumerate(ccb_events, start=1):
        # Do not log event names or event contents; public Actions logs may be visible.
        print(f"Processing event {index}/{stats['total']}")

        try:
            ministry_email = MINISTRY_MAPPING.get(ccb_event.get("ministry"))
            google_event = transform_ccb_event_to_google(
                ccb_event, ministry_email
            )

            if not google_event:
                stats["skipped"] += 1
                continue

            ccb_event_id = ccb_event.get("id")
            if not ccb_event_id:
                raise ValueError("Missing CCB event ID.")

            existing_event = find_existing_event(
                service, ccb_event_id, calendar_id
            )

            if existing_event:
                update_existing_event(
                    service,
                    existing_event["id"],
                    google_event,
                    calendar_id,
                )
                stats["updated"] += 1
            else:
                create_event(service, google_event, calendar_id)
                stats["created"] += 1

        except Exception as exc:
            stats["failed"] += 1
            # Keep logs useful without leaking event data or credentials.
            print(f"Event {index} failed: {type(exc).__name__}")

    print(
        "Sync complete: "
        f"{stats['created']} created, "
        f"{stats['updated']} updated, "
        f"{stats['skipped']} skipped, "
        f"{stats['failed']} failed."
    )

    return stats


def main():
    response = fetch_events_from_ccb()
    ccb_events = parse_events(response)
    print(f"Fetched {len(ccb_events)} event records from CCB.")

    service = get_google_calendar_service()
    stats = sync_calendar(service, ccb_events, GOOGLE_CALENDAR_ID)

    if stats["failed"]:
        raise SystemExit(f"Sync completed with {stats['failed']} failed event(s).")


if __name__ == "__main__":
    main()
