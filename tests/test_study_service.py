import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import seed
from service import AppError, Service, utcnow


class StudyServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name) / 'base.sqlite'
        cls.ids = seed(cls.base)
        with closing(sqlite3.connect(cls.base)) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import shutil
        self.path = Path(self.temp.name) / (self._testMethodName + '.sqlite')
        shutil.copyfile(self.base, self.path)
        self.svc = Service(self.path)
        self.student = self.ids['student']
        self.teacher = self.ids['teacher']

    def test_students_see_only_their_unsubmitted_published_work(self):
        tasks = self.svc.study_tasks(self.student)
        self.assertEqual([t['title'] for t in tasks], ['Build your authentication flow'])
        self.assertEqual(tasks[0]['remaining_minutes'], 120)
        outsider = self.svc.register('Outsider', 'outsider@example.com', 'StrongPass123')
        self.assertEqual(self.svc.study_tasks(outsider), [])
        with self.assertRaises(AppError):
            self.svc.study_tasks(self.teacher)

    def test_preferences_and_own_progress_persist(self):
        aid = self.svc.study_tasks(self.student)[0]['id']
        self.svc.save_study_settings(self.student, 90, '19:30', 5, 'Қазақша', 'Small steps', {aid: 45})
        reopened = Service(self.path)
        self.assertEqual(reopened.study_preferences(self.student)['daily_minutes'], 90)
        self.assertEqual(reopened.study_tasks(self.student)[0]['remaining_minutes'], 45)
        self.assertEqual(reopened.study_preferences(self.ids['second_student'])['daily_minutes'], 120)
        self.assertEqual(reopened.study_tasks(self.ids['second_student'])[0]['remaining_minutes'], 120)

    def test_invalid_preferences_roll_back(self):
        aid = self.svc.study_tasks(self.student)[0]['id']
        for minutes, start, days, language, remaining in [(0, '18:00', 7, 'Русский', 60), (120, '25:00', 7, 'Русский', 60), (120, '18:00', 20, 'Русский', 60), (120, '18:00', 7, 'bad', 60), (120, '18:00', 7, 'Русский', -1)]:
            with self.assertRaises(AppError):
                self.svc.save_study_settings(self.student, minutes, start, days, language, '', {aid: remaining})
        self.assertEqual(self.svc.study_tasks(self.student)[0]['remaining_minutes'], 120)

    def test_progress_cannot_target_other_courses_or_drafts(self):
        other_course = self.svc.create_course(self.teacher, 'Private course', '')
        private = self.svc.save_assignment(self.teacher, other_course, 'Private', 'Instructions', 100, utcnow() + timedelta(days=2), 'published')
        draft = next(a for a in self.svc.assignments(self.teacher) if a['status'] == 'draft')['id']
        for aid in (private, draft):
            with self.assertRaises(AppError):
                self.svc.save_study_settings(self.student, 120, '18:00', 7, 'Русский', '', {aid: 30})

    def test_assignment_estimates_validate_and_survive_legacy_updates(self):
        assignment = self.svc.study_tasks(self.student)[0]
        aid = assignment['id']
        self.svc.save_assignment(self.teacher, self.ids['course'], assignment['title'], assignment['instructions'], 100,
                                 utcnow() + timedelta(days=2), 'published', aid, estimated_hours=3.5)
        self.assertEqual(self.svc.study_tasks(self.student)[0]['remaining_minutes'], 210)
        self.svc.save_assignment(self.teacher, self.ids['course'], assignment['title'], assignment['instructions'], 100,
                                 utcnow() + timedelta(days=3), 'published', aid)
        self.assertEqual(self.svc.study_tasks(self.student)[0]['remaining_minutes'], 210)
        with self.assertRaises(AppError):
            self.svc.save_assignment(self.teacher, self.ids['course'], 'A', 'B', 100, utcnow(), 'published', estimated_hours=float('nan'))

    def test_additive_migration_preserves_legacy_assignments(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite'
        schema = (Path(__file__).resolve().parents[1] / 'schema.sql').read_text(encoding='utf-8')
        schema = schema.replace(' estimated_hours REAL NOT NULL DEFAULT 2 CHECK(estimated_hours BETWEEN 0.25 AND 160),\n', '')
        with closing(sqlite3.connect(legacy)) as db:
            db.executescript(schema)
            db.execute("INSERT INTO users(id,name,email,password_hash,role,created_at) VALUES(1,'Teacher','legacy@example.com','unchanged-hash','teacher','2026-10-08T00:00:00+00:00')")
            db.execute("INSERT INTO courses(id,name,teacher_id,created_at) VALUES(1,'Legacy',1,'2026-10-08T00:00:00+00:00')")
            db.execute("INSERT INTO assignments(id,course_id,title,instructions,max_score,deadline,status,created_at,updated_at) VALUES(1,1,'Old work','Old instructions',100,'2026-10-12T00:00:00+00:00','published','2026-10-08T00:00:00+00:00','2026-10-08T00:00:00+00:00')")
            db.commit()
        svc = Service(legacy)
        again = Service(legacy)  # Migration is idempotent.
        self.assertEqual(svc.assignments(1)[0]['title'], 'Old work')
        self.assertEqual(again.assignments(1)[0]['estimated_hours'], 2.0)
        with again.connection() as db:
            self.assertEqual(db.execute('SELECT password_hash FROM users WHERE id=1').fetchone()[0], 'unchanged-hash')


if __name__ == '__main__':
    unittest.main()
