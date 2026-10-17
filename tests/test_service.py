import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service import AppError, MAX_FILE_BYTES, Service, verify_password


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name) / 'base.sqlite3'
        cls.now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        cls.current = cls.now
        svc = Service(cls.base, clock=lambda: cls.current)
        cls.admin = svc.bootstrap('Admin', 'admin@test.com', 'StrongPass123')
        cls.teacher = svc.create_user(cls.admin, 'Teacher', 'teacher@test.com', 'StrongPass123', 'teacher')
        cls.other_teacher = svc.create_user(cls.admin, 'Other', 'other@test.com', 'StrongPass123', 'teacher')
        cls.student = svc.register('Student', 'student@test.com', 'StrongPass123')
        cls.other_student = svc.register('Other Student', 'otherstudent@test.com', 'StrongPass123')
        cls.group = svc.create_group(cls.admin, 'G-1')
        svc.set_members(cls.admin, cls.group, [cls.student])
        cls.course = svc.create_course(cls.teacher, 'Python', 'Course description')
        svc.set_course_groups(cls.admin, cls.course, [cls.group])
        cls.assignment = svc.save_assignment(cls.teacher, cls.course, 'Project', 'Instructions', 100, cls.now, 'published')
        with svc.connection() as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import shutil
        self.current = self.now
        self.db = Path(self.temp.name) / f'{self._testMethodName}.sqlite3'
        shutil.copyfile(self.base, self.db)
        self.svc = Service(self.db, clock=lambda: self.current)

    def test_authentication_and_password_hashing(self):
        self.assertEqual(self.svc.authenticate(' STUDENT@test.com ', 'StrongPass123')['id'], self.student)
        with self.svc.connection() as db:
            encoded = db.execute('SELECT password_hash FROM users WHERE id=?', (self.student,)).fetchone()[0]
        self.assertNotIn('StrongPass123', encoded)
        self.assertTrue(verify_password('StrongPass123', encoded))
        self.assertFalse(verify_password('wrong', encoded))

    def test_registration_student_only_and_duplicate_email(self):
        uid = self.svc.register('New', 'NEW@test.com', 'StrongPass123')
        self.assertEqual(self.svc.user(uid)['role'], 'student')
        with self.assertRaises(AppError):
            self.svc.register('Duplicate', 'new@test.com', 'StrongPass123')
        with self.assertRaises(AppError):
            self.svc.bootstrap('Bad', 'bad@test.com', 'StrongPass123')
        with self.assertRaises(AppError):
            self.svc.create_user(self.student, 'Bad', 'bad@test.com', 'StrongPass123', 'teacher')

    def test_validation(self):
        for email, pw in [('invalid', 'StrongPass123'), ('valid@test.com', 'short')]:
            with self.assertRaises(AppError):
                self.svc.register('User', email, pw)
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.teacher, self.course, 'A', 'B', float('nan'), self.now, 'published')
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.teacher, self.course, 'A', 'B', 100, self.now.replace(tzinfo=None), 'published')

    def test_login_lockout_is_persisted(self):
        for _ in range(5):
            with self.assertRaises(AppError):
                self.svc.authenticate('student@test.com', 'WrongPass123')
        with self.assertRaisesRegex(AppError, 'five minutes'):
            self.svc.authenticate('student@test.com', 'StrongPass123')
        self.current += timedelta(minutes=5, seconds=1)
        self.assertEqual(self.svc.authenticate('student@test.com', 'StrongPass123')['id'], self.student)

    def test_exact_deadline_is_on_time(self):
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com/work')
        result = self.svc.submission(self.student, sid)
        self.assertEqual(result['timeliness'], 'on_time')
        self.assertEqual(result['submitted_at'], result['deadline_at_submission'])

    def test_one_microsecond_late(self):
        self.current += timedelta(microseconds=1)
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com/work')
        self.assertEqual(self.svc.submission(self.student, sid)['timeliness'], 'late')

    def test_timezone_deadline_compares_absolute_time(self):
        local = self.now.astimezone(timezone(timedelta(hours=6)))
        self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 100, local, 'published', self.assignment)
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com')
        self.assertEqual(self.svc.submission(self.student, sid)['timeliness'], 'on_time')

    def test_file_upload_is_persistent_and_sanitized(self):
        sid = self.svc.submit(self.student, self.assignment, file_name='../../file.txt', file_data=b'actual student work')
        result = self.svc.submission(self.teacher, sid)
        self.assertEqual(result['file_name'], 'file.txt')
        self.assertEqual(result['file_data'], b'actual student work')
        with self.assertRaises(AppError):
            self.svc.submission(self.other_student, sid)

    def test_empty_oversize_and_invalid_links(self):
        for kwargs in [{}, {'link': 'javascript:alert(1)'}, {'link': 'https://user:pw@example.com'}, {'file_name': 'a.txt', 'file_data': b''}, {'file_name': 'a', 'file_data': b'x' * (MAX_FILE_BYTES + 1)}]:
            with self.assertRaises(AppError):
                self.svc.submit(self.student, self.assignment, **kwargs)

    def test_role_and_course_access_enforced(self):
        with self.assertRaises(AppError):
            self.svc.submit(self.other_student, self.assignment, 'https://example.com')
        with self.assertRaises(AppError):
            self.svc.submit(self.teacher, self.assignment, 'https://example.com')
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.other_teacher, self.course, 'A', 'B', 100, self.now, 'published')
        with self.assertRaises(AppError):
            self.svc.set_members(self.teacher, self.group, [self.other_student])
        self.assertEqual(self.svc.courses(self.other_teacher), [])
        self.assertEqual(self.svc.assignments(self.other_student), [])

    def test_draft_and_closed_visibility(self):
        self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 100, self.now, 'draft', self.assignment)
        self.assertEqual(self.svc.assignments(self.student), [])
        with self.assertRaises(AppError):
            self.svc.submit(self.student, self.assignment, 'https://example.com')
        self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 100, self.now, 'closed', self.assignment)
        self.assertEqual(len(self.svc.assignments(self.student)), 1)
        with self.assertRaises(AppError):
            self.svc.submit(self.student, self.assignment, 'https://example.com')

    def test_grading_feedback_and_limits(self):
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com')
        for actor in (self.student, self.other_teacher):
            with self.assertRaises(AppError):
                self.svc.grade(actor, sid, 50, 'Bad')
        for score in (-1, 101, float('inf')):
            with self.assertRaises(AppError):
                self.svc.grade(self.teacher, sid, score, 'Bad')
        self.svc.grade(self.teacher, sid, 0, 'Needs work')
        self.assertEqual(self.svc.report(self.student, self.course)[0]['status'], 'Graded')
        self.svc.grade(self.teacher, sid, 90, 'Updated feedback')
        result = self.svc.submission(self.student, sid)
        self.assertEqual(result['score'], 90)
        self.assertEqual(result['feedback'], 'Updated feedback')
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 80, self.now, 'published', self.assignment)

    def test_revision_keeps_history_and_requires_new_grade(self):
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com')
        self.svc.grade(self.teacher, sid, 75, 'First grade')
        self.current += timedelta(seconds=1)
        sid2 = self.svc.submit(self.student, self.assignment, 'https://example.com/revised')
        self.assertNotEqual(sid, sid2)
        attempts = self.svc.attempts(self.student, self.assignment)
        self.assertEqual([a['attempt'] for a in attempts], [2, 1])
        self.assertEqual(attempts[1]['score'], 75)
        row = self.svc.report(self.student, self.course)[0]
        self.assertEqual(row['status'], 'Submitted')
        self.assertEqual(row['timeliness'], 'late')
        self.assertIsNone(row['score'])

    def test_deadline_edits_preserve_historical_classification(self):
        self.current += timedelta(seconds=1)
        sid = self.svc.submit(self.student, self.assignment, 'https://example.com')
        self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 100, self.now + timedelta(days=1), 'published', self.assignment)
        self.assertEqual(self.svc.submission(self.student, sid)['timeliness'], 'late')
        sid2 = self.svc.submit(self.student, self.assignment, 'https://example.com/new')
        self.assertEqual(self.svc.submission(self.student, sid2)['timeliness'], 'on_time')

    def test_pending_and_overdue_missing_work(self):
        self.assertEqual(self.svc.report(self.student, self.course)[0]['status'], 'Pending')
        self.current += timedelta(microseconds=1)
        self.assertEqual(self.svc.report(self.student, self.course)[0]['status'], 'Overdue')

    def test_reports_include_missing_deduplicate_and_scope(self):
        second = self.svc.create_group(self.admin, 'G-2')
        self.svc.set_members(self.admin, second, [self.student, self.other_student])
        self.svc.set_course_groups(self.admin, self.course, [self.group, second])
        self.assertEqual(len(self.svc.report(self.teacher, self.course)), 2)
        self.assertEqual(len(self.svc.report(self.teacher, self.course, self.group)), 1)
        mine = self.svc.report(self.student, self.course)
        self.assertEqual([r['student_id'] for r in mine], [self.student])
        with self.assertRaises(AppError):
            self.svc.report(self.other_teacher, self.course)
        not_enrolled = self.svc.create_group(self.admin, 'G-3')
        with self.assertRaises(AppError):
            self.svc.report(self.teacher, self.course, not_enrolled)

    def test_inactive_users_lose_existing_session_access(self):
        self.svc.update_user(self.admin, self.student, 'student', False)
        for action in [lambda: self.svc.user(self.student), lambda: self.svc.submit(self.student, self.assignment, 'https://example.com'), lambda: self.svc.authenticate('student@test.com', 'StrongPass123')]:
            with self.assertRaises(AppError):
                action()
        # Inactive members can remain in a roster without breaking enrolment editing.
        self.svc.set_members(self.admin, self.group, [self.student])

    def test_admin_self_protection_and_role_constraints(self):
        with self.assertRaises(AppError):
            self.svc.update_user(self.admin, self.admin, 'student', True)
        with self.assertRaises(AppError):
            self.svc.update_user(self.admin, self.admin, 'administrator', False)
        with self.assertRaises(AppError):
            self.svc.update_user(self.admin, self.teacher, 'student', True)
        with self.assertRaises(AppError):
            self.svc.update_user(self.admin, self.student, 'teacher', True)

    def test_password_change(self):
        with self.assertRaises(AppError):
            self.svc.change_password(self.student, 'WrongPass123', 'NewStrong123')
        self.svc.change_password(self.student, 'StrongPass123', 'NewStrong123')
        self.assertEqual(self.svc.authenticate('student@test.com', 'NewStrong123')['id'], self.student)
        with self.assertRaises(AppError):
            self.svc.authenticate('student@test.com', 'StrongPass123')

    def test_submission_prevents_return_to_draft(self):
        self.svc.submit(self.student, self.assignment, 'https://example.com')
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.teacher, self.course, 'Project', 'Instructions', 100, self.now, 'draft', self.assignment)

    def test_database_persists_across_service_instances(self):
        sid = self.svc.submit(self.student, self.assignment, file_name='work.txt', file_data=b'hello')
        other = Service(self.db)
        self.assertEqual(other.submission(self.student, sid)['file_data'], b'hello')


if __name__ == '__main__':
    unittest.main()
