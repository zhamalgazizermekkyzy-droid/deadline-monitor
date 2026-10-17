# Deadline Desk — Project 6

A functional Python + Streamlit web application for assignment publication, student submissions, grading, and deadline monitoring. SQLite stores users, enrolment, assignments, files, and grades persistently.

## Mobile web version

The same application adapts to Android and iPhone browser screens. At widths of 768 pixels or less:

- A **Go to** page selector appears above the content. It stays synchronized with the sidebar navigation and offers only the pages allowed for the signed-in role.
- Assignment creation, deadline inputs, grading controls, and submission forms stack vertically.
- Buttons and interactive controls have larger touch targets. Inputs use a 16-pixel font to avoid automatic zoom while typing on iPhones.
- Dashboard metrics form a two-column grid.
- Results, group reports, rosters, and user lists display as cards with status labels. Lists show 10 records per page, with a page selector for longer lists.
- File upload controls fit the available width; students can upload from their phone's file picker or submit a link.

Wider screens show the original tables and sidebar navigation. Both layouts use the same authorized rows, report filters, accounts, deadlines, and database. CSV downloads still include all filtered records, regardless of the visible card page. Open the **☰** menu for account details, timezone, refresh, and sign out.

### Upgrade your existing application

Stop the running Streamlit server. Copy the updated **`app.py`**, **`mobile_ui.py`**, and **`styles.css`** into your existing `deadline_monitor` folder, then restart using your usual launch command. Keep your existing **`data`** folder and any `DEADLINE_DB_PATH` setting. This update does not change the database schema or business rules, and the ZIP contains no database.

### Open it on your phone

1. Start the server on your computer:

   ```bash
   python -m streamlit run app.py --server.address 0.0.0.0
   ```

2. Connect the phone and computer to the same Wi-Fi network.
3. Find the computer's Wi-Fi **IPv4 address** in its network settings (on Windows, run `ipconfig`). Open that address and port in the phone browser, for example `http://192.168.1.20:8501`. Use the computer's actual address. `localhost` on a phone refers to the phone itself; `0.0.0.0` is the server's listening address, not a URL to enter on your phone.
4. Sign in with your existing account. Allow Python's local-network connection in the computer's firewall if it blocks access.

For access outside your Wi-Fi network, deploy the app to an HTTPS host with persistent storage as described below. The mobile version is a responsive web application and requires a connection to the running server.

## Run

Requires Python 3.10 or later (tested with Python 3.11 and Streamlit 1.51).

Open a terminal in this folder:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Then install and launch:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the URL printed by Streamlit, usually **http://localhost:8501**. On Windows, `start.bat` also launches the app after dependencies are installed. The server must stay running while people use the application.

On the first launch, create the administrator account. A fresh installation contains no predefined accounts. The default database is `data/deadline_desk.sqlite3`; the directory is created automatically. Database paths are relative to the application folder, so launching from another directory does not lose your data.

To use another database, set `DEADLINE_DB_PATH` to an absolute path before starting Streamlit:

```powershell
$env:DEADLINE_DB_PATH = 'C:\path\to\school.sqlite3'
python -m streamlit run app.py
```

On macOS/Linux:

```bash
DEADLINE_DB_PATH=/path/to/school.sqlite3 python -m streamlit run app.py
```

## Optional demonstration

Before creating real accounts, populate an **empty** database:

```bash
python demo.py
python -m streamlit run app.py
```

The script refuses to alter a database that already has users. For a separate demonstration database, run `python demo.py --db /absolute/path/demo.sqlite3` and launch the app with `DEADLINE_DB_PATH` pointing at the same file.

| Role | Email | Password |
| --- | --- | --- |
| Administrator | admin@example.com | DemoPass123! |
| Teacher | teacher@example.com | DemoPass123! |
| Student | student@example.com | DemoPass123! |
| Second student | taylor@example.com | DemoPass123! |

The demo includes a course, a student group, published and draft assignments, a graded on-time submission, a file submission awaiting grading, a late submission, and missing overdue work. Dates are relative to when you run the script. Example links are placeholders; a file attachment is included for testing downloads.

## Use the application

1. **Administrator:** in **Users & access**, create teacher accounts. Students can self-register, or an administrator can create them. Create groups in **Courses & groups**, add students, create a course with a teacher, and save its enrolled groups.
2. **Teacher:** create courses and assignments. An administrator enrols groups in teacher-created courses. Set instructions, maximum score, a date/time, and **Published** status to make an assignment visible to enrolled students. Drafts are hidden. **Closed** assignments remain visible but accept no further work.
3. **Student:** sign in, open **My assignments**, attach a file or provide an HTTP/HTTPS link, and submit. Both a file and a link may be supplied. See timing, attempt history, grades, and feedback on the assignment or in **My results**.
4. **Teacher:** open **Review submissions**, inspect the latest submission or earlier attempts, download the file/open the link, and save the score and feedback. Scores must be between zero and the assignment maximum.
5. **Teacher/administrator:** open **Group reports** for course/group statistics, student summaries, and downloadable CSV detail. Missing submissions are included.

Students see only courses assigned to their groups, their own submissions, and their own grades. Teachers manage only the courses they own. Administrators manage all courses, groups, and accounts. Group creation and membership changes are reserved for administrators. Course creation is available to teachers and administrators.

Everyone can change their password in **Profile** and choose a display timezone in the sidebar. Account deactivation or role changes take effect on the next refresh or action. Saving account access also clears a temporary login lockout. Administrators cannot remove their own administrator access. Reassign owned courses or remove group membership before changing a user to an incompatible role.

## Deadline and status rules

The server records each submission time in UTC. Clients cannot supply or backdate a submission timestamp.

```text
submitted_at <= deadline_at_submission  -> On time
submitted_at >  deadline_at_submission  -> Late
```

The comparison includes microseconds. Submission exactly at the deadline is on time. Work submitted one microsecond later is late. Published assignments continue accepting late work until a teacher closes them.

Each submission retains the deadline that applied to that attempt. Changing an assignment deadline affects future attempts and the overdue status of missing work. It does not rewrite timing labels on earlier attempts.

Each revision is a separate, numbered attempt. The latest attempt determines the current submission status and grade; a revised submission needs its own grade. Older attachments, links, timing labels, scores, and feedback remain in the history. Teachers can review that history. Files are stored as SQLite BLOBs and cannot be accessed by guessing a public upload URL.

| Work status | Meaning |
| --- | --- |
| Pending | No submission; current deadline has not passed |
| Overdue | No submission; current deadline has passed |
| Submitted | Latest attempt exists and awaits a grade |
| Graded | Latest attempt has a grade, including a score of zero |

**On time/Late** is independent of work status, so a graded submission can still be late. In **My results**, **Completed** means an assignment has been submitted, with or without a grade. Publication status (draft/published/closed) is also separate. An overdue closed assignment remains overdue when work is missing.

The overview and read-only result/course/report views refresh every 60 seconds while connected. Timed refresh is disabled on form pages to avoid interrupting unsaved work; **Refresh data** updates immediately. Deadline checks run again inside every submission transaction regardless of what the browser currently displays. The default display timezone is `Asia/Bishkek`; all storage and comparisons use UTC.

Reports use the current course/group roster, include only published or closed assignments, and count each student once even when two enrolled groups share a student. They use the latest attempt for each student/assignment. Removing enrolment retains stored submissions but excludes the student from the current roster report. A student's current course/results views follow current enrolment; their historical submissions remain in the database and are available to the course teacher.

Student summary grade percentages use earned points divided by maximum points **for graded assignments only**. Ungraded/missing assignments are not silently counted as zero. Summary export covers the full selected cohort; detail export respects status/timing filters. CSV text values are escaped to prevent spreadsheet formula interpretation.

## Requirement coverage

| Required feature | Implementation |
| --- | --- |
| User registration and authentication | Student sign-up, administrator-created accounts, password hashing, role-aware sessions |
| Teacher, student, administrator roles | UI navigation and server-side authorization for all data operations |
| Creation of courses and student groups | Courses & groups; group membership and course enrolment |
| Publication of assignments | Draft, published, and closed states; create/edit forms |
| Instructions, maximum score, deadline | Assignment form with validation and timezone-aware date/time |
| Submission of a file or link | Student submission form; persistent attachment/link storage |
| Recording submission date and time | UTC server timestamp for every attempt |
| Updating assignment status | Publication status editor and computed work status |
| Grade and feedback | Review submissions; editable bounded scores and feedback |
| Completed and overdue assignments | My assignments filters, My results, overview, and reports |
| Group reports | Cohort summary, assignment detail, timing statistics, CSV exports |
| Automatic on-time or late marking | Transactional timestamp comparison with saved timing and deadline snapshot |

## Architecture and database

```text
Browser client
    | HTTP + Streamlit WebSocket session
Streamlit Python server (app.py)
    | Service calls with the authenticated session's user ID
Validation / authorization / deadline rules (service.py)
    | Parameterized SQL; transaction per mutation
SQLite database (schema.sql)
```

Streamlit provides the web server and browser client, so a separate API server is not required. The browser never connects directly to SQLite. Each operation opens its own database connection; write operations use `BEGIN IMMEDIATE`, foreign keys, a busy timeout, and WAL journaling. Creating a new attempt and classifying its timing happen in one transaction.

The six required tables are `users`, `groups`, `courses`, `assignments`, `submissions`, and `grades`. `group_members` and `course_groups` model many-to-many enrolment. A grade references a submission attempt; a submission references its student and assignment. Courses reference their teacher; assignments reference their course. See `schema.sql` for constraints and indexes.

Passwords use salted PBKDF2-HMAC-SHA256 with 600,000 rounds and constant-time verification. Five failed sign-in attempts lock an account for five minutes. Students cannot select a privileged role during registration. SQL uses parameter binding. Authorization checks re-read active account and ownership data in the service layer, including downloads and reports. File uploads are limited to 10 MB, require nonempty content, and have their filenames stripped of path components. The app stores uploads and never executes them. User content is rendered as text/Streamlit content; mobile HTML cards escape all user-supplied names, labels, and values. Custom styles come from the static `styles.css` file.

## Verification

Run from this folder:

```bash
python -m unittest discover -s tests -v
```

Tests use isolated temporary databases and do not modify application data. Service tests cover authentication, lockout, password changes, persistence, group/course isolation, permission enforcement, draft/closed states, exact deadline boundaries, timezone comparison, file validation/storage, revisions, historical deadline snapshots, missing/overdue work, grades and feedback, and report scope. Streamlit AppTest tests exercise initial setup, registration, sign-in, every role's pages, administrator enrolment forms, and publish → submit → grade → report → student feedback workflows.

The mobile update passes **31 automated tests**, including phone/sidebar navigation synchronization for every role, report card pagination and filtering, HTML escaping, zero-score display, and inactive-account labels. Actual browser viewport testing could not be completed in the build environment because local server connections timed out. Check the responsive layout on your phone after launching it locally. The CSS targets Streamlit 1.51 layout hooks; recheck styling after major Streamlit upgrades.

## Sharing and operating

For use across a LAN, launch with `python -m streamlit run app.py --server.address 0.0.0.0` and give users the host machine's URL. Keep the database and uploads on the server, not a network share. Complete first-administrator setup before sharing the URL. For internet hosting, use HTTPS through a reverse proxy and a persistent disk. There is no external mail service, password-recovery email, or background notification service in this project.

SQLite suits a classroom/single-server deployment. Use PostgreSQL plus object storage if you expand to multiple app-server instances or large file volumes. Back up the SQLite database consistently (for example using Python's `sqlite3.Connection.backup` while the app is running); files are included because uploads live inside the database. Do not copy only the main SQLite file while ignoring an active WAL file. A browser reload creates a new Streamlit session and may require sign-in again.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Streamlit interface, forms, dashboards, reports, navigation |
| `mobile_ui.py` | Escaped HTML cards for mobile record lists |
| `styles.css` | Desktop styling and responsive phone/tablet layouts |
| `service.py` | Business rules, security checks, authentication, persistence |
| `schema.sql` | Required tables, enrolment tables, constraints, indexes |
| `demo.py` | Optional sample data for an empty database |
| `tests/test_service.py` | Database and business-rule tests |
| `tests/test_ui.py` | Streamlit form/workflow tests |
| `tests/test_mobile_ui.py` | Mobile card escaping and value/status display tests |
| `.streamlit/config.toml` | Theme, upload size, XSRF/CORS settings |
| `requirements.txt` | Python dependencies |
| `start.bat` | Windows launch shortcut |

Streamlit API and testing references: [forms](https://docs.streamlit.io/develop/concepts/architecture/forms), [AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest).
