"""Small-screen report cards. Pure HTML generation; no business-rule changes."""
from html import escape
import math


def display_value(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    return str(value)


def record_cards(records):
    cards = []
    for record in records:
        if not record:
            continue
        title_key = next((key for key in ('Assignment', 'Student', 'name', 'Course') if key in record), next(iter(record)))
        title = escape(display_value(record[title_key]))
        details = []
        for key, value in record.items():
            if key == title_key:
                continue
            label = escape(str(key).replace('_', ' ').title())
            if key == 'active':
                value = 'Active' if value else 'Inactive'
            text = escape(display_value(value))
            status_class = {'Overdue': 'danger', 'Late': 'warning', 'Graded': 'success', 'On time': 'success'}.get(str(value), '')
            content = f'<span class="record-badge {status_class}">{text}</span>' if key in ('Status', 'Timing') else text
            details.append(f'<div class="record-detail"><dt>{label}</dt><dd>{content}</dd></div>')
        cards.append(f'<article class="record-card"><h3>{title}</h3><dl>{"".join(details)}</dl></article>')
    return '<div class="record-list">' + ''.join(cards) + '</div>'
