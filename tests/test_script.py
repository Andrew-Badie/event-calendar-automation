"""Unit tests for the CCB -> Google Calendar sync logic.

The Google Calendar service is replaced with a small fake so no network
access or credentials are needed.
"""
from types import SimpleNamespace

import pytest

import script


# --- Recurrence parsing -----------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Every week on Sunday until Dec 22, 2026", True),
    ("every 2 weeks on Monday", True),
    ("Feb 15, 2025 at 9:00 am", False),
    ("", False),
    (None, False),
])
def test_is_recurring_event(text, expected):
    assert script.is_recurring_event(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("Every week on Sunday", "WEEKLY"),
    ("Every 2 weeks on Tuesday", "WEEKLY"),
    ("Every month on the first Sunday", "MONTHLY"),
    ("Every day until Jan 1, 2027", "DAILY"),
    ("Every year", None),
])
def test_extract_frequency(text, expected):
    assert script.extract_frequency(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("Every week on Sunday", 1),
    ("Every 2 weeks on Monday", 2),
    ("Every 3 months on the first Friday", 3),
    ("Every", 1),
])
def test_extract_interval(text, expected):
    assert script.extract_interval(text) == expected


def test_extract_day_single_weekday():
    assert script.extract_day("Every week on Sunday until Dec 22, 2026") == ["Sunday"]


def test_extract_day_multiple_weekdays():
    assert script.extract_day(
        "Every week on Monday, Wednesday and Friday from 6:00 pm to 7:00 pm"
    ) == ["Monday", "Wednesday", "Friday"]


def test_extract_day_monthly_ordinal():
    assert script.extract_day("Every month on the second Tuesday") == ["2TU"]


def test_extract_day_monthly_last_uses_negative_ordinal():
    # RFC 5545: "last" is -1, not 5 (a 5th weekday does not exist in most months)
    assert script.extract_day("Every month on the last Saturday") == ["-1SA"]


@pytest.mark.parametrize("text, expected", [
    ("Every week on Sunday until Dec 22, 2026", "2026-12-22"),
    ("Every week on Sunday until Dec 22, 2026 from 9:00 am to 11:00 am", "2026-12-22"),
    ("Every week on Sunday until Jan 5, 2027 and is all day", "2027-01-05"),
    ("Every week on Sunday", None),
    ("Every week on Sunday until sometime", None),
])
def test_extract_until_date(text, expected):
    assert script.extract_until_date(text) == expected


def test_convert_days_to_google_format_ignores_unknown_days():
    assert script.convert_days_to_google_format(["Monday", "Funday", "Friday"]) == "MO,FR"
    assert script.convert_days_to_google_format([]) is None


def test_convert_until_to_google_format():
    assert script.convert_until_to_google_format("2026-12-22") == "20261222T235959Z"
    assert script.convert_until_to_google_format(None) is None


# --- RRULE building ---------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Every week on Sunday until Dec 22, 2026",
     "RRULE:FREQ=WEEKLY;BYDAY=SU;UNTIL=20261222T235959Z"),
    ("Every 2 weeks on Monday and Thursday",
     "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,TH"),
    ("Every day until Jan 1, 2027",
     "RRULE:FREQ=DAILY;UNTIL=20270101T235959Z"),
    ("Every month on the first Sunday",
     "RRULE:FREQ=MONTHLY;BYDAY=1SU"),
])
def test_recurrence_text_to_rrule(text, expected):
    assert script.build_google_rrule(script.parse_recurrence_description(text)) == [expected]


def test_build_google_rrule_handles_missing_input():
    assert script.build_google_rrule(None) is None
    assert script.build_google_rrule({"frequency": None}) is None


# --- Formatting and transformation -----------------------------------------

def test_format_datetime_for_google_applies_dst_offset():
    assert script.format_datetime_for_google(
        "2025-10-08 09:00:00", "America/New_York") == "2025-10-08T09:00:00-04:00"
    assert script.format_datetime_for_google(
        "2025-12-08 09:00:00", "America/New_York") == "2025-12-08T09:00:00-05:00"


def test_format_datetime_for_google_rejects_empty():
    with pytest.raises(ValueError):
        script.format_datetime_for_google("", "America/New_York")


def test_format_location_for_google():
    event = {"location_name": "Gym", "address": "1 Main St", "city": "Markham",
             "state": "ON", "zip": None}
    assert script.format_location_for_google(event) == "Gym, 1 Main St, Markham, ON"
    assert script.format_location_for_google({}) is None


def _ccb_event(**overrides):
    event = {
        "id": "42",
        "name": "Youth Night",
        "description": "Games and pizza",
        "organizer": "Jane",
        "leader_notes": None,
        "start_datetime": "2026-11-06 18:00:00",
        "end_datetime": "2026-11-06 20:00:00",
        "timezone": "America/Toronto",
        "recurrence_description": "Every week on Friday until Dec 18, 2026",
        "location_name": "Gym",
        "address": None, "city": None, "state": None, "zip": None,
    }
    event.update(overrides)
    return event


def test_transform_builds_complete_google_event():
    google = script.transform_ccb_event_to_google(_ccb_event(), "youth@example.com")
    assert google["summary"] == "Youth Night"
    assert google["start"]["dateTime"] == "2026-11-06T18:00:00-05:00"
    assert google["location"] == "Gym"
    assert google["description"] == "Games and pizza\nJane"
    assert google["attendees"] == [{"email": "youth@example.com", "responseStatus": "accepted"}]
    assert google["extendedProperties"]["private"]["ccb_event_id"] == "42"
    assert google["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR;UNTIL=20261218T235959Z"]


@pytest.mark.parametrize("field", ["start_datetime", "end_datetime"])
def test_transform_skips_event_missing_times(field):
    assert script.transform_ccb_event_to_google(_ccb_event(**{field: None}), None) is None


def test_transform_defaults_untitled_name():
    google = script.transform_ccb_event_to_google(_ccb_event(name=None), None)
    assert google["summary"] == "Untitled Event"


# --- XML parsing ------------------------------------------------------------

CCB_XML = b"""<?xml version="1.0"?>
<ccb_api><response><events>
  <event id="7">
    <name>Choir Practice</name>
    <start_datetime>2026-11-08 10:00:00</start_datetime>
    <end_datetime>2026-11-08 11:00:00</end_datetime>
    <timezone>America/Toronto</timezone>
    <recurrence_description>Every week on Sunday</recurrence_description>
    <location><name>Hall</name><city>Markham</city></location>
    <resources><resource><name>Choir</name></resource></resources>
  </event>
  <event id="8"><name>No Resources</name></event>
</events></response></ccb_api>"""


def test_parse_events_extracts_fields():
    events = script.parse_events(SimpleNamespace(content=CCB_XML))
    assert [e["id"] for e in events] == ["7", "8"]
    first = events[0]
    assert first["name"] == "Choir Practice"
    assert first["location_name"] == "Hall"
    assert first["city"] == "Markham"
    assert first["ministry"] == "Choir"
    assert events[1]["ministry"] is None
    assert events[1]["timezone"] == script.TIMEZONE


def test_parse_events_raises_on_api_error():
    xml = b"<ccb_api><response><errors><error>bad</error></errors></response></ccb_api>"
    with pytest.raises(RuntimeError):
        script.parse_events(SimpleNamespace(content=xml))


# --- Sync / de-duplication --------------------------------------------------

class _Call:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeEvents:
    """In-memory stand-in for service.events() keyed on ccb_event_id."""

    def __init__(self, fail_insert=False):
        self.store = {}
        self.fail_insert = fail_insert
        self.inserts = self.updates = 0

    def list(self, calendarId, privateExtendedProperty, maxResults):
        ccb_id = privateExtendedProperty.split("=", 1)[1]
        items = [e for e in self.store.values()
                 if e["extendedProperties"]["private"]["ccb_event_id"] == ccb_id]
        return _Call({"items": items[:maxResults]})

    def insert(self, calendarId, body, sendUpdates):
        if self.fail_insert:
            return _Call(error=RuntimeError("API down"))
        self.inserts += 1
        event = dict(body, id=f"g{len(self.store) + 1}")
        self.store[event["id"]] = event
        return _Call(event)

    def update(self, calendarId, eventId, body, sendUpdates):
        self.updates += 1
        self.store[eventId] = dict(body, id=eventId)
        return _Call(self.store[eventId])


class FakeService:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


def test_sync_is_idempotent():
    events = FakeEvents()
    service = FakeService(events)
    ccb = [_ccb_event(id="1"), _ccb_event(id="2", name="Bible Study")]

    first = script.sync_calendar(service, ccb)
    second = script.sync_calendar(service, ccb)

    assert first["created"] == 2 and first["updated"] == 0
    assert second["created"] == 0 and second["updated"] == 2
    assert len(events.store) == 2  # no duplicates after repeated runs


def test_sync_counts_skips_and_failures_without_crashing():
    service = FakeService(FakeEvents(fail_insert=True))
    ccb = [_ccb_event(id="1"), _ccb_event(id="2", start_datetime=None)]

    stats = script.sync_calendar(service, ccb)

    assert stats == {"total": 2, "created": 0, "updated": 0, "skipped": 1, "failed": 1}
