"""Recorded analysis bounds retain real query lineage without new SQL or inference."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from incidentcourt.investigate import investigate
from incidentcourt.sample import ensure_sample_db
from incidentcourt.store import Store
from incidentcourt.workspace import validate_report, WorkspaceError

ROOT = Path(__file__).resolve().parents[1]


class NoHistory:
    """Explicit test-only empty memory; no provider calls or persistent writes."""
    def retrieve(self, *args, **kwargs):
        return {'memories': [], 'context': '', 'trace': {}}

    def remember_verified(self, *args, **kwargs):
        pass


def public_report():
    return json.loads((ROOT / 'output_examples/example_1.json').read_text())


class AnalysisWindowTests(unittest.TestCase):
    def test_actual_bounds_and_query_id_without_additional_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(path=ensure_sample_db(root / 'sample.sqlite3'), mode='sample')
            # A prior query ensures provenance is captured rather than assuming Q01.
            incident = store.incidents()[0]
            with patch('incidentcourt.investigate.ROOT', root):
                report = investigate('IC-001', store=store, memory=NoHistory())
            start, end = incident['start_ts'], incident['end_ts']
            self.assertEqual(report['analysis_window'], {
                'start_ts': start, 'end_ts': end,
                'baseline_end_ts': start + (end - start) / 3,
                'comparison_start_ts': start + 2 * (end - start) / 3,
                'query_id': 'Q02',
            })
            query = next(q for q in report['queries'] if q['id'] == report['analysis_window']['query_id'])
            self.assertEqual(query['purpose'], 'Read incident window')
            self.assertEqual(query['row_count'], 1)
            # Visualization metadata reuses the existing incident SELECT and adds
            # no telemetry requests to the established cold investigation flow.
            expected = [q['purpose'] for q in public_report()['queries']]
            self.assertEqual([q['purpose'] for q in report['queries'][1:]], expected)
            self.assertIs(validate_report(report), report)

    def test_legacy_report_without_bounds_remains_unchanged(self):
        report = public_report()
        original = deepcopy(report)
        self.assertIs(validate_report(report), report)
        self.assertEqual(report, original)
        self.assertNotIn('analysis_window', report)

    def test_optional_bounds_require_ordered_finite_values_and_known_query(self):
        valid = {'start_ts': 0, 'baseline_end_ts': 30,
                 'comparison_start_ts': 60, 'end_ts': 90, 'query_id': 'Q01'}
        report = public_report()
        report['analysis_window'] = valid
        self.assertIs(validate_report(report), report)
        mutations = [
            None, [], {},
            {**valid, 'start_ts': True},
            {**valid, 'start_ts': '0'},
            {**valid, 'end_ts': float('inf')},
            {**valid, 'comparison_start_ts': float('nan')},
            {**valid, 'baseline_end_ts': 0},
            {**valid, 'baseline_end_ts': 61},
            {**valid, 'comparison_start_ts': 90},
            {**valid, 'query_id': 'Q-missing'},
            {**valid, 'query_id': []},
        ]
        for window in mutations:
            with self.subTest(window=window):
                candidate = public_report()
                candidate['analysis_window'] = window
                with self.assertRaises(WorkspaceError):
                    validate_report(candidate)


if __name__ == '__main__':
    unittest.main()
