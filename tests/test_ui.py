"""Exercise real Streamlit forms and page rendering against isolated SQLite databases."""
import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from demo import seed
from service import Service


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Streamlit's REPL detection scans every imported module's Windows path.
        # These are file-based AppTest runs, so bypass that environment-only scan.
        cls.repl_check = patch('streamlit.env_util.is_repl', return_value=False)
        cls.repl_check.start()

    @classmethod
    def tearDownClass(cls):
        cls.repl_check.stop()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'ui.sqlite3'
        self.old_db = os.environ.get('DEADLINE_DB_PATH')
        os.environ['DEADLINE_DB_PATH'] = str(self.db)

    def tearDown(self):
        if self.old_db is None:
            os.environ.pop('DEADLINE_DB_PATH', None)
        else:
            os.environ['DEADLINE_DB_PATH'] = self.old_db
        self.temp.cleanup()

    def launch(self):
        at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=45).run()
        self.assertFalse(at.exception)
        return at

    def button(self, at, label):
        matches = [button for button in at.button if button.label == label]
        self.assertEqual(len(matches), 1, label)
        return matches[0]

    def field(self, elements, label):
        matches = [e for e in elements if e.label == label]
        self.assertEqual(len(matches), 1, label)
        return matches[0]

    def login(self, at, email, password='DemoPass123!'):
        at.text_input(key='login_email').input(email)
        at.text_input(key='login_pw').input(password)
        self.button(at, 'Sign in').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.title[0].value, 'Workspace overview')

    def navigate(self, at, page):
        at.sidebar.radio[0].set_value(page).run()
        self.assertFalse(at.exception, page)
        self.assertFalse(at.error, page)
        for kind in ('title', 'header', 'subheader', 'caption', 'markdown', 'text', 'info', 'warning', 'error', 'success'):
            for element in at.get(kind):
                value = getattr(element, 'value', '')
                if isinstance(value, str):
                    self.assertIsNone(re.search('[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]', value), f'Non-English interface text on {page}: {value}')

    def test_bootstrap_registration_and_sign_in(self):
        at = self.launch()
        at.text_input(key='setup_name').input('First Admin')
        at.text_input(key='setup_email').input('first@test.com')
        at.text_input(key='setup_pw').input('StrongPass123')
        at.text_input(key='setup_confirm').input('StrongPass123')
        self.button(at, 'Create administrator').click().run()
        self.assertFalse(at.exception)
        at.text_input(key='register_name').input('New Student')
        at.text_input(key='register_email').input('new@test.com')
        at.text_input(key='register_pw').input('StrongPass123')
        at.text_input(key='register_confirm').input('StrongPass123')
        self.button(at, 'Create account').click().run()
        self.assertFalse(at.exception)
        self.login(at, 'new@test.com', 'StrongPass123')
        self.navigate(at, 'My assignments')
        self.assertTrue(at.info)
        self.button(at, 'Sign out').click().run()
        self.login(at, 'first@test.com', 'StrongPass123')
        self.navigate(at, 'Users & access')
        self.assertEqual(len(at.dataframe[0].value), 2)

    def test_every_role_page_renders(self):
        seed(self.db)
        at = self.launch()
        for email, pages in [
            ('admin@example.com', ['Overview', 'Assignments', 'Review submissions', 'Courses & groups', 'Group reports', 'Users & access', 'Profile']),
            ('teacher@example.com', ['Overview', 'Assignments', 'Review submissions', 'Courses & groups', 'Group reports', 'Profile']),
            ('student@example.com', ['Overview', 'My assignments', 'My results', 'My courses', 'Profile'])]:
            self.login(at, email)
            for page in pages:
                self.navigate(at, page)
            self.button(at, 'Sign out').click().run()
            self.assertFalse(at.exception)

    def test_publish_submit_grade_report_workflow(self):
        ids = seed(self.db)
        at = self.launch()
        self.login(at, 'teacher@example.com')
        self.navigate(at, 'Assignments')
        at.text_input(key='new_assignment_title').input('UI workflow assignment')
        at.text_area(key='new_assignment_instructions').input('Submit a repository link.')
        at.selectbox(key='new_assignment_status').set_value('published')
        self.button(at, 'Create assignment').click().run()
        self.assertFalse(at.exception)
        svc = Service(self.db)
        assignment = next(a for a in svc.assignments(ids['teacher']) if a['title'] == 'UI workflow assignment')
        aid = assignment['id']
        self.button(at, 'Sign out').click().run()
        self.login(at, 'student@example.com')
        self.navigate(at, 'My assignments')
        at.text_input(key=f'submit_link_{aid}').input('https://example.com/ui-work')
        at.text_area(key=f'submit_note_{aid}').input('Please review this version.')
        at.button(key=f'FormSubmitter:submit_{aid}-Submit work').click().run()
        self.assertFalse(at.exception)
        attempt = svc.attempts(ids['student'], aid)[0]
        self.assertEqual(attempt['timeliness'], 'on_time')
        self.button(at, 'Sign out').click().run()
        self.login(at, 'teacher@example.com')
        self.navigate(at, 'Review submissions')
        self.field(at.selectbox, 'Select submission').set_value(attempt['id']).run()
        self.field(at.number_input, 'Score (out of 100)').set_value(88.0)
        self.field(at.text_area, 'Feedback').input('Good implementation.')
        self.button(at, 'Save grade & feedback').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(svc.submission(ids['student'], attempt['id'])['score'], 88)
        self.navigate(at, 'Group reports')
        summary = at.dataframe[0].value
        self.assertEqual(len(summary), 2)
        self.assertTrue(len(at.get('download_button')) >= 2)
        self.button(at, 'Sign out').click().run()
        self.login(at, 'student@example.com')
        self.navigate(at, 'My results')
        self.assertTrue(any(t.value == 'Good implementation.' for t in at.text))

    def test_admin_creation_and_enrolment_forms(self):
        ids = seed(self.db)
        at = self.launch()
        self.login(at, 'admin@example.com')
        self.navigate(at, 'Users & access')
        self.field(at.text_input, 'Full name').input('New Teacher')
        self.field(at.text_input, 'Email').input('newteacher@test.com')
        self.field(at.text_input, 'Initial password (10–128 characters)').input('StrongPass123')
        # Two Role widgets exist: use the one inside the create-user form.
        role_widgets = [e for e in at.selectbox if e.label == 'Role']
        role_widgets[0].set_value('teacher')
        self.button(at, 'Create user').click().run()
        self.assertFalse(at.exception)
        svc = Service(self.db)
        self.assertTrue(any(u['email'] == 'newteacher@test.com' for u in svc.list_users(ids['admin'])))
        self.navigate(at, 'Courses & groups')
        self.field(at.text_input, 'Group name').input('New group')
        self.button(at, 'Create group').click().run()
        self.assertFalse(at.exception)
        gid = next(g['id'] for g in svc.groups(ids['admin']) if g['name'] == 'New group')
        at.multiselect(key=f'members_selection_{gid}').set_value([ids['student']])
        at.button(key=f'FormSubmitter:members_{gid}-Save group members').click().run()
        self.assertFalse(at.exception)
        self.field(at.text_input, 'Course name').input('New course')
        self.field(at.text_area, 'Description').input('New course description')
        self.button(at, 'Create course').click().run()
        self.assertFalse(at.exception)
        cid = next(c['id'] for c in svc.courses(ids['admin']) if c['name'] == 'New course')
        at.multiselect(key=f'enrol_{cid}').set_value([gid])
        at.button(key=f'FormSubmitter:course_groups_{cid}-Save enrolment').click().run()
        self.assertFalse(at.exception)
        self.assertIn(cid, {c['id'] for c in svc.courses(ids['student'])})

    def test_phone_navigation_matches_sidebar_for_every_role(self):
        seed(self.db)
        at = self.launch()
        for email in ('student@example.com', 'teacher@example.com', 'admin@example.com'):
            self.login(at, email)
            pages = list(at.selectbox(key='phone_page').options)
            if email == 'student@example.com':
                self.assertNotIn('Users & access', pages)
                self.assertNotIn('Review submissions', pages)
            for page in pages:
                at.selectbox(key='phone_page').set_value(page).run()
                self.assertFalse(at.exception, page)
                self.assertFalse(at.error, page)
                self.assertEqual(at.sidebar.radio[0].value, page)
            self.navigate(at, 'Overview')
            self.assertEqual(at.selectbox(key='phone_page').value, 'Overview')
            self.button(at, 'Sign out').click().run()

    def test_ai_planner_without_key_saves_settings_without_network(self):
        ids = seed(self.db)
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'OPENAI_MODEL': ''}), patch('streamlit.secrets', {}), patch('ai_planner.request_blueprint') as provider:
            at = self.launch()
            self.login(at, 'student@example.com')
            self.navigate(at, 'AI study planner')
            self.assertTrue(at.button(key='generate_study_plan').disabled)
            at.number_input(key='study_daily').set_value(90)
            at.selectbox(key='study_language').set_value('English')
            self.button(at, 'Save preferences').click().run()
            self.assertFalse(at.exception)
            self.assertEqual(Service(self.db).study_preferences(ids['student'])['daily_minutes'], 90)
            self.assertEqual(Service(self.db).study_preferences(ids['student'])['language'], 'English')
            provider.assert_not_called()

    def test_ai_generation_and_stale_plan_with_mocked_provider(self):
        ids = seed(self.db)
        def blueprint(tasks, *args, **kwargs):
            return {'summary': 'A plan for your assignments.', 'tasks': [
                {'assignment_id': t['id'], 'reason': 'The nearest deadline.',
                 'steps': [{'title': 'Complete the assignment', 'minutes': t['remaining_minutes']}]} for t in tasks]}
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-test-key', 'OPENAI_MODEL': 'gpt-4.1-mini'}), patch('ai_planner.request_blueprint', side_effect=blueprint) as provider:
            at = self.launch()
            self.login(at, 'student@example.com')
            self.navigate(at, 'AI study planner')
            self.assertTrue(at.button(key='generate_study_plan').disabled)
            at.checkbox(key='study_consent').check().run()
            self.assertFalse(at.button(key='generate_study_plan').disabled)
            at.button(key='generate_study_plan').click().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            self.assertEqual(provider.call_count, 1)
            self.assertTrue(any(text.value == 'A plan for your assignments.' for text in at.text))
            self.assertTrue(any(button.proto.label == 'Download full plan JSON' for button in at.get('download_button')))
            self.navigate(at, 'AI study planner')
            self.assertEqual(provider.call_count, 1)  # Rendering does not generate another paid request.
            svc = Service(self.db)
            prefs = svc.study_preferences(ids['student'])
            svc.save_study_settings(ids['student'], 30, prefs['start_time'], prefs['horizon_days'], prefs['language'], prefs['goal'], {})
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any('changed' in warning.value for warning in at.warning))

    def test_phone_report_pagination_and_filters(self):
        from datetime import timedelta
        from service import utcnow
        ids = seed(self.db)
        svc = Service(self.db)
        for index in range(10):
            svc.save_assignment(ids['teacher'], ids['course'], f'Extra assignment {index}', 'Instructions', 100, utcnow() + timedelta(days=20), 'published')
        at = self.launch()
        self.login(at, 'student@example.com')
        at.selectbox(key='phone_page').set_value('My results').run()
        self.assertFalse(at.exception)
        count = len(svc.report(ids['student'], ids['course']))
        card_html = next(m.value for m in at.markdown if m.value.startswith('<div class="record-list">'))
        self.assertEqual(card_html.count('class="record-card"'), 10)
        at.selectbox(key='record_page_results').set_value(1).run()
        self.assertFalse(at.exception)
        card_html = next(m.value for m in at.markdown if m.value.startswith('<div class="record-list">'))
        self.assertEqual(card_html.count('class="record-card"'), count - 10)
        self.assertEqual(len(at.dataframe[0].value), count)
        self.field(at.selectbox, 'Show').set_value('Overdue').run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.dataframe[0].value), 0) if at.dataframe else self.assertTrue(at.info)
        self.field(at.selectbox, 'Show').set_value('Pending').run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.dataframe[0].value), 11)


if __name__ == '__main__':
    unittest.main()
