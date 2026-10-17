import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_planner import allocate_plan, generate_plan, input_fingerprint, request_blueprint, validate_blueprint
from service import AppError, parse_timestamp, timestamp


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 8, 8, tzinfo=timezone.utc)
        self.prefs = dict(daily_minutes=120, start_time='18:00', horizon_days=3, language='Русский', goal='Small steps')
        self.tasks = [dict(id=7, title='Project', course='Python', instructions='Build a database.',
                           deadline=timestamp(self.now + timedelta(days=2)), remaining_minutes=180)]
        self.blueprint = dict(summary='Start with the database.', tasks=[dict(assignment_id=7, reason='It is due first.',
                              steps=[dict(title='Design schema', minutes=60), dict(title='Implement tables', minutes=120)])])

    def mock_factory(self, blueprint=None):
        factory = MagicMock()
        client = factory.return_value.__enter__.return_value
        client.responses.create.return_value = SimpleNamespace(status='completed', output_text=json.dumps(blueprint or self.blueprint))
        return factory, client

    def test_openai_contract_and_privacy(self):
        factory, client = self.mock_factory()
        result = generate_plan(self.tasks, self.prefs, 'Asia/Bishkek', self.now, 'fake-key', client_factory=factory)
        self.assertEqual(result['scheduled_minutes'], 180)
        request = client.responses.create.call_args.kwargs
        self.assertEqual(request['model'], 'gpt-4.1-mini')
        self.assertFalse(request['store'])
        self.assertTrue(request['text']['format']['strict'])
        payload = json.loads(request['input'])
        self.assertNotIn('fake-key', request['input'])
        self.assertEqual(payload['preferences']['language'], 'English')
        self.assertEqual(set(payload), {'now', 'timezone', 'preferences', 'tasks'})
        self.assertEqual(set(payload['tasks'][0]), {'assignment_id', 'title', 'course', 'instructions', 'deadline', 'remaining_minutes'})

    def test_missing_key_does_not_call_provider(self):
        factory, _ = self.mock_factory()
        with self.assertRaisesRegex(AppError, 'API key'):
            request_blueprint(self.tasks, self.prefs, 'UTC', self.now, '', client_factory=factory)
        factory.assert_not_called()

    def test_provider_error_redacts_request_and_key(self):
        factory, client = self.mock_factory()
        client.responses.create.side_effect = RuntimeError('secret fake-key; student private details')
        with self.assertRaises(AppError) as caught:
            request_blueprint(self.tasks, self.prefs, 'UTC', self.now, 'fake-key', client_factory=factory)
        self.assertNotIn('fake-key', str(caught.exception))
        self.assertNotIn('private details', str(caught.exception))

    def test_incomplete_and_refused_responses(self):
        for status, output in [('incomplete', ''), ('completed', '')]:
            factory, client = self.mock_factory()
            client.responses.create.return_value = SimpleNamespace(status=status, output_text=output)
            with self.assertRaises(AppError):
                request_blueprint(self.tasks, self.prefs, 'UTC', self.now, 'fake-key', client_factory=factory)

    def test_invalid_ids_durations_and_json_are_rejected(self):
        from copy import deepcopy
        cases = []
        bad = deepcopy(self.blueprint)
        bad['tasks'][0]['assignment_id'] = 999
        cases.append(bad)
        bad = deepcopy(self.blueprint)
        bad['tasks'][0]['steps'][0]['minutes'] = 61
        cases.append(bad)
        bad = deepcopy(self.blueprint)
        bad['tasks'][0]['steps'][0]['minutes'] = True
        cases.append(bad)
        bad = deepcopy(self.blueprint)
        bad['tasks'].append(bad['tasks'][0])
        cases.append(bad)
        for bad in cases:
            with self.assertRaises(AppError):
                validate_blueprint(bad, self.tasks)
        factory, client = self.mock_factory()
        client.responses.create.return_value.output_text = 'bad JSON'
        with self.assertRaises(AppError):
            request_blueprint(self.tasks, self.prefs, 'UTC', self.now, 'fake-key', client_factory=factory)

    def test_no_calendar_block_exceeds_budget_or_deadline(self):
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        by_day = {}
        last_end = None
        for block in sorted(plan['blocks'], key=lambda b: b['start']):
            start, end = parse_timestamp(block['start']), parse_timestamp(block['end'])
            self.assertGreaterEqual(start, self.now)
            self.assertLessEqual(end, parse_timestamp(self.tasks[0]['deadline']))
            if last_end:
                self.assertGreaterEqual(start, last_end)
            last_end = end
            day = start.astimezone(timezone(timedelta(hours=6))).date()
            by_day[day] = by_day.get(day, 0) + block['minutes']
        self.assertTrue(all(minutes <= 120 for minutes in by_day.values()))
        self.assertEqual(plan['scheduled_minutes'], 180)
        self.assertFalse(plan['unscheduled'])

    def test_shortage_is_reported_and_minutes_balance(self):
        self.prefs['daily_minutes'] = 30
        self.prefs['horizon_days'] = 1
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        self.assertEqual(plan['scheduled_minutes'], 30)
        self.assertEqual(plan['unscheduled'][0]['minutes'], 150)
        self.assertEqual(plan['requested_minutes'], plan['scheduled_minutes'] + sum(t['minutes'] for t in plan['unscheduled']))

    def test_today_after_study_window_is_not_backdated(self):
        now = self.now.replace(hour=17)  # 23:00 in Bishkek; 18:00–20:00 has passed.
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', now)
        self.assertTrue(plan['blocks'])
        self.assertTrue(all(parse_timestamp(b['start']) >= now for b in plan['blocks']))

    def test_midnight_clamps_late_start(self):
        self.prefs['start_time'] = '23:30'
        self.prefs['horizon_days'] = 1
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        self.assertEqual(plan['available_minutes'], 30)
        self.assertEqual(plan['scheduled_minutes'], 30)

    def test_expired_deadline_is_labeled_as_catchup(self):
        self.tasks[0]['deadline'] = timestamp(self.now - timedelta(days=1))
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        self.assertTrue(all(b['overdue'] for b in plan['blocks']))

    def test_impossible_future_deadline_not_silently_ignored(self):
        self.tasks[0]['deadline'] = timestamp(self.now + timedelta(minutes=10))
        plan = allocate_plan(self.blueprint, self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        self.assertEqual(plan['scheduled_minutes'], 0)
        self.assertEqual(plan['unscheduled'][0]['minutes'], 180)

    def test_fingerprint_changes_after_settings_work_or_day_change(self):
        first = input_fingerprint(self.tasks, self.prefs, 'Asia/Bishkek', self.now)
        self.assertNotEqual(first, input_fingerprint(self.tasks, self.prefs, 'Asia/Bishkek', self.now + timedelta(days=1)))
        self.prefs['daily_minutes'] += 15
        self.assertNotEqual(first, input_fingerprint(self.tasks, self.prefs, 'Asia/Bishkek', self.now))


if __name__ == '__main__':
    unittest.main()
