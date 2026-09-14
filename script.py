import requests
from dotenv import load_dotenv
import os
import json
import re

from lxml import etree
from datetime import datetime
import pytz
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

DAY_MAPPING = {
    'monday': 'MO',
    'tuesday': 'TU',
    'wednesday': 'WE',
    'thursday': 'TH',
    'friday': 'FR',
    'saturday': 'SA',
    'sunday': 'SU'
}

ORDINAL_MAPPING = {
    'first': '1',
    'second': '2',
    'third': '3',
    'fourth': '4',
    'last': '-1'
}



load_dotenv()


def load_ministry_mapping():
    """Load optional ministry/resource mappings from a JSON environment variable."""
    raw = os.getenv("MINISTRY_EMAIL_MAP_JSON", "{}")
    try:
        mapping = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("MINISTRY_EMAIL_MAP_JSON must be valid JSON") from exc
    if not isinstance(mapping, dict):
        raise ValueError("MINISTRY_EMAIL_MAP_JSON must contain a JSON object")
    return mapping


MINISTRY_MAPPING = load_ministry_mapping()

USERNAME = os.getenv("CCB_USERNAME")
PASSWORD = os.getenv("CCB_PASSWORD")
BASE_URL = os.getenv("CCB_BASE_URL")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")
TIMEZONE = 'America/New_York'
SCOPES = ['https://www.googleapis.com/auth/calendar']


def fetch_events_from_ccb():
    """Fetch list of events from CCB"""
    url = f"{BASE_URL}/api.php"
    params = {
        'srv': 'event_profiles'
    }

    response = requests.get(
        url,
        params=params,
        auth=(USERNAME, PASSWORD),
        timeout=30
    )

    response.raise_for_status()
    print(f"CCB API status: {response.status_code}")
    return response


def parse_events(response):
    """Parse XML and extract events into dictionaries"""

    # Parse XML
    root = etree.fromstring(response.content)

    # CCB may return HTTP 200 with an application-level error in the XML.
    api_errors = root.findall(".//errors/error")
    if api_errors:
        raise RuntimeError("CCB API returned an application-level error")

    # Get all event elements
    event_all = root.findall(".//event")

    print(f"Found {len(event_all)} events\n")

    event_list = []

    for event_element in event_all:
        ministry_name = None
        location_elem = event_element.find("location")
        resources_elem = event_element.find("resources")
        if resources_elem is not None and len(resources_elem) > 0:
            resource_elem = resources_elem.findall("resource")

            ministry_name = None
            for res in resources_elem:
                res_name = res.findtext('name')
                if res_name in MINISTRY_MAPPING:
                    ministry_name = res_name
                    break
            if not ministry_name and len(resources_elem) > 0:
                ministry_name = resources_elem[0].findtext('name')
        else:
            resource_elem = None

        event = {
            'id': event_element.get('id'),
            'name': event_element.findtext('name'),
            'description': event_element.findtext('description'),
            'location': event_element.findtext('location'),

            # Original Fields
            'start_datetime': event_element.findtext('start_datetime'),
            'end_datetime': event_element.findtext('end_datetime'),
            'timezone': event_element.findtext('timezone') or TIMEZONE,
            'leader_notes': event_element.findtext('leader_notes'),
            'recurrence_description': event_element.findtext('recurrence_description'),

            'organizer': event_element.findtext('organizer'),
            'location_name': location_elem.findtext('name') if location_elem is not None else None,
            'address': location_elem.findtext('street_address') if location_elem is not None else None,
            'city': location_elem.findtext('city') if location_elem is not None else None,
            'state': location_elem.findtext('state') if location_elem is not None else None,
            'zip': location_elem.findtext('zip') if location_elem is not None else None,
            'ministry': ministry_name
        }
        event_list.append(event)
    return event_list


def parse_recurrence_description(recurrence_description):
    recurrence_exists = is_recurring_event(recurrence_description)

    if not recurrence_exists:
        return None

    parsed = {
        'frequency': extract_frequency(recurrence_description),
        'interval': extract_interval(recurrence_description),
        'days': extract_day(recurrence_description),
        'until_date': extract_until_date(recurrence_description),
        'is_all_day': extract_all_day_flag(recurrence_description)
    }

    return parsed


def build_google_rrule(parsed_recurrence):
    parts = []
    if not parsed_recurrence:
        return None
    parsed_frequency = parsed_recurrence.get('frequency')
    if not parsed_frequency:
        return None
    parts.append(f'FREQ={parsed_frequency}')

    parsed_interval = parsed_recurrence.get('interval', 1)
    if parsed_interval > 1:
        parts.append(f"INTERVAL={parsed_interval}")
    parsed_days = parsed_recurrence.get('days')
    if parsed_days and parsed_frequency in ['WEEKLY', 'MONTHLY']:
        days_code = convert_days_to_google_format(parsed_days)
        if days_code:
            parts.append(f"BYDAY={days_code}")
    parsed_until_date = parsed_recurrence.get('until_date')
    if parsed_until_date:
        google_until = convert_until_to_google_format(parsed_until_date)
        parts.append(f"UNTIL={google_until}")

    rrule = 'RRULE:' + ';'.join(parts)
    return [rrule]


def get_google_calendar_authenticate():
    """Authenticate with Google Calendar API"""

    credentials = None
    if os.path.exists(GOOGLE_TOKEN_FILE):
        credentials = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)

    if not credentials or not credentials.valid:
        # If credentials are expired, so try to refresh
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        else:
            # This open up a browser for the user to log in
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CREDENTIALS_FILE, SCOPES
            )
            credentials = flow.run_local_server(port=0)

        # Save credentials for next time
        with open(GOOGLE_TOKEN_FILE, 'w', encoding='utf-8') as token:
            token.write(credentials.to_json())
        print("Google credentials refreshed")
    service = build('calendar', 'v3', credentials=credentials)
    return service


def format_datetime_for_google(datetime_string, timezone_string):
    """
        Converts CCB datetime string to Google Calendar ISO 8601 format.

        Args:
            datetime_string: '2025-10-08 09:00:00' from CCB
            timezone_string: 'America/New_York' from CCB

        Returns:
            '2025-10-08T09:00:00-04:00' in ISO 8601 format
        """

    if not datetime_string:
        raise ValueError("Missing datetime")
    dt = datetime.strptime(datetime_string, '%Y-%m-%d %H:%M:%S')

    tz = pytz.timezone(timezone_string)
    dt_with_tz = tz.localize(dt)
    iso_string = dt_with_tz.isoformat()

    return iso_string


def format_location_for_google(ccb_event):
    """
    Combines CCB location fields into a single Google Calendar location string

    Args:
        ccb_event: Dictionary of CCB event

    Returns:
        Combined location String, or None if all fields are empty

    Example:
        Input: {'location_name': 'Sanctuary', 'city': 'NYC', 'state': 'CA'}
        Output: 'Sanctuary, NYC, NY'
    """

    # Step 1: Create a list of location parts
    location_parts = []

    # Step 2: Add each field if it exists and it's not empty

    if ccb_event.get('location_name'):
        location_parts.append(ccb_event['location_name'])
    if ccb_event.get('address'):
        location_parts.append(ccb_event['address'])
    if ccb_event.get('city'):
        location_parts.append(ccb_event['city'])
    if ccb_event.get('state'):
        location_parts.append(ccb_event['state'])
    if ccb_event.get('zip'):
        location_parts.append(ccb_event['zip'])

    # Step 3: Join all parts with commas and space
    if location_parts:
        return ', '.join(location_parts)
    else:
        return None


def is_recurring_event(recurrence_description):
    """
        Determines if a CCB event is recurring based on its recurrence description.

        Args:
            recurrence_description: String from CCB like "Every week on Sunday until..."

        Returns:
            True if recurring, False if single occurrence

        Examples:
            "Every week on Sunday until Dec 22, 2024..." → True
            "Feb 15, 2025 at 9:00 am" → False
        """
    if not recurrence_description:
        return False

    recurring_keyword = 'every'

    description_lower = recurrence_description.lower()
    if recurring_keyword in description_lower:
        return True

    return False


def extract_until_date(recurrence_description):
    """
    Extracts the end date from CCB recurrence description

    :param recurrence_description:
    :return: Date string in YYYY-MM-DD format, or None is no end date
    """

    if 'until' not in recurrence_description:
        return None

    until_index = recurrence_description.find('until')

    after_until = recurrence_description[until_index + len('until '):]

    from_pos = after_until.find(' from')
    and_pos = after_until.find(' and')

    if from_pos == -1 and and_pos == -1:
        # No markers found, take everything
        date_string = after_until.strip()
    elif from_pos == -1:
        # Only and is found
        date_string = after_until[:and_pos].strip()
    elif and_pos == -1:
        date_string = after_until[:from_pos].strip()
    else:
        end_pos = min(from_pos, and_pos)
        date_string = after_until[:end_pos].strip()

    try:
        parsed_date = datetime.strptime(date_string, '%b %d, %Y')
        return parsed_date.strftime('%Y-%m-%d')
    except Exception as e:
        print(e)
        return None


def transform_ccb_event_to_google(ccb_event, ministry_email):
    if not ccb_event.get('id') or not ccb_event.get('start_datetime') or not ccb_event.get('end_datetime'):
        return None
    start_datetime_iso = format_datetime_for_google(ccb_event['start_datetime'], ccb_event['timezone'])
    end_datetime_iso = format_datetime_for_google(ccb_event['end_datetime'], ccb_event['timezone'])

    location_iso = format_location_for_google(ccb_event)

    if not ccb_event.get('start_datetime') or not ccb_event.get('end_datetime'):
        return None

    parts = []

    if ccb_event.get('description'):
        parts.append(ccb_event['description'])

    if ccb_event.get('organizer'):
        parts.append(ccb_event['organizer'])

    if ccb_event.get('leader_notes'):
        parts.append(ccb_event['leader_notes'])

    if parts:
        description = '\n'.join(parts)
    else:
        description = None

    google_event = {
        'summary': ccb_event.get('name', 'Untitled Event'),
        'start': {
            'dateTime': start_datetime_iso,
            'timeZone': ccb_event['timezone']
        },
        'end': {
            'dateTime': end_datetime_iso,
            'timeZone': ccb_event['timezone']
        }
    }
    if location_iso:
        google_event['location'] = location_iso
    if description:
        google_event['description'] = description

    if ministry_email:
        google_event['attendees'] = [{'email': ministry_email,
                                     'responseStatus': 'accepted'}]

    google_event['extendedProperties'] = {
        'private': {
            'ccb_event_id': ccb_event.get('id')
        }
    }

    recurrence_description = ccb_event.get('recurrence_description')

    if recurrence_description:
        parsed = parse_recurrence_description(recurrence_description)
        rrule = build_google_rrule(parsed)

        if rrule:
            google_event['recurrence'] = rrule
    return google_event


def create_event(service, google_event, calendar_id='primary'):
    """
        Creates an event in Google Calendar.

        Args:
            service: Authenticated Google Calendar service object
            google_event: Dictionary with Google Calendar event format
            calendar_id: Which calendar to create in (default: 'primary')

        Returns:
            Created event response from Google, or None if failed
        """
    try:
        # Step 1: Make the API call
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=google_event,
            sendUpdates='none'
        ).execute()

        # Step 2: return the created event
        return created_event
    except Exception as e:
        print(f"Failed to create event: {e}")
        return None


def analyze_recurrence_patterns(ccb_events):
    """See what recurrence patterns CCB actually gives us"""

    print("\n" + "=" * 60)
    print("RECURRENCE PATTERNS IN YOUR DATA")
    print("=" * 60)

    patterns = {}

    for event in ccb_events:
        recurrence = event.get('recurrence_description', '')
        if recurrence:
            # Count how many times each pattern appears
            if recurrence in patterns:
                patterns[recurrence] += 1
            else:
                patterns[recurrence] = 1

    for pattern, count in patterns.items():
        print(f"\n[{count}x] {pattern}")

    print(f"\n{'=' * 60}\n")
    print(f"Total unique patterns: {len(patterns)}")
    print("=" * 60 + "\n")


def find_existing_event(service, ccb_event_id, calendar_id='primary'):
    """
        Searches Google Calendar for an event with matching CCB event ID.

        Args:
            service: Authenticated Google Calendar service object
            ccb_event_id: The CCB event ID to search for
            calendar_id: Which calendar to search (default: 'primary')

        Returns:
            Existing Google event if found, None if not found
        """
    try:
        query = f"ccb_event_id={ccb_event_id}"

        events_result = service.events().list(
            calendarId=calendar_id,
            privateExtendedProperty=query,
            maxResults=1
        ).execute()

        events = events_result.get('items', [])

        if events:
            return events[0]  # Found existing event
        else:
            return None  # No existing event found

    except Exception as e:
        raise RuntimeError("Calendar lookup failed; creation must not be attempted") from e


def update_existing_event(service, event_id, google_event, calendar_id='primary'):
    """
        Updates an existing event in Google Calendar.

        Args:
            service: Authenticated Google Calendar service object
            event_id: The Google Calendar event ID to update
            google_event: Dictionary with updated event data
            calendar_id: Which calendar the event is in (default: 'primary')

        Returns:
            Updated event response from Google, or None if failed
        """

    try:
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=google_event,
            sendUpdates='none'
        ).execute()

        return updated_event

    except Exception as e:
        print(f"Failed to update event: {e}")
        return None


def sync_calendar(service, ccb_events, calendar_id='primary'):
    """
        Syncs all CCB events to Google Calendar.
        Creates new events, updates existing ones.

        Args:
            service: Authenticated Google Calendar service object
            ccb_events: List of CCB event dictionaries
            calendar_id: Which calendar to sync to (default: 'primary')

        Returns:
            Dictionary with sync statistics
        """
    stats = {
        'total': len(ccb_events),
        'created': 0,
        'updated': 0,
        'skipped': 0,
        'failed': 0
    }

    print(f"\n{'=' * 60}")
    print(f"Starting sync of {stats['total']} CCB events")
    print(f"{'=' * 60}")

    for index, ccb_event in enumerate(ccb_events, start=1):
        print(f"[{index}/{stats['total']}] Processing: {ccb_event.get('name', 'Untitled')[:50]}")
        ministry_name = ccb_event.get('ministry')
        ministry_email = MINISTRY_MAPPING.get(ministry_name, None)

        try:
            google_event = transform_ccb_event_to_google(ccb_event, ministry_email)
        except (ValueError, TypeError, KeyError, pytz.UnknownTimeZoneError):
            stats['failed'] += 1
            print("Failed to transform event: invalid date, timezone, or required field")
            continue

        # Skip if tranformation failed
        if not google_event:
            print(f"Skipped (missing required fields)")
            stats['skipped'] += 1
            continue

        # Check if existing event found

        ccb_event_id = ccb_event.get('id')
        try:
            existing_event = find_existing_event(service, ccb_event_id, calendar_id)
        except RuntimeError:
            stats['failed'] += 1
            print("Lookup failed; skipped write for this event")
            continue

        if existing_event:
            google_event_id = existing_event['id']
            result = update_existing_event(service, google_event_id, google_event, calendar_id)

            if result:
                print(f"Updated {google_event_id}")
                stats['updated'] += 1
            else:
                print(f"Failed to update {google_event_id}")
                stats['failed'] += 1

        else:

            result = create_event(service, google_event, calendar_id)
            if result:
                print(f"Created {result['id']}")
                stats['created'] += 1
            else:
                print("Failed to create event")
                stats['failed'] += 1
    # Print Summary
    print(f"\n{'=' * 60}")
    print(f"Sync Complete!")
    print(f"{'=' * 60}")
    print(f"Total events processed: {stats['total']}")
    print(f"  ✓ Created: {stats['created']}")
    print(f"  ✓ Updated: {stats['updated']}")
    print(f"  ○ Skipped: {stats['skipped']}")
    print(f"  ✗ Failed:  {stats['failed']}")
    print(f"{'=' * 60}\n")

    return stats


def extract_frequency(recurrence_description):
    """
        Determines if event is DAILY, WEEKLY, or MONTHLY.

        Args:
            recurrence_description: "Every week on Sunday..."

        Returns:
            'DAILY', 'WEEKLY', 'MONTHLY', or None

        Examples:
            "Every week on Sunday..." → 'WEEKLY'
            "Every day until..." → 'DAILY'
            "Every month on the first..." → 'MONTHLY'
        """
    textwrap = recurrence_description.lower()

    if 'week' in textwrap:
        return 'WEEKLY'
    elif 'month' in textwrap:
        return 'MONTHLY'
    elif 'every day' in textwrap or 'daily' in textwrap:
        return 'DAILY'
    else:
        return None


def extract_interval(recurrence_description):
    textwrap = recurrence_description.lower()

    if 'every' not in textwrap:
        return 1  # This is the default

    every_index = textwrap.find('every')
    after_every = textwrap[every_index + len('every '):]

    words_after_every = after_every.split()

    if not words_after_every:
        return 1
    first_word = words_after_every[0]

    if first_word.isdigit():
        return int(first_word)
    else:
        return 1


def extract_day(recurrence_description):
    textwrap = recurrence_description.lower()

    if 'month' in textwrap and ' on the ' in textwrap:
        # Extract ordinal + day (e.g., "first Sunday")
        on_the_index = textwrap.find(' on the ')
        after_on_the = textwrap[on_the_index + len(' on the '):]

        for ordinal, code in ORDINAL_MAPPING.items():
            if ordinal in after_on_the:
                for day, day_code in DAY_MAPPING.items():
                    if day in after_on_the:
                        return [f"{code}{day_code}"]

    if 'on' not in textwrap:
        return None

    on_index = textwrap.find(' on ')
    after_on = textwrap[on_index + len('on '):]

    until_index = after_on.find(' until')
    from_index = after_on.find(' from')
    and_is_index = after_on.find(' and is')

    endings = [until_index, from_index, and_is_index]
    valid_endings = [pos for pos in endings if pos != -1]

    if valid_endings:
        end_pos = min(valid_endings)
        day_text = after_on[:end_pos]
    else:
        day_text = after_on

    day_text = day_text.replace(' and ', ', ')

    days_after_on = day_text.split(',')
    days = [day.strip().capitalize() for day in days_after_on if day.strip()]

    return days if days else None


def convert_days_to_google_format(day_names):
    if not day_names:
        return None

    codes = []

    for day in day_names:
        day_lower = day.lower()
        if day_lower in DAY_MAPPING:
            codes.append(DAY_MAPPING[day_lower])
        elif re.fullmatch(r'-?[1-5](MO|TU|WE|TH|FR|SA|SU)', day.upper()):
            codes.append(day.upper())
    return ','.join(codes) if codes else None


def extract_all_day_flag(recurrence_description):
    textwrap = recurrence_description.lower()

    if 'all day' not in textwrap:
        return False

    else:
        return True


def convert_until_to_google_format(parsed_until):
    if not parsed_until:
        return None

    google_until = parsed_until.replace('-', '')

    return google_until + 'T' + '235959Z'


def main():
    import argparse
    from pathlib import Path
    from types import SimpleNamespace

    parser = argparse.ArgumentParser(description="Synchronize CCB events to Google Calendar")
    parser.add_argument('--dry-run', metavar='XML_FILE', help='Transform local XML without authentication or API calls')
    args = parser.parse_args()
    if args.dry_run:
        events = parse_events(SimpleNamespace(content=Path(args.dry_run).read_bytes()))
        payloads = []
        for event in events:
            payload = transform_ccb_event_to_google(event, None)
            if payload is not None:
                payloads.append(payload)
        print(json.dumps(payloads, indent=2))
        return

    response = fetch_events_from_ccb()
    ccb_events = parse_events(response)
    service = get_google_calendar_authenticate()
    stats = sync_calendar(service, ccb_events, calendar_id=GOOGLE_CALENDAR_ID)
    if stats['failed']:
        raise SystemExit(f"Sync completed with {stats['failed']} errors")


if __name__ == "__main__":
    main()
