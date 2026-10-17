"""Persistence, validation, authorization and deadline rules. No Streamlit dependency."""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

UTC = timezone.utc
MAX_FILE_BYTES = 10 * 1024 * 1024
ROLES = ('administrator', 'teacher', 'student')


class AppError(ValueError):
    """A user-facing validation or permission error."""


def utcnow():
    return datetime.now(UTC)


def timestamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AppError('A timezone-aware date and time is required.')
    return value.astimezone(UTC).isoformat(timespec='microseconds')


def parse_timestamp(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def clean_text(value, label, limit, required=True):
    value = str(value or '').strip()
    if required and not value:
        raise AppError(f'{label} is required.')
    if len(value) > limit:
        raise AppError(f'{label} must be at most {limit} characters.')
    return value


def clean_email(value):
    value = clean_text(value, 'Email', 254).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise AppError('Enter a valid email address.')
    return value


def hash_password(password):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        raise AppError('Use a password of 10–128 characters.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600_000)
    return f'pbkdf2_sha256$600000${salt}${digest.hex()}'


def verify_password(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        algorithm, rounds, salt, digest = encoded.split('$')
        if algorithm != 'pbkdf2_sha256':
            return False
        computed = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(computed.hex(), digest)
    except (ValueError, TypeError):
        return False


def numeric_score(value, label):
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise AppError(f'{label} must be a number.') from None
    if not math.isfinite(value):
        raise AppError(f'{label} must be finite.')
    return value


class Service:
    def __init__(self, db_path, clock=utcnow):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self.connection() as db:
            db.executescript(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        # Additive migration for databases created before the AI planner update.
        with self.connection(write=True) as db:
            columns = {row['name'] for row in db.execute('PRAGMA table_info(assignments)')}
            if 'estimated_hours' not in columns:
                db.execute('ALTER TABLE assignments ADD COLUMN estimated_hours REAL NOT NULL DEFAULT 2 CHECK(estimated_hours BETWEEN 0.25 AND 160)')
        # Used to avoid skipping expensive password verification for unknown emails.
        self.dummy_hash = hash_password('unavailable-account-password')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA journal_mode = WAL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def has_users(self):
        with self.connection() as db:
            return bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())

    def _insert_user(self, db, name, email, password, role):
        if role not in ROLES:
            raise AppError('Invalid role.')
        name = clean_text(name, 'Name', 100)
        email = clean_email(email)
        encoded = hash_password(password)
        try:
            return db.execute('INSERT INTO users(name,email,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                              (name, email, encoded, role, timestamp(self.clock()))).lastrowid
        except sqlite3.IntegrityError:
            raise AppError('An account with this email already exists.') from None

    def bootstrap(self, name, email, password):
        with self.connection(write=True) as db:
            if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise AppError('The administrator has already been configured.')
            return self._insert_user(db, name, email, password, 'administrator')

    def register(self, name, email, password):
        with self.connection(write=True) as db:
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                raise AppError('Configure the administrator first.')
            return self._insert_user(db, name, email, password, 'student')

    def _actor(self, db, actor_id, roles=ROLES):
        row = db.execute('SELECT id,name,email,role,active FROM users WHERE id=?', (actor_id,)).fetchone()
        if not row or not row['active'] or row['role'] not in roles:
            raise AppError('You do not have permission to perform this action.')
        return dict(row)

    def user(self, actor_id):
        with self.connection() as db:
            return self._actor(db, actor_id)

    def authenticate(self, email, password):
        email = str(email or '').strip().lower()
        error = None
        user_id = None
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
            valid = verify_password(password, row['password_hash'] if row else self.dummy_hash)
            now = self.clock()
            if row and row['locked_until'] and parse_timestamp(row['locked_until']) > now:
                error = 'Too many failed attempts. Try again in five minutes.'
            elif not row or not valid or not row['active']:
                error = 'Incorrect email or password, or the account is inactive.'
                if row and row['active']:
                    failed = (0 if row['locked_until'] else row['failed_attempts']) + 1
                    locked = timestamp(now + timedelta(minutes=5)) if failed >= 5 else None
                    db.execute('UPDATE users SET failed_attempts=?,locked_until=? WHERE id=?', (failed, locked, row['id']))
            else:
                db.execute('UPDATE users SET failed_attempts=0,locked_until=NULL WHERE id=?', (row['id'],))
                user_id = row['id']
        # Raise after committing failed-attempt counters.
        if error:
            raise AppError(error)
        return self.user(user_id)

    def create_user(self, actor_id, name, email, password, role):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            return self._insert_user(db, name, email, password, role)

    def list_users(self, actor_id):
        with self.connection() as db:
            self._actor(db, actor_id, ('administrator',))
            return [dict(r) for r in db.execute('SELECT id,name,email,role,active,created_at FROM users ORDER BY name')]

    def update_user(self, actor_id, user_id, role, active):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            if role not in ROLES:
                raise AppError('Invalid role.')
            if actor_id == user_id and (role != 'administrator' or not active):
                raise AppError('You cannot remove your own administrator access.')
            if not db.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone():
                raise AppError('User not found.')
            if role not in ('teacher', 'administrator') and db.execute('SELECT 1 FROM courses WHERE teacher_id=?', (user_id,)).fetchone():
                raise AppError('Reassign this user’s courses before changing their role.')
            if role != 'student' and db.execute('SELECT 1 FROM group_members WHERE student_id=?', (user_id,)).fetchone():
                raise AppError('Remove the student from groups before changing their role.')
            db.execute('UPDATE users SET role=?,active=?,failed_attempts=0,locked_until=NULL WHERE id=?', (role, int(bool(active)), user_id))

    def change_password(self, actor_id, old_password, new_password):
        with self.connection(write=True) as db:
            self._actor(db, actor_id)
            row = db.execute('SELECT password_hash FROM users WHERE id=?', (actor_id,)).fetchone()
            if not verify_password(old_password, row['password_hash']):
                raise AppError('Current password is incorrect.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(new_password), actor_id))

    def create_group(self, actor_id, name, description=''):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            try:
                return db.execute('INSERT INTO groups(name,description) VALUES(?,?)',
                                  (clean_text(name, 'Group name', 100), clean_text(description, 'Description', 2000, False))).lastrowid
            except sqlite3.IntegrityError:
                raise AppError('A group with that name already exists.') from None

    def groups(self, actor_id):
        with self.connection() as db:
            actor = self._actor(db, actor_id)
            if actor['role'] == 'administrator':
                rows = db.execute('SELECT * FROM groups ORDER BY name')
            elif actor['role'] == 'teacher':
                rows = db.execute('SELECT DISTINCT g.* FROM groups g JOIN course_groups cg ON cg.group_id=g.id JOIN courses c ON c.id=cg.course_id WHERE c.teacher_id=? ORDER BY g.name', (actor_id,))
            else:
                rows = db.execute('SELECT g.* FROM groups g JOIN group_members gm ON gm.group_id=g.id WHERE gm.student_id=? ORDER BY g.name', (actor_id,))
            return [dict(r) for r in rows]

    def set_members(self, actor_id, group_id, student_ids):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            if not db.execute('SELECT 1 FROM groups WHERE id=?', (group_id,)).fetchone():
                raise AppError('Group not found.')
            for sid in set(student_ids):
                student = db.execute('SELECT role FROM users WHERE id=?', (sid,)).fetchone()
                if not student or student['role'] != 'student':
                    raise AppError('Only student accounts can belong to a group.')
            db.execute('DELETE FROM group_members WHERE group_id=?', (group_id,))
            db.executemany('INSERT INTO group_members VALUES(?,?)', [(group_id, sid) for sid in set(student_ids)])

    def members(self, actor_id, group_id):
        if group_id not in {g['id'] for g in self.groups(actor_id)}:
            raise AppError('You do not have access to this group.')
        with self.connection() as db:
            self._actor(db, actor_id, ('administrator', 'teacher'))
            return [dict(r) for r in db.execute('SELECT u.id,u.name,u.email,u.active FROM users u JOIN group_members gm ON gm.student_id=u.id WHERE gm.group_id=? ORDER BY u.name', (group_id,))]

    def _course(self, db, actor_id, course_id, manage=False):
        actor = self._actor(db, actor_id)
        course = db.execute('SELECT * FROM courses WHERE id=?', (course_id,)).fetchone()
        if not course:
            raise AppError('Course not found.')
        permitted = actor['role'] == 'administrator' or (actor['role'] == 'teacher' and course['teacher_id'] == actor_id)
        if not manage and actor['role'] == 'student':
            permitted = bool(db.execute('SELECT 1 FROM course_groups cg JOIN group_members gm ON gm.group_id=cg.group_id WHERE cg.course_id=? AND gm.student_id=?', (course_id, actor_id)).fetchone())
        if not permitted:
            raise AppError('You do not have access to this course.')
        return dict(course)

    def courses(self, actor_id):
        with self.connection() as db:
            actor = self._actor(db, actor_id)
            sql = 'SELECT DISTINCT c.*,u.name AS teacher_name FROM courses c JOIN users u ON u.id=c.teacher_id'
            args = ()
            if actor['role'] == 'teacher':
                sql += ' WHERE c.teacher_id=?'
                args = (actor_id,)
            elif actor['role'] == 'student':
                sql += ' JOIN course_groups cg ON cg.course_id=c.id JOIN group_members gm ON gm.group_id=cg.group_id WHERE gm.student_id=?'
                args = (actor_id,)
            return [dict(r) for r in db.execute(sql + ' ORDER BY c.name', args)]

    def create_course(self, actor_id, name, description, teacher_id=None):
        with self.connection(write=True) as db:
            actor = self._actor(db, actor_id, ('administrator', 'teacher'))
            teacher_id = teacher_id or actor_id
            if actor['role'] == 'teacher' and teacher_id != actor_id:
                raise AppError('Teachers can create only their own courses.')
            self._actor(db, teacher_id, ('teacher', 'administrator'))
            return db.execute('INSERT INTO courses(name,description,teacher_id,created_at) VALUES(?,?,?,?)',
                              (clean_text(name, 'Course name', 150), clean_text(description, 'Description', 4000, False), teacher_id, timestamp(self.clock()))).lastrowid

    def set_course_groups(self, actor_id, course_id, group_ids):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            self._course(db, actor_id, course_id, True)
            for gid in set(group_ids):
                if not db.execute('SELECT 1 FROM groups WHERE id=?', (gid,)).fetchone():
                    raise AppError('Group not found.')
            db.execute('DELETE FROM course_groups WHERE course_id=?', (course_id,))
            db.executemany('INSERT INTO course_groups VALUES(?,?)', [(course_id, gid) for gid in set(group_ids)])

    def course_group_ids(self, actor_id, course_id):
        with self.connection() as db:
            self._course(db, actor_id, course_id)
            return [r[0] for r in db.execute('SELECT group_id FROM course_groups WHERE course_id=?', (course_id,))]

    def reassign_course(self, actor_id, course_id, teacher_id):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('administrator',))
            self._course(db, actor_id, course_id, True)
            self._actor(db, teacher_id, ('teacher', 'administrator'))
            db.execute('UPDATE courses SET teacher_id=? WHERE id=?', (teacher_id, course_id))

    def save_assignment(self, actor_id, course_id, title, instructions, max_score, deadline, status, assignment_id=None, estimated_hours=None):
        score = numeric_score(max_score, 'Maximum score')
        if score <= 0 or score > 1_000_000:
            raise AppError('Maximum score must be greater than 0 and at most 1,000,000.')
        if status not in ('draft', 'published', 'closed'):
            raise AppError('Invalid assignment status.')
        if estimated_hours is not None:
            estimated_hours = numeric_score(estimated_hours, 'Estimated hours')
            if not 0.25 <= estimated_hours <= 160:
                raise AppError('Estimated work must be between 0.25 and 160 hours.')
        values = (clean_text(title, 'Title', 200), clean_text(instructions, 'Instructions', 20000), score, timestamp(deadline), status)
        with self.connection(write=True) as db:
            self._course(db, actor_id, course_id, True)
            now = timestamp(self.clock())
            if assignment_id:
                existing = db.execute('SELECT * FROM assignments WHERE id=? AND course_id=?', (assignment_id, course_id)).fetchone()
                if not existing:
                    raise AppError('Assignment not found in this course.')
                if status == 'draft' and db.execute('SELECT 1 FROM submissions WHERE assignment_id=?', (assignment_id,)).fetchone():
                    raise AppError('An assignment with submissions cannot return to draft. Close it instead.')
                highest = db.execute('SELECT MAX(g.score) FROM grades g JOIN submissions s ON s.id=g.submission_id WHERE s.assignment_id=?', (assignment_id,)).fetchone()[0]
                if highest is not None and score < highest:
                    raise AppError('Maximum score cannot be lower than an existing grade.')
                effort = existing['estimated_hours'] if estimated_hours is None else estimated_hours
                db.execute('UPDATE assignments SET title=?,instructions=?,max_score=?,deadline=?,status=?,estimated_hours=?,updated_at=? WHERE id=?', (*values, effort, now, assignment_id))
                return assignment_id
            return db.execute('INSERT INTO assignments(course_id,title,instructions,max_score,deadline,status,estimated_hours,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (course_id, *values, 2.0 if estimated_hours is None else estimated_hours, now, now)).lastrowid

    def assignments(self, actor_id):
        course_ids = [c['id'] for c in self.courses(actor_id)]
        if not course_ids:
            return []
        with self.connection() as db:
            actor = self._actor(db, actor_id)
            sql = f'SELECT a.*,c.name AS course_name FROM assignments a JOIN courses c ON c.id=a.course_id WHERE a.course_id IN ({",".join("?" for _ in course_ids)})'
            if actor['role'] == 'student':
                sql += " AND a.status != 'draft'"
            return [dict(r) for r in db.execute(sql + ' ORDER BY a.deadline,a.id', course_ids)]

    def study_preferences(self, actor_id):
        with self.connection() as db:
            self._actor(db, actor_id, ('student',))
            row = db.execute('SELECT daily_minutes,start_time,horizon_days,language,goal FROM study_preferences WHERE student_id=?', (actor_id,)).fetchone()
            return {**dict(row), 'language': 'English'} if row else dict(daily_minutes=120, start_time='18:00', horizon_days=7, language='English', goal='')

    def study_tasks(self, actor_id):
        """Only this student's enrolled, published work that has no submission."""
        with self.connection() as db:
            self._actor(db, actor_id, ('student',))
            rows = db.execute('''SELECT DISTINCT a.id,a.title,a.instructions,a.deadline,a.estimated_hours,c.name AS course,
                COALESCE(p.remaining_minutes,CAST(ROUND(a.estimated_hours * 60) AS INTEGER)) AS remaining_minutes
                FROM assignments a JOIN courses c ON c.id=a.course_id
                JOIN course_groups cg ON cg.course_id=c.id JOIN group_members gm ON gm.group_id=cg.group_id
                LEFT JOIN study_progress p ON p.assignment_id=a.id AND p.student_id=gm.student_id
                WHERE gm.student_id=? AND a.status='published'
                AND NOT EXISTS(SELECT 1 FROM submissions s WHERE s.assignment_id=a.id AND s.student_id=gm.student_id)
                ORDER BY a.deadline,a.id''', (actor_id,)).fetchall()
            return [dict(row) for row in rows]

    def save_study_settings(self, actor_id, daily_minutes, start_time, horizon_days, language, goal, progress):
        if isinstance(daily_minutes, bool) or not isinstance(daily_minutes, int) or not 15 <= daily_minutes <= 480:
            raise AppError('Daily study time must be 15–480 minutes.')
        if isinstance(horizon_days, bool) or not isinstance(horizon_days, int) or not 1 <= horizon_days <= 14:
            raise AppError('Planning horizon must be 1–14 days.')
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', str(start_time)):
            raise AppError('Enter a study start time in HH:MM format.')
        if language not in ('Русский', 'Қазақша', 'English'):
            raise AppError('Unsupported plan language.')
        goal = clean_text(goal, 'Study goal', 1000, False)
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('student',))
            for aid, remaining in progress.items():
                self._assignment(db, actor_id, aid)
                if isinstance(remaining, bool) or not isinstance(remaining, int) or not 0 <= remaining <= 9600:
                    raise AppError('Remaining work must be 0–160 hours per assignment.')
            db.execute('''INSERT INTO study_preferences(student_id,daily_minutes,start_time,horizon_days,language,goal)
                VALUES(?,?,?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET daily_minutes=excluded.daily_minutes,
                start_time=excluded.start_time,horizon_days=excluded.horizon_days,language=excluded.language,goal=excluded.goal''',
                (actor_id, daily_minutes, start_time, horizon_days, language, goal))
            db.executemany('''INSERT INTO study_progress(student_id,assignment_id,remaining_minutes) VALUES(?,?,?)
                ON CONFLICT(student_id,assignment_id) DO UPDATE SET remaining_minutes=excluded.remaining_minutes''',
                [(actor_id, aid, remaining) for aid, remaining in progress.items()])

    def _assignment(self, db, actor_id, assignment_id, manage=False):
        row = db.execute('SELECT * FROM assignments WHERE id=?', (assignment_id,)).fetchone()
        if not row:
            raise AppError('Assignment not found.')
        self._course(db, actor_id, row['course_id'], manage)
        actor = self._actor(db, actor_id)
        if actor['role'] == 'student' and row['status'] == 'draft':
            raise AppError('This assignment has not been published.')
        return dict(row)

    def submit(self, actor_id, assignment_id, link='', file_name=None, file_data=None, note=''):
        link = clean_text(link, 'Link', 2048, False)
        if link:
            try:
                parsed = urlsplit(link)
                valid = parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password
            except ValueError:
                valid = False
            if not valid or any(c.isspace() for c in link):
                raise AppError('Use a valid http:// or https:// link without embedded credentials.')
        if file_data is not None:
            if not isinstance(file_data, bytes) or not file_data or len(file_data) > MAX_FILE_BYTES:
                raise AppError('Upload a nonempty file no larger than 10 MB.')
            file_name = clean_text(str(file_name or '').replace('\\', '/').split('/')[-1], 'File name', 255)
        if not link and file_data is None:
            raise AppError('Attach a file or provide a link.')
        note = clean_text(note, 'Note', 4000, False)
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('student',))
            assignment = self._assignment(db, actor_id, assignment_id)
            if assignment['status'] != 'published':
                raise AppError('This assignment is closed for submissions.')
            now = self.clock()  # Trusted server clock, captured inside the write transaction.
            on_time = now <= parse_timestamp(assignment['deadline'])
            attempt = db.execute('SELECT COALESCE(MAX(attempt),0)+1 FROM submissions WHERE assignment_id=? AND student_id=?', (assignment_id, actor_id)).fetchone()[0]
            return db.execute('INSERT INTO submissions(assignment_id,student_id,attempt,submitted_at,deadline_at_submission,timeliness,link,file_name,file_data,note) VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (assignment_id, actor_id, attempt, timestamp(now), assignment['deadline'], 'on_time' if on_time else 'late', link or None, file_name if file_data is not None else None, file_data, note)).lastrowid

    def _submission(self, db, actor_id, submission_id):
        actor = self._actor(db, actor_id)
        row = db.execute('SELECT s.*,g.score,g.feedback,g.graded_at FROM submissions s LEFT JOIN grades g ON g.submission_id=s.id WHERE s.id=?', (submission_id,)).fetchone()
        if not row:
            raise AppError('Submission not found.')
        if actor['role'] == 'student':
            if row['student_id'] != actor_id:
                raise AppError('You do not have access to this submission.')
        else:
            self._assignment(db, actor_id, row['assignment_id'], True)
        return dict(row)

    def submission(self, actor_id, submission_id):
        with self.connection() as db:
            return self._submission(db, actor_id, submission_id)

    def attempts(self, actor_id, assignment_id, student_id=None):
        with self.connection() as db:
            actor = self._actor(db, actor_id)
            if actor['role'] == 'student':
                student_id = actor_id
                # Students retain access to their own past work after enrolment changes.
            else:
                self._assignment(db, actor_id, assignment_id, True)
            sql = 'SELECT s.id,s.assignment_id,s.student_id,s.attempt,s.submitted_at,s.deadline_at_submission,s.timeliness,s.link,s.file_name,s.note,u.name AS student_name,g.score,g.feedback,g.graded_at FROM submissions s JOIN users u ON u.id=s.student_id LEFT JOIN grades g ON g.submission_id=s.id WHERE s.assignment_id=?'
            args = [assignment_id]
            if student_id is not None:
                sql += ' AND s.student_id=?'
                args.append(student_id)
            return [dict(r) for r in db.execute(sql + ' ORDER BY s.submitted_at DESC,s.id DESC', args)]

    def grade(self, actor_id, submission_id, score, feedback):
        with self.connection(write=True) as db:
            self._actor(db, actor_id, ('teacher', 'administrator'))
            submission = self._submission(db, actor_id, submission_id)
            assignment = self._assignment(db, actor_id, submission['assignment_id'], True)
            score = numeric_score(score, 'Score')
            if not 0 <= score <= assignment['max_score']:
                raise AppError(f'Score must be between 0 and {assignment["max_score"]:g}.')
            feedback = clean_text(feedback, 'Feedback', 10000, False)
            db.execute('INSERT INTO grades(submission_id,grader_id,score,feedback,graded_at) VALUES(?,?,?,?,?) ON CONFLICT(submission_id) DO UPDATE SET grader_id=excluded.grader_id,score=excluded.score,feedback=excluded.feedback,graded_at=excluded.graded_at', (submission_id, actor_id, score, feedback, timestamp(self.clock())))

    def report(self, actor_id, course_id, group_id=None):
        """One row per currently enrolled student and non-draft assignment, latest attempt only."""
        with self.connection() as db:
            actor = self._actor(db, actor_id)
            course = self._course(db, actor_id, course_id)
            if group_id is not None and not db.execute('SELECT 1 FROM course_groups WHERE course_id=? AND group_id=?', (course_id, group_id)).fetchone():
                raise AppError('This group is not enrolled in this course.')
            sql = 'SELECT DISTINCT u.id,u.name,u.email FROM users u JOIN group_members gm ON gm.student_id=u.id JOIN course_groups cg ON cg.group_id=gm.group_id WHERE cg.course_id=?'
            args = [course_id]
            if group_id is not None:
                sql += ' AND gm.group_id=?'
                args.append(group_id)
            if actor['role'] == 'student':
                sql += ' AND u.id=?'
                args.append(actor_id)
            students = db.execute(sql + ' ORDER BY u.name', args).fetchall()
            assignments = db.execute("SELECT * FROM assignments WHERE course_id=? AND status!='draft' ORDER BY deadline", (course_id,)).fetchall()
            results = []
            now = self.clock()
            for assignment in assignments:
                for student in students:
                    sub = db.execute('SELECT s.id,s.attempt,s.submitted_at,s.timeliness,g.score,g.feedback FROM submissions s LEFT JOIN grades g ON g.submission_id=s.id WHERE s.assignment_id=? AND s.student_id=? ORDER BY s.attempt DESC LIMIT 1', (assignment['id'], student['id'])).fetchone()
                    state = ('Graded' if sub['score'] is not None else 'Submitted') if sub else ('Overdue' if now > parse_timestamp(assignment['deadline']) else 'Pending')
                    results.append(dict(course=course['name'], course_id=course_id, student=student['name'], student_id=student['id'], email=student['email'], assignment=assignment['title'], assignment_id=assignment['id'], deadline=assignment['deadline'], publication=assignment['status'], max_score=assignment['max_score'], status=state, submission_id=sub['id'] if sub else None, attempt=sub['attempt'] if sub else None, submitted_at=sub['submitted_at'] if sub else None, timeliness=sub['timeliness'] if sub else None, score=sub['score'] if sub else None, feedback=sub['feedback'] if sub else None))
            return results
