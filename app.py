"""Deadline Desk — launch with: python -m streamlit run app.py"""
from __future__ import annotations

import csv
from html import escape
import io
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from service import AppError, Service, parse_timestamp, utcnow
from mobile_ui import record_cards
from ai_planner import MAX_TASKS, generate_plan, input_fingerprint

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('DEADLINE_DB_PATH', str(ROOT / 'data' / 'deadline_desk.sqlite3')))
st.set_page_config(page_title='Deadline Desk', page_icon='📘', layout='wide', initial_sidebar_state='auto')
sidebar_slot = st.sidebar.empty()

st.markdown('<style>' + (ROOT / 'styles.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)
main_slot = st.empty()


@st.cache_resource
def service(path, schema_revision='ai_planner_v1'):
    return Service(path)


svc = service(str(DB_PATH))


def perform(action, message):
    try:
        result = action()
    except AppError as exc:
        st.error(str(exc))
        return False
    st.session_state['flash'] = message
    st.rerun()
    return result


def show_flash():
    # Keep element positions stable across form-save reruns.
    notice = st.empty()
    if 'flash' in st.session_state:
        notice.success(st.session_state.pop('flash'))


def page_header(title, description, eyebrow='A LITTLE CLARITY, EVERY DAY'):
    with st.container(key='page_header'):
        st.markdown('<div class="eyebrow">' + escape(eyebrow) + '</div>', unsafe_allow_html=True)
        st.title(title)
        st.write(description)


def local_time(value):
    return parse_timestamp(value).astimezone(ZoneInfo(st.session_state.get('timezone', 'Asia/Bishkek')))


def format_time(value):
    return local_time(value).strftime('%d %b %Y · %H:%M %Z') if value else '—'


def deadline_editor(prefix, default=None):
    tz = ZoneInfo(st.session_state.get('timezone', 'Asia/Bishkek'))
    default = local_time(default) if default else (utcnow() + timedelta(days=7)).astimezone(tz)
    a, b = st.columns(2)
    date = a.date_input('Deadline date', value=default.date(), key=f'{prefix}_date')
    time = b.time_input('Deadline time', value=default.time().replace(tzinfo=None), step=60, key=f'{prefix}_time')
    st.caption(f'Deadline interpreted in {tz.key}. Submission timestamps are recorded by the server.')
    return datetime.combine(date, time, tzinfo=tz)


def all_rows(user):
    return [row for course in svc.courses(user['id']) for row in svc.report(user['id'], course['id'])]


def nice_table(rows, columns=None, key='records'):
    if not rows:
        st.info('No records yet.')
        return
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame[columns]
    with st.container(key=f'wide_records_{key}'):
        st.dataframe(frame, hide_index=True, width='stretch')
    # The two views use the same authorized, filtered rows. CSS chooses by width.
    with st.container(key=f'phone_records_{key}'):
        count = len(frame)
        page_count = (count + 9) // 10
        page_index = st.selectbox('Records page', range(page_count),
                                  format_func=lambda index: f'Page {index + 1} of {page_count}',
                                  key=f'record_page_{key}') if page_count > 1 else 0
        start = page_index * 10
        st.caption(f'{start + 1}–{min(start + 10, count)} of {count} records')
        st.markdown(record_cards(frame.iloc[start:start + 10].to_dict(orient='records')), unsafe_allow_html=True)


def report_table(rows):
    return [dict(Student=r['student'], Course=r['course'], Assignment=r['assignment'],
                 Deadline=format_time(r['deadline']), Status=r['status'],
                 Timing={'on_time': 'On time', 'late': 'Late'}.get(r['timeliness'], '—'),
                 Submitted=format_time(r['submitted_at']),
                 Score=f'{r["score"]:g} / {r["max_score"]:g}' if r['score'] is not None else '—') for r in rows]


def csv_data(rows):
    if not rows:
        return b''
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        # Protect exported text from spreadsheet formula injection.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else v) for k, v in row.items()})
    return buffer.getvalue().encode('utf-8-sig')


def download_submission(user, attempt):
    if attempt['link']:
        st.link_button(f'Open link · submission #{attempt["id"]}', attempt['link'])
    if attempt['file_name']:
        attachment = svc.submission(user['id'], attempt['id'])
        st.download_button('Download ' + attempt['file_name'], attachment['file_data'], file_name=attempt['file_name'],
                           mime='application/octet-stream', key=f'file_{attempt["id"]}')
    if attempt['note']:
        st.text(attempt['note'])


def auth_screen():
    page_header('Every assignment. Every deadline.', 'Less rushing. More room to learn. Bring your courses, submissions, and next steps together.', 'WELCOME TO DEADLINE DESK')
    left, right = st.columns([1.35, 1], gap='large')
    with right:
        with st.container(border=True):
            st.subheader('A clear path from assigned to graded')
            st.write('**01 · Publish** — instructions, points, and a deadline.')
            st.write('**02 · Submit** — upload a file or share a link.')
            st.write('**03 · Review** — grades, feedback, and group reports.')
            st.caption('On-time and late labels are calculated automatically using the server clock.')
    with left:
        if not svc.has_users():
            st.subheader('Set up your workspace')
            st.info('Create the first administrator account. This setup is available only while the database is empty.')
            with st.form('setup'):
                name = st.text_input('Full name', key='setup_name')
                email = st.text_input('Email', key='setup_email')
                password = st.text_input('Password', type='password', key='setup_pw')
                confirm = st.text_input('Confirm password', type='password', key='setup_confirm')
                if st.form_submit_button('Create administrator', type='primary'):
                    if password != confirm:
                        st.error('Passwords do not match.')
                    else:
                        perform(lambda: svc.bootstrap(name, email, password), 'Administrator created. Sign in to continue.')
            return
        login, register = st.tabs(['Sign in', 'Create student account'])
        with login:
            with st.form('login'):
                email = st.text_input('Email', key='login_email')
                password = st.text_input('Password', type='password', key='login_pw')
                if st.form_submit_button('Sign in', type='primary', width='stretch'):
                    try:
                        user = svc.authenticate(email, password)
                        st.session_state['user_id'] = user['id']
                        st.rerun()
                    except AppError as exc:
                        st.error(str(exc))
        with register:
            st.caption('Students can register here. Your administrator assigns your group and courses.')
            with st.form('registration'):
                name = st.text_input('Full name', key='register_name')
                email = st.text_input('Email', key='register_email')
                password = st.text_input('Password (10–128 characters)', type='password', key='register_pw')
                confirm = st.text_input('Confirm password', type='password', key='register_confirm')
                if st.form_submit_button('Create account', type='primary'):
                    if password != confirm:
                        st.error('Passwords do not match.')
                    else:
                        perform(lambda: svc.register(name, email, password), 'Student account created. You can now sign in.')


def dashboard(user):
    page_header('Workspace overview', 'Your courses, deadlines, and next steps — all in one place.', 'YOUR DAY, WITH A LITTLE MORE CLARITY')
    rows = all_rows(user)
    student = user['role'] == 'student'
    with st.container(key='overview_metrics'):
        cols = st.columns(4)
        cols[0].metric('Your courses' if student else 'Courses', len(svc.courses(user['id'])))
        cols[1].metric('Awaiting your work' if student else 'Awaiting grading', sum(r['status'] == ('Pending' if student else 'Submitted') for r in rows))
        cols[2].metric('Overdue work', sum(r['status'] == 'Overdue' for r in rows))
        cols[3].metric('Graded', sum(r['status'] == 'Graded' for r in rows))
    st.write('')
    a, b = st.columns([1.65, 1], gap='large')
    with a:
        st.subheader('Coming up')
        assignments = [r for r in svc.assignments(user['id']) if r['status'] == 'published' and parse_timestamp(r['deadline']) >= utcnow()]
        if not assignments:
            st.info('No upcoming deadlines. Published assignments will appear here.')
        for assignment in assignments[:6]:
            with st.container(border=True):
                st.write('**' + assignment['title'] + '**')
                st.caption(assignment['course_name'] + ' · ' + format_time(assignment['deadline']))
    with b:
        st.subheader('Submission timing')
        submitted = [r for r in rows if r['submission_id']]
        ontime = sum(r['timeliness'] == 'on_time' for r in submitted)
        with st.container(border=True):
            st.metric('On-time rate', f'{ontime / len(submitted):.0%}' if submitted else '—')
            st.write(f'{ontime} on time · {len(submitted) - ontime} late')
            st.caption('Based on the latest attempt for each student and assignment.')
        st.subheader('Needs attention')
        overdue = [r for r in rows if r['status'] == 'Overdue']
        if overdue:
            nice_table(report_table(overdue[:8]), ['Student', 'Assignment', 'Deadline'], key='overview_overdue')
        else:
            st.success('No missing overdue work in your current courses.')
    if not svc.courses(user['id']):
        st.info('Ask your administrator to enrol your group in a course.' if student else 'Create a course in Courses & groups to get started.')


def assignment_form(user, courses, existing=None):
    prefix = f'edit_{existing["id"]}' if existing else 'new_assignment'
    course_map = {c['id']: c for c in courses}
    with st.form(prefix):
        course_id = st.selectbox('Course', list(course_map), format_func=lambda cid: course_map[cid]['name'],
                                 index=list(course_map).index(existing['course_id']) if existing else 0,
                                 disabled=bool(existing), key=prefix + '_course')
        title = st.text_input('Assignment title', value=existing['title'] if existing else '', key=prefix + '_title', max_chars=200)
        instructions = st.text_area('Instructions', value=existing['instructions'] if existing else '', height=160, key=prefix + '_instructions')
        c1, c2 = st.columns(2)
        score = c1.number_input('Maximum score', min_value=0.01, max_value=1000000.0, value=float(existing['max_score']) if existing else 100.0, key=prefix + '_score')
        status = c2.selectbox('Publication status', ['draft', 'published', 'closed'], index=['draft', 'published', 'closed'].index(existing['status']) if existing else 0,
                              format_func=str.title, key=prefix + '_status')
        effort = st.number_input('Estimated work (hours)', min_value=0.25, max_value=160.0, step=0.25,
                                 value=float(existing.get('estimated_hours', 2.0)) if existing else 2.0,
                                 key=prefix + '_effort', help='Used by the study planner. Students can adjust their remaining work.')
        deadline = deadline_editor(prefix, existing['deadline'] if existing else None)
        st.caption('Draft: hidden from students. Published: accepts work, including late work. Closed: visible, accepts no new work.')
        if existing:
            st.caption('Deadline edits affect future attempts and missing-work status. Earlier attempts retain their recorded deadline and timing label.')
        if st.form_submit_button('Save changes' if existing else 'Create assignment', type='primary'):
            perform(lambda: svc.save_assignment(user['id'], course_id, title, instructions, score, deadline, status, existing['id'] if existing else None, estimated_hours=effort), 'Assignment saved.')


def filter_assignments(user, assignments):
    courses = svc.courses(user['id'])
    course_map = {c['id']: c['name'] for c in courses}
    a, b = st.columns([2, 1])
    selected = a.selectbox('Course filter', [None] + list(course_map), format_func=lambda cid: course_map[cid] if cid else 'All courses', key='assignment_course_filter')
    status = b.selectbox('Publication filter', ['All', 'Published', 'Closed'] + (['Draft'] if user['role'] != 'student' else []))
    return [r for r in assignments if (selected is None or r['course_id'] == selected) and (status == 'All' or r['status'] == status.lower())]


def assignment_page(user):
    student = user['role'] == 'student'
    page_header('My assignments' if student else 'Assignments', 'One step closer. Find your next assignment and share your work.' if student else 'Give every assignment a clear brief, a thoughtful deadline, and room to succeed.', 'MAKE SPACE FOR GOOD WORK')
    courses = svc.courses(user['id'])
    if not courses:
        st.info('No courses yet. ' + ('Your administrator needs to enrol your group.' if student else 'Create a course first.'))
        return
    if not student:
        with st.expander('＋ Create assignment'):
            assignment_form(user, courses)
    assignments = filter_assignments(user, svc.assignments(user['id']))
    rows = {r['assignment_id']: r for r in all_rows(user)} if student else {}
    if student:
        view = st.selectbox('Work status', ['All', 'Pending', 'Overdue', 'Submitted', 'Graded'])
        if view != 'All':
            assignments = [a for a in assignments if rows.get(a['id'], {}).get('status') == view]
    if not assignments:
        st.info('No assignments match these filters.')
    for assignment in assignments:
        aid = assignment['id']
        with st.container(border=True):
            heading, metadata = st.columns([3, 1])
            heading.subheader(assignment['title'])
            heading.caption(assignment['course_name'] + ' · ' + assignment['status'].title())
            metadata.write(f'**{assignment["max_score"]:g} points**')
            metadata.caption(f'Estimated work: {assignment["estimated_hours"]:g} hours')
            metadata.caption('Due ' + format_time(assignment['deadline']))
            st.text(assignment['instructions'])
            if student:
                row = rows.get(aid)
                if row:
                    timing = ' · ' + ('On time' if row['timeliness'] == 'on_time' else 'Late') if row['timeliness'] else ''
                    st.write('**' + row['status'] + timing + '**')
                    if row['score'] is not None:
                        st.success(f'Grade: {row["score"]:g} / {row["max_score"]:g}')
                        if row['feedback']:
                            st.text('Feedback: ' + row['feedback'])
                if assignment['status'] == 'published':
                    with st.expander('Submit or revise your work'):
                        st.caption('Each revision is a new attempt. The latest attempt determines current status and grade. All earlier work is retained.')
                        if utcnow() > parse_timestamp(assignment['deadline']):
                            st.warning('The deadline has passed. Your new attempt will be marked late.')
                        with st.form(f'submit_{aid}', clear_on_submit=True):
                            file = st.file_uploader('Attach a file (up to 10 MB)', key=f'upload_{aid}')
                            link = st.text_input('Or provide a link (http/https)', key=f'submit_link_{aid}')
                            note = st.text_area('Note to your teacher (optional)', key=f'submit_note_{aid}')
                            if st.form_submit_button('Submit work', type='primary'):
                                perform(lambda: svc.submit(user['id'], aid, link, file.name if file else None, file.getvalue() if file else None, note), 'Submission saved. Its timing was calculated automatically.')
                else:
                    st.caption('Submissions are closed.')
                attempts = svc.attempts(user['id'], aid)
                if attempts:
                    with st.expander(f'Your submission history · {len(attempts)} attempt(s)'):
                        for attempt in attempts:
                            st.write(f'**Attempt {attempt["attempt"]} · {"On time" if attempt["timeliness"] == "on_time" else "Late"}**')
                            st.caption('Submitted ' + format_time(attempt['submitted_at']) + ' · Deadline then ' + format_time(attempt['deadline_at_submission']))
                            download_submission(user, attempt)
                            if attempt['score'] is not None:
                                st.write(f'Grade: {attempt["score"]:g} / {assignment["max_score"]:g}')
                                st.text(attempt['feedback'] or 'No written feedback.')
                            st.divider()
            else:
                with st.expander('Edit assignment'):
                    assignment_form(user, courses, assignment)


def review_page(user):
    page_header('Review submissions', 'Thoughtful feedback helps the next attempt shine.', 'TURN WORK INTO PROGRESS')
    rows = [r for r in all_rows(user) if r['submission_id']]
    course_map = {c['id']: c['name'] for c in svc.courses(user['id'])}
    a, b, c = st.columns(3)
    cid = a.selectbox('Course', [None] + list(course_map), format_func=lambda value: course_map.get(value, 'All courses'), key='review_course')
    state = b.selectbox('Review status', ['Awaiting grading', 'All submissions', 'Graded'])
    timing = c.selectbox('Timing', ['All', 'On time', 'Late'])
    rows = [r for r in rows if (cid is None or cid == r['course_id']) and (state == 'All submissions' or r['status'] == ('Submitted' if state == 'Awaiting grading' else 'Graded')) and (timing == 'All' or r['timeliness'] == ('on_time' if timing == 'On time' else 'late'))]
    if not rows:
        st.info('No submissions match these filters.')
        return
    mapping = {r['submission_id']: r for r in rows}
    selected = st.selectbox('Select submission', list(mapping), format_func=lambda sid: f'{mapping[sid]["student"]} / {mapping[sid]["assignment"]} / attempt {mapping[sid]["attempt"]}')
    row = mapping[selected]
    attempt = svc.submission(user['id'], selected)
    with st.container(border=True):
        st.subheader(row['assignment'])
        st.write('**' + row['student'] + '** · ' + row['email'])
        st.caption(row['course'] + ' · Submitted ' + format_time(attempt['submitted_at']))
        st.write('**' + ('On time' if row['timeliness'] == 'on_time' else 'Late') + '** · Deadline at submission: ' + format_time(attempt['deadline_at_submission']))
        download_submission(user, attempt)
        with st.form(f'grade_{selected}'):
            score = st.number_input(f'Score (out of {row["max_score"]:g})', min_value=0.0, max_value=float(row['max_score']), value=float(row['score']) if row['score'] is not None else 0.0)
            feedback = st.text_area('Feedback', value=row['feedback'] or '', height=140)
            if st.form_submit_button('Save grade & feedback', type='primary'):
                perform(lambda: svc.grade(user['id'], selected, score, feedback), 'Grade and feedback saved.')
    history = svc.attempts(user['id'], row['assignment_id'], row['student_id'])
    if len(history) > 1:
        with st.expander('Earlier attempts'):
            for old in history:
                if old['id'] == selected:
                    continue
                st.write(f'**Attempt {old["attempt"]} · {old["timeliness"].replace("_", " ").title()}**')
                st.caption(format_time(old['submitted_at']))
                download_submission(user, old)
                if old['score'] is not None:
                    st.write(f'Previous grade: {old["score"]:g}')
                    st.text(old['feedback'] or 'No written feedback.')
                st.divider()


def courses_page(user):
    admin = user['role'] == 'administrator'
    student = user['role'] == 'student'
    page_header('My courses' if student else 'Courses & groups', 'A shared space for your subjects, people, and possibilities.', 'CONNECTED BY LEARNING')
    courses = svc.courses(user['id'])
    groups = svc.groups(user['id'])
    users = svc.list_users(user['id']) if admin else []
    if not student:
        with st.expander('＋ Create course'):
            with st.form('new_course'):
                name = st.text_input('Course name')
                description = st.text_area('Description')
                instructors = {u['id']: u for u in users if u['role'] in ('teacher', 'administrator') and u['active']}
                teacher_id = st.selectbox('Teacher', list(instructors), format_func=lambda uid: instructors[uid]['name']) if admin else user['id']
                if st.form_submit_button('Create course', type='primary'):
                    perform(lambda: svc.create_course(user['id'], name, description, teacher_id), 'Course created. An administrator can now enrol groups.')
    for course in courses:
        with st.container(border=True):
            st.subheader(course['name'])
            st.caption('Teacher: ' + course['teacher_name'])
            st.text(course['description'] or 'No course description.')
            enrolled = svc.course_group_ids(user['id'], course['id'])
            st.write('Groups: ' + (', '.join(g['name'] for g in groups if g['id'] in enrolled) or 'No enrolled groups yet'))
            if admin:
                with st.form(f'course_groups_{course["id"]}'):
                    gm = {g['id']: g for g in groups}
                    selected_groups = st.multiselect('Enrolled groups', list(gm), default=enrolled, format_func=lambda gid: gm[gid]['name'], key=f'enrol_{course["id"]}')
                    if st.form_submit_button('Save enrolment'):
                        perform(lambda: svc.set_course_groups(user['id'], course['id'], selected_groups), 'Course enrolment updated.')
                with st.expander('Reassign teacher'):
                    owners = {u['id']: u for u in users if u['role'] in ('teacher', 'administrator') and (u['active'] or u['id'] == course['teacher_id'])}
                    with st.form(f'owner_{course["id"]}'):
                        owner = st.selectbox('New teacher', list(owners), index=list(owners).index(course['teacher_id']), format_func=lambda uid: owners[uid]['name'])
                        if st.form_submit_button('Save teacher'):
                            perform(lambda: svc.reassign_course(user['id'], course['id'], owner), 'Course teacher updated.')
    if not courses:
        st.info('No courses yet.')
    if student:
        return
    st.subheader('Student groups')
    if admin:
        with st.expander('＋ Create group'):
            with st.form('new_group'):
                name = st.text_input('Group name')
                description = st.text_area('Group description')
                if st.form_submit_button('Create group', type='primary'):
                    perform(lambda: svc.create_group(user['id'], name, description), 'Group created.')
    if not groups:
        st.info('No groups yet. Administrators create groups and assign students.')
    for group in groups:
        with st.expander(group['name']):
            st.text(group['description'] or 'No group description.')
            members = svc.members(user['id'], group['id'])
            nice_table(members, ['name', 'email', 'active'], key=f'group_{group["id"]}')
            if admin:
                students = {u['id']: u for u in users if u['role'] == 'student'}
                with st.form(f'members_{group["id"]}'):
                    selected = st.multiselect('Students in this group', list(students), default=[m['id'] for m in members], format_func=lambda uid: students[uid]['name'] + ' · ' + students[uid]['email'], key=f'members_selection_{group["id"]}')
                    st.caption('Removing enrolment hides the course from that student. Existing submissions remain stored.')
                    if st.form_submit_button('Save group members'):
                        perform(lambda: svc.set_members(user['id'], group['id'], selected), 'Group members updated.')


def reports_page(user):
    page_header('Group reports', 'See the bigger picture, spot overdue work, and celebrate progress.', 'SMALL DETAILS. A CLEARER PICTURE.')
    courses = svc.courses(user['id'])
    if not courses:
        st.info('Create a course and enrol groups to generate a report.')
        return
    cm = {c['id']: c for c in courses}
    a, b = st.columns(2)
    cid = a.selectbox('Course', list(cm), format_func=lambda value: cm[value]['name'], key='report_course')
    enrolled = svc.course_group_ids(user['id'], cid)
    groups = {g['id']: g for g in svc.groups(user['id']) if g['id'] in enrolled}
    gid = b.selectbox('Group', [None] + list(groups), format_func=lambda value: groups[value]['name'] if value else 'All enrolled groups', key=f'report_group_{cid}')
    rows = svc.report(user['id'], cid, gid)
    if not rows:
        st.info('This report needs enrolled students and at least one published or closed assignment.')
        return
    submitted = sum(bool(r['submission_id']) for r in rows)
    with st.container(key='report_metrics'):
        cols = st.columns(4)
        cols[0].metric('Students', len({r['student_id'] for r in rows}))
        cols[1].metric('Submission rate', f'{submitted / len(rows):.0%}')
        cols[2].metric('Missing & overdue', sum(r['status'] == 'Overdue' for r in rows))
        cols[3].metric('Late submissions', sum(r['timeliness'] == 'late' for r in rows))
    summaries = []
    for sid in dict.fromkeys(r['student_id'] for r in rows):
        student_rows = [r for r in rows if r['student_id'] == sid]
        graded = [r for r in student_rows if r['score'] is not None]
        summaries.append(dict(Student=student_rows[0]['student'], Assignments=len(student_rows), Submitted=sum(bool(r['submission_id']) for r in student_rows), Graded=len(graded), Overdue=sum(r['status'] == 'Overdue' for r in student_rows), Late=sum(r['timeliness'] == 'late' for r in student_rows), **{'Earned points': sum(r['score'] for r in graded), 'Graded maximum': sum(r['max_score'] for r in graded), 'Grade %': round(sum(r['score'] for r in graded) / sum(r['max_score'] for r in graded) * 100, 1) if graded else None}))
    st.subheader('Student summary')
    nice_table(summaries, key='report_summary')
    st.download_button('Download student summary CSV', csv_data(summaries), file_name=f'course_{cid}_summary.csv', mime='text/csv')
    st.subheader('Assignment detail')
    a, b = st.columns(2)
    status = a.selectbox('Status', ['All', 'Pending', 'Overdue', 'Submitted', 'Graded'], key='report_status')
    timing = b.selectbox('Timing', ['All', 'On time', 'Late', 'No submission'], key='report_timing')
    filtered = [r for r in rows if (status == 'All' or r['status'] == status) and (timing == 'All' or (timing == 'No submission' and not r['submission_id']) or r['timeliness'] == {'On time': 'on_time', 'Late': 'late'}.get(timing, 'not-a-value'))]
    nice_table(report_table(filtered), key='report_detail')
    export = [{**r, 'deadline': format_time(r['deadline']), 'submitted_at': format_time(r['submitted_at'])} for r in filtered]
    st.download_button('Download detail CSV', csv_data(export), file_name=f'course_{cid}_detail.csv', mime='text/csv', disabled=not filtered)
    st.caption('Summary includes the full selected course/group. Detail export follows the status and timing filters. Grade % uses only graded assignments; missing grades are not treated as zero.')


def results_page(user):
    page_header('My results', 'Your effort, your feedback, and everything you are building toward.', 'PROGRESS LOOKS GOOD ON YOU')
    rows = all_rows(user)
    filter_by = st.selectbox('Show', ['All', 'Completed', 'Graded', 'Overdue', 'Pending'])
    rows = [r for r in rows if filter_by == 'All' or (filter_by == 'Completed' and r['submission_id']) or r['status'] == filter_by]
    nice_table(report_table(rows), key='results')
    for row in rows:
        if row['score'] is not None:
            with st.expander(row['assignment'] + ' · Feedback'):
                st.write(f'{row["score"]:g} / {row["max_score"]:g} points')
                st.text(row['feedback'] or 'No written feedback yet.')
    export = [{**r, 'deadline': format_time(r['deadline']), 'submitted_at': format_time(r['submitted_at'])} for r in rows]
    if export:
        st.download_button('Download my results CSV', csv_data(export), file_name='my_results.csv', mime='text/csv')


def users_page(user):
    page_header('Users & access', 'Help the right people find their place in the workspace.', 'YOUR LEARNING COMMUNITY')
    users = svc.list_users(user['id'])
    nice_table(users, ['id', 'name', 'email', 'role', 'active'], key='users')
    with st.expander('＋ Create user'):
        with st.form('new_user'):
            name = st.text_input('Full name')
            email = st.text_input('Email')
            password = st.text_input('Initial password (10–128 characters)', type='password')
            role = st.selectbox('Role', ['student', 'teacher', 'administrator'])
            if st.form_submit_button('Create user', type='primary'):
                perform(lambda: svc.create_user(user['id'], name, email, password, role), 'User created. Share the initial password privately and ask them to change it in Profile.')
    st.subheader('Manage account')
    um = {u['id']: u for u in users}
    uid = st.selectbox('User', list(um), format_func=lambda value: um[value]['name'] + ' · ' + um[value]['email'])
    selected = um[uid]
    with st.form(f'manage_user_{uid}'):
        role = st.selectbox('Role', ['student', 'teacher', 'administrator'], index=['student', 'teacher', 'administrator'].index(selected['role']))
        active = st.checkbox('Account active', value=bool(selected['active']))
        st.caption('Role or activation changes apply on the next page refresh. Saving also clears a login lockout.')
        if st.form_submit_button('Save account'):
            perform(lambda: svc.update_user(user['id'], uid, role, active), 'Account updated.')


def ai_configuration():
    # Credentials live on the server, never in a widget, database, or plan export.
    key = os.environ.get('OPENAI_API_KEY', '')
    model = os.environ.get('OPENAI_MODEL', '')
    if not key or not model:
        try:
            key = key or st.secrets.get('OPENAI_API_KEY', '')
            model = model or st.secrets.get('OPENAI_MODEL', '')
        except FileNotFoundError:
            pass
        except Exception:
            raise AppError('AI settings could not be read. Ask your administrator to check .streamlit/secrets.toml.') from None
    return str(key).strip(), str(model).strip() or 'gpt-4.1-mini'


def ai_planner_page(user):
    page_header('AI study planner', 'Make room for what matters. Turn your workload into small, manageable steps.', 'A CALMER WAY TO GET THERE')
    tasks = svc.study_tasks(user['id'])
    preferences = svc.study_preferences(user['id'])
    timezone = st.session_state.get('timezone', 'Asia/Bishkek')
    api_key, model = ai_configuration()
    with st.expander('Your time & remaining work', expanded=True):
        with st.form('study_settings'):
            a, b = st.columns(2)
            daily = a.number_input('Daily study time (minutes)', min_value=15, max_value=480,
                                   value=preferences['daily_minutes'], step=15, key='study_daily')
            start = b.time_input('Study start time', value=datetime.strptime(preferences['start_time'], '%H:%M').time(),
                                 step=900, key='study_start')
            a, b = st.columns(2)
            horizon = a.number_input('Planning horizon (days)', min_value=1, max_value=14,
                                      value=preferences['horizon_days'], key='study_horizon')
            language = b.selectbox('Plan language', ['English'],
                                    index=0, disabled=True, key='study_language')
            goal = st.text_area('What would help you focus?', value=preferences['goal'], max_chars=1000,
                                placeholder='For example: break larger assignments into small, manageable steps.', key='study_goal')
            edited = None
            if tasks:
                frame = pd.DataFrame([{'ID': t['id'], 'Assignment': t['title'], 'Course': t['course'],
                                       'Deadline': format_time(t['deadline']), 'Hours remaining': t['remaining_minutes'] / 60} for t in tasks])
                edited = st.data_editor(frame, hide_index=True, width='stretch', num_rows='fixed', key='study_effort_editor',
                                        disabled=['ID', 'Assignment', 'Course', 'Deadline'],
                                        column_config={'ID': None, 'Hours remaining': st.column_config.NumberColumn(
                                            'Hours remaining', min_value=0.0, max_value=160.0, step=0.25, required=True)})
                st.caption('Estimate the work you still need to do. Set 0 hours when it is ready, then submit it from My assignments.')
            st.caption(f'Times are shown in {timezone}. Study sessions end by midnight. Work estimates are approximate.')
            if st.form_submit_button('Save preferences', type='primary'):
                try:
                    progress = {int(r['ID']): round(float(r['Hours remaining']) * 60) for r in edited.to_dict(orient='records')} if edited is not None else {}
                    perform(lambda: svc.save_study_settings(user['id'], int(daily), start.strftime('%H:%M'), int(horizon), language, goal, progress), 'Your planning preferences have been saved.')
                except (TypeError, ValueError, OverflowError):
                    st.error('Enter a valid number of remaining hours.')
    if not tasks:
        st.success('You are all caught up. New work will appear here when it is published.')
        return
    ready = [t for t in tasks if t['remaining_minutes'] == 0]
    for task in ready:
        st.info('Ready to submit: ' + task['title'] + '. Open My assignments to send your work.')
    pending = [t for t in tasks if t['remaining_minutes'] > 0]
    if not pending:
        return
    task_map = {t['id']: t for t in pending}
    selected_ids = st.multiselect('Assignments to plan', list(task_map), default=list(task_map)[:MAX_TASKS],
                                  format_func=lambda aid: task_map[aid]['title'] + ' · ' + task_map[aid]['course'], key='study_selected')
    selected = [task_map[aid] for aid in selected_ids]
    st.caption(f'{len(selected)} of {len(pending)} assignments selected. Plan up to {MAX_TASKS} at a time. Save your preferences above before generating.')
    if not api_key:
        st.info('Your planner is ready to connect. Ask your administrator to add an OpenAI API key to enable generation.')
        with st.expander('Connect your planner'):
            st.write('Create a key in your OpenAI account. Copy .streamlit/secrets.example.toml to .streamlit/secrets.toml in the project folder, set OPENAI_API_KEY, and restart the app.')
            st.link_button('Open API key settings', 'https://platform.openai.com/api-keys')
    st.caption('Generating a plan sends your selected assignment details, deadlines, time estimates, and planning goal to OpenAI. Usage is charged to the API key owner’s account.')
    consent = st.checkbox('I agree to share these details with OpenAI to create my study plan', key='study_consent')
    if st.button('Create my study plan', type='primary', width='stretch', key='generate_study_plan',
                 disabled=not api_key or not consent or not 1 <= len(selected) <= MAX_TASKS):
        now = svc.clock()
        last_request = st.session_state.get('study_last_request', 0)
        if time.monotonic() - last_request < 15:
            st.warning('Please wait 15 seconds before creating another plan.')
        else:
            st.session_state['study_last_request'] = time.monotonic()
            try:
                with st.spinner('Finding your next steps and checking your schedule…'):
                    plan = generate_plan(selected, preferences, timezone, now, api_key, model)
                st.session_state['study_plan'] = plan
            except AppError as exc:
                st.error(str(exc))
    plan = st.session_state.get('study_plan')
    if not plan:
        return
    if plan['source_fingerprint'] != input_fingerprint(selected, preferences, timezone, svc.clock()):
        st.warning('Your assignments, preferences, or planning date have changed. Create a fresh plan to stay up to date.')
        return
    st.subheader('Your study plan')
    st.caption('Created ' + format_time(plan['generated_at']) + ' · ' + plan['model'])
    st.text(plan['summary'])
    a, b, c = st.columns(3)
    a.metric('Work remaining', f'{plan["requested_minutes"] / 60:g} h')
    b.metric('Available time', f'{plan["available_minutes"] / 60:g} h')
    c.metric('Scheduled time', f'{plan["scheduled_minutes"] / 60:g} h')
    for task in selected:
        if parse_timestamp(task['deadline']) < svc.clock():
            st.warning('Already overdue: ' + task['title'] + '. These sessions help you catch up.')
    for missing in plan['unscheduled']:
        st.warning(f'There is not enough room for {missing["minutes"] / 60:g} h of work on “{missing["title"]}”. Add study time, extend your planning horizon, or discuss the deadline with your teacher.')
    if plan['blocks']:
        days = dict.fromkeys(local_time(block['start']).date().isoformat() for block in plan['blocks'])
        for day in days:
            blocks = sorted([b for b in plan['blocks'] if local_time(b['start']).date().isoformat() == day], key=lambda b: b['start'])
            with st.expander(day + f' · {sum(b["minutes"] for b in blocks)} min', expanded=True):
                for block in blocks:
                    st.write('**' + local_time(block['start']).strftime('%H:%M') + '–' + local_time(block['end']).strftime('%H:%M') + ' · ' + str(block['minutes']) + ' min**')
                    st.text(block['title'] + ' · ' + block['course'])
                    st.text(block['step'])
                    st.divider()
        export = [{'Date': local_time(b['start']).date().isoformat(), 'Start': format_time(b['start']), 'End': format_time(b['end']),
                   'Assignment': b['title'], 'Course': b['course'], 'Step': b['step'], 'Minutes': b['minutes'],
                   'Already overdue': b['overdue']} for b in sorted(plan['blocks'], key=lambda b: b['start'])]
        st.download_button('Download schedule CSV', csv_data(export), file_name='ai_study_plan.csv', mime='text/csv', key='study_csv')
    with st.expander('The thinking behind your plan'):
        for item in plan['priorities']:
            st.write('**' + task_map[item['assignment_id']]['title'] + '**')
            st.text(item['reason'])
    st.download_button('Download full plan JSON', json.dumps(plan, ensure_ascii=False, indent=2),
                       file_name='ai_study_plan.json', mime='application/json', key='study_json')
    st.caption('This plan is a recommendation. Update your remaining hours as you go and create a fresh plan when your workload changes. Your deadlines, grades, and submission status stay under your course’s rules.')


def profile_page(user):
    page_header('Profile', 'A few personal details for a workspace that feels like yours.', 'YOUR OWN CORNER')
    st.write('**' + user['name'] + '**')
    st.caption(user['email'] + ' · ' + user['role'].title())
    with st.form('change_password', clear_on_submit=True):
        st.subheader('Change password')
        old = st.text_input('Current password', type='password')
        new = st.text_input('New password (10–128 characters)', type='password')
        confirm = st.text_input('Confirm new password', type='password')
        if st.form_submit_button('Update password', type='primary'):
            if new != confirm:
                st.error('Passwords do not match.')
            else:
                perform(lambda: svc.change_password(user['id'], old, new), 'Password updated.')
    st.caption('Use the sidebar timezone selector to change how deadlines and submission times are displayed.')


if 'user_id' not in st.session_state:
    with main_slot.container():
        show_flash()
        auth_screen()
    st.stop()

try:
    current_user = svc.user(st.session_state['user_id'])
except AppError:
    st.session_state.clear()
    st.rerun()

# Authentication widgets are no longer rendered; discard password widget values.
for auth_key in ('login_pw', 'register_pw', 'register_confirm', 'setup_pw', 'setup_confirm'):
    st.session_state.pop(auth_key, None)

pages = ['Overview', 'My assignments', 'AI study planner', 'My results', 'My courses', 'Profile'] if current_user['role'] == 'student' else ['Overview', 'Assignments', 'Review submissions', 'Courses & groups', 'Group reports'] + (['Users & access'] if current_user['role'] == 'administrator' else []) + ['Profile']
# Separate widget keys, synchronized in callbacks before the next render.
if st.session_state.get('navigation') not in pages:
    st.session_state['navigation'] = pages[0]
st.session_state['phone_page'] = st.session_state['navigation']


def switch_page(source):
    selected = st.session_state[source]
    st.session_state['navigation'] = selected
    st.session_state['phone_page'] = selected


with sidebar_slot.container():
    st.markdown('<div class="brand-lockup"><span class="brand-mark">dd</span><div><div class="brand">Deadline Desk</div><div class="brand-sub">ROOM FOR YOUR NEXT STEP</div></div></div>', unsafe_allow_html=True)
    st.write('**' + current_user['name'] + '**')
    st.caption(current_user['role'].title())
    navigation_icons = {'Overview': '◈', 'My assignments': '▤', 'Assignments': '▤', 'AI study planner': '✧', 'My results': '◷', 'My courses': '▦', 'Profile': '◉', 'Review submissions': '✓', 'Courses & groups': '▦', 'Group reports': '◷', 'Users & access': '◎'}
    st.radio('Navigate', pages, key='navigation', label_visibility='collapsed', format_func=lambda item: navigation_icons.get(item, '·') + '  ' + item, on_change=switch_page, args=('navigation',))
    st.divider()
    timezones = ['Asia/Bishkek', 'Asia/Almaty', 'UTC', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'Asia/Kolkata', 'Asia/Tokyo']
    st.selectbox('Display timezone', timezones, key='timezone')
    st.caption('Deadlines are inclusive: work received exactly at the deadline is on time.')
    if st.button('Refresh data', width='stretch'):
        st.rerun()
    if st.button('Sign out', width='stretch'):
        st.session_state.clear()
        st.rerun()


def workspace():
    try:
        user = svc.user(st.session_state['user_id'])
        if user['role'] != current_user['role']:
            st.rerun()
        views = {'Overview': dashboard, 'Assignments': assignment_page, 'My assignments': assignment_page,
                 'AI study planner': ai_planner_page,
                 'Review submissions': review_page, 'Courses & groups': courses_page, 'My courses': courses_page,
                 'Group reports': reports_page, 'My results': results_page, 'Users & access': users_page, 'Profile': profile_page}
        views[page](user)
    except AppError as exc:
        try:
            svc.user(st.session_state['user_id'])
        except AppError:
            st.session_state.clear()
            st.rerun()
        st.error(str(exc))


with main_slot.container():
    show_flash()
    with st.container(key='phone_navigation'):
        st.markdown('**▣ Deadline Desk** · ' + current_user['role'].title())
        st.selectbox('Go to', pages, key='phone_page', on_change=switch_page, args=('phone_page',))
        st.caption('Open the ☰ menu for your account, timezone, refresh, and sign out.')
    page = st.session_state['navigation']
    workspace()

# Only the timer is a fragment. Forms stay in the full app so navigation and
# authentication reruns cleanly remove the previous page's widgets.
st.session_state['_refresh_at'] = time.monotonic()


@st.fragment(run_every=60 if page in ('Overview', 'My results', 'My courses', 'Group reports') else None)
def refresh_timer():
    if time.monotonic() - st.session_state.get('_refresh_at', 0) >= 59:
        st.rerun()


refresh_timer()
