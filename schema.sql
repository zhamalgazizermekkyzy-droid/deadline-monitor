PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 email TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('administrator','teacher','student')),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 failed_attempts INTEGER NOT NULL DEFAULT 0,
 locked_until TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL UNIQUE,
 description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS group_members (
 group_id INTEGER NOT NULL REFERENCES groups(id),
 student_id INTEGER NOT NULL REFERENCES users(id),
 PRIMARY KEY(group_id, student_id)
);
CREATE TABLE IF NOT EXISTS courses (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 description TEXT NOT NULL DEFAULT '',
 teacher_id INTEGER NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS course_groups (
 course_id INTEGER NOT NULL REFERENCES courses(id),
 group_id INTEGER NOT NULL REFERENCES groups(id),
 PRIMARY KEY(course_id, group_id)
);
CREATE TABLE IF NOT EXISTS assignments (
 id INTEGER PRIMARY KEY,
 course_id INTEGER NOT NULL REFERENCES courses(id),
 title TEXT NOT NULL,
 instructions TEXT NOT NULL,
 max_score REAL NOT NULL CHECK(max_score > 0),
 estimated_hours REAL NOT NULL DEFAULT 2 CHECK(estimated_hours BETWEEN 0.25 AND 160),
 deadline TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','published','closed')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
 id INTEGER PRIMARY KEY,
 assignment_id INTEGER NOT NULL REFERENCES assignments(id),
 student_id INTEGER NOT NULL REFERENCES users(id),
 attempt INTEGER NOT NULL,
 submitted_at TEXT NOT NULL,
 deadline_at_submission TEXT NOT NULL,
 timeliness TEXT NOT NULL CHECK(timeliness IN ('on_time','late')),
 link TEXT,
 file_name TEXT,
 file_data BLOB,
 note TEXT NOT NULL DEFAULT '',
 UNIQUE(assignment_id, student_id, attempt),
 CHECK(link IS NOT NULL OR file_data IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS grades (
 id INTEGER PRIMARY KEY,
 submission_id INTEGER NOT NULL UNIQUE REFERENCES submissions(id),
 grader_id INTEGER NOT NULL REFERENCES users(id),
 score REAL NOT NULL CHECK(score >= 0),
 feedback TEXT NOT NULL DEFAULT '',
 graded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_latest ON submissions(assignment_id, student_id, attempt DESC);
CREATE INDEX IF NOT EXISTS idx_assignments_course ON assignments(course_id, status);
CREATE INDEX IF NOT EXISTS idx_group_members_student ON group_members(student_id);
CREATE TABLE IF NOT EXISTS study_preferences (
 student_id INTEGER PRIMARY KEY REFERENCES users(id),
 daily_minutes INTEGER NOT NULL CHECK(daily_minutes BETWEEN 15 AND 480),
 start_time TEXT NOT NULL,
 horizon_days INTEGER NOT NULL CHECK(horizon_days BETWEEN 1 AND 14),
 language TEXT NOT NULL CHECK(language IN ('Русский','Қазақша','English')),
 goal TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS study_progress (
 student_id INTEGER NOT NULL REFERENCES users(id),
 assignment_id INTEGER NOT NULL REFERENCES assignments(id),
 remaining_minutes INTEGER NOT NULL CHECK(remaining_minutes BETWEEN 0 AND 9600),
 PRIMARY KEY(student_id,assignment_id)
);
