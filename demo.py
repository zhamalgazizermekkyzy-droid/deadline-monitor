"""Explicit, opt-in sample data for a fresh database. Never runs on app startup."""
import argparse
from datetime import timedelta
from pathlib import Path

from service import Service, utcnow

DEMO_PASSWORD = 'DemoPass123!'


def seed(path):
    svc = Service(path)
    if svc.has_users():
        raise SystemExit('Demo requires an empty database. Existing data was not modified.')
    admin = svc.bootstrap('Alex Morgan', 'admin@example.com', DEMO_PASSWORD)
    teacher = svc.create_user(admin, 'Dr. Jamie Chen', 'teacher@example.com', DEMO_PASSWORD, 'teacher')
    student = svc.create_user(admin, 'Sam Rivera', 'student@example.com', DEMO_PASSWORD, 'student')
    student2 = svc.create_user(admin, 'Taylor Brooks', 'taylor@example.com', DEMO_PASSWORD, 'student')
    group = svc.create_group(admin, 'CS-2026', 'Computer science · first-year cohort')
    svc.set_members(admin, group, [student, student2])
    course = svc.create_course(teacher, 'Client–Server Development', 'Build reliable web applications with Python, databases, and a browser client.')
    svc.set_course_groups(admin, course, [group])
    now = utcnow().replace(second=0, microsecond=0)
    upcoming = svc.save_assignment(teacher, course, 'Design a relational schema', 'Draw an ER diagram and explain the primary and foreign keys for your deadline-monitoring application. Submit a PDF or a link to your diagram.', 100, now + timedelta(days=3), 'published')
    svc.save_assignment(teacher, course, 'Build your authentication flow', 'Implement registration, password hashing, sign-in, and role-based access. Submit your source code or a repository link.', 100, now + timedelta(days=7), 'published')
    past = svc.save_assignment(teacher, course, 'Write the project proposal', 'Describe your users, requirements, and client–server architecture in a one-page proposal.', 50, now - timedelta(days=2), 'published')
    svc.save_assignment(teacher, course, 'Final application walkthrough', 'Prepare a short demonstration of the complete application.', 100, now + timedelta(days=14), 'draft')
    sid = svc.submit(student, upcoming, 'https://example.com/schema', note='My schema design is ready for review.')
    svc.grade(teacher, sid, 92, 'Clear relationships and good constraints. Add an index to support deadline queries.')
    svc.submit(student2, upcoming, file_name='schema.txt', file_data=b'users -> groups -> courses -> assignments -> submissions -> grades\n')
    svc.submit(student, past, 'https://example.com/proposal', note='Submitted after the deadline.')
    return dict(admin=admin, teacher=teacher, student=student, second_student=student2, group=group, course=course, assignment=upcoming, overdue_assignment=past)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create explicit sample accounts and assignments in an empty database.')
    parser.add_argument('--db', default=str(Path(__file__).parent / 'data' / 'deadline_desk.sqlite3'))
    args = parser.parse_args()
    seed(args.db)
    print('Demo ready. Password for every sample account: ' + DEMO_PASSWORD)
    print('admin@example.com | teacher@example.com | student@example.com | taylor@example.com')
