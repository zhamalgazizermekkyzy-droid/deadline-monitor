import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mobile_ui import record_cards, display_value


class TagCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CardTests(unittest.TestCase):
    def test_user_content_is_escaped_in_names_keys_and_values(self):
        html = record_cards([{'Student': '<script>alert(1)</script>', '<img src=x>': '<svg onload=alert(1)>', 'Email': 'a&b@example.com'}])
        collector = TagCollector()
        collector.feed(html)
        self.assertFalse(any(tag in ('script', 'img', 'svg') for tag, _ in collector.tags))
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('a&amp;b@example.com', html)

    def test_zero_scores_and_inactive_accounts_are_preserved(self):
        html = record_cards([{'Student': 'Sam', 'Score': 0, 'active': 0, 'Status': 'Graded'}])
        self.assertIn('<dd>0</dd>', html)
        self.assertIn('Inactive', html)
        self.assertIn('record-badge success', html)

    def test_missing_and_late_labels_are_readable(self):
        html = record_cards([{'Assignment': 'Project', 'Score': None, 'Status': 'Overdue', 'Timing': 'Late'}])
        self.assertIn('record-badge danger', html)
        self.assertIn('record-badge warning', html)
        self.assertEqual(display_value(float('nan')), '—')
        self.assertEqual(display_value(None), '—')

    def test_empty_records(self):
        self.assertEqual(record_cards([{}]), '<div class="record-list"></div>')


if __name__ == '__main__':
    unittest.main()
