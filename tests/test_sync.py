"""Offline regression checks; all Calendar API calls are mocked."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import script


class SyncTests(unittest.TestCase):
    def events(self):
        return script.parse_events(SimpleNamespace(content=(Path(__file__).parent / 'fixtures/events.xml').read_bytes()))

    def test_missing_resources_do_not_crash_or_inherit_previous_ministry(self):
        events = self.events()
        self.assertEqual(events[0]['ministry'], 'Example Ministry')
        self.assertIsNone(events[1]['ministry'])
        self.assertIsNone(events[2]['ministry'])

    def test_monthly_ordinals_and_weekly_days(self):
        for description, expected in [
            ('Every month on the first Sunday', 'RRULE:FREQ=MONTHLY;BYDAY=1SU'),
            ('Every month on the last Sunday', 'RRULE:FREQ=MONTHLY;BYDAY=-1SU'),
            ('Every week on Monday and Wednesday', 'RRULE:FREQ=WEEKLY;BYDAY=MO,WE'),
        ]:
            with self.subTest(description=description):
                self.assertEqual(script.build_google_rrule(script.parse_recurrence_description(description)), [expected])

    def test_lookup_failure_does_not_create_or_update(self):
        service = Mock()
        service.events.return_value.list.return_value.execute.side_effect = RuntimeError('outage')
        stats = script.sync_calendar(service, self.events()[:1])
        self.assertEqual(stats['failed'], 1)
        service.events.return_value.insert.assert_not_called()
        service.events.return_value.update.assert_not_called()

    def test_existing_event_is_updated_not_inserted(self):
        service = Mock()
        service.events.return_value.list.return_value.execute.return_value = {'items': [{'id': 'existing'}]}
        stats = script.sync_calendar(service, self.events()[:1])
        self.assertEqual(stats['updated'], 1)
        service.events.return_value.insert.assert_not_called()

    def test_absent_event_is_created(self):
        service = Mock()
        service.events.return_value.list.return_value.execute.return_value = {'items': []}
        service.events.return_value.insert.return_value.execute.return_value = {'id': 'created'}
        self.assertEqual(script.sync_calendar(service, self.events()[:1])['created'], 1)

    def test_failed_creation_is_counted_without_crashing(self):
        service = Mock()
        service.events.return_value.list.return_value.execute.return_value = {'items': []}
        service.events.return_value.insert.return_value.execute.side_effect = RuntimeError('outage')
        self.assertEqual(script.sync_calendar(service, self.events()[:1])['failed'], 1)

    def test_missing_dates_are_skipped_without_api_calls(self):
        service = Mock()
        event = self.events()[0]
        event['start_datetime'] = None
        self.assertEqual(script.sync_calendar(service, [event])['skipped'], 1)
        service.events.assert_not_called()

    def test_invalid_dates_are_counted_without_api_calls(self):
        service = Mock()
        event = self.events()[0]
        event['start_datetime'] = 'invalid'
        self.assertEqual(script.sync_calendar(service, [event])['failed'], 1)
        service.events.assert_not_called()


if __name__ == '__main__':
    unittest.main()
