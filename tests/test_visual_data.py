"""Visualization data invariants; fixture tests do not invoke SQL or inference."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = """
const fs = require('node:fs');
const {buildVisualData} = require('./incidentcourt/static/visual-data.js');
const report = JSON.parse(fs.readFileSync(0, 'utf8'));
const before = JSON.stringify(report);
const result = buildVisualData(report);
if (JSON.stringify(report) !== before) throw Error('Saved report mutated');
process.stdout.write(JSON.stringify(result));
"""


def fixture():
    return {
        'queries': [{'id': 'Q01'}, {'id': 'Q02'}, {'id': 'Q03'}],
        'evidence': [
            {'id': 'E01', 'query_id': 'Q01', 'kind': 'metrics', 'service': 'cart', 'rows': [
                {'service': 'cart', 'name': 'requests', 'baseline': 0, 'current': 2,
                 'baseline_n': 4, 'current_n': 5, 'changed': True}]},
            {'id': 'E02', 'query_id': 'Q02', 'kind': 'traces', 'service': 'cart', 'rows': [
                {'baseline_ms': 10, 'current_ms': 5, 'baseline_n': 4, 'current_n': 3}]},
            {'id': 'E03', 'query_id': 'Q03', 'kind': 'metrics', 'service': 'coverage', 'rows': [
                {'first_ts': 100, 'last_ts': 200, 'observations': 10}]},
            {'id': 'E04', 'query_id': 'Q03', 'kind': 'logs', 'service': 'coverage', 'rows': [
                {'first_ts': None, 'last_ts': None, 'observations': 0}]},
            {'id': 'E05', 'query_id': 'Q03', 'kind': 'traces', 'service': 'service graph', 'rows': [
                {'caller': 'web', 'callee': 'cart', 'spans': 4, 'child_mean_ms': 3}]},
        ]
    }


class VisualDataTests(unittest.TestCase):
    def derive(self, report):
        result = subprocess.run(['node', '-e', NODE], cwd=ROOT, input=json.dumps(report),
                                text=True, capture_output=True, timeout=5, check=True)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)

    def test_existing_real_sample_reports_keep_values_and_citation_provenance(self):
        for filename in sorted((ROOT / 'output_examples').glob('*.json')):
            with self.subTest(file=filename.name):
                report = json.loads(filename.read_text())
                result = self.derive(report)
                self.assertGreater(len(result['comparisons']), 0)
                evidence = {item['id']: item for item in report['evidence']}
                for comparison in result['comparisons']:
                    original = evidence[comparison['evidenceId']]
                    self.assertEqual(comparison['queryId'], original['query_id'])
                    field = 'baseline_ms' if comparison['kind'] == 'traces' else 'baseline'
                    self.assertTrue(any(row.get(field) == comparison['baseline'] for row in original['rows']))
                self.assertTrue(result['window']['inferredExtent'])
                self.assertIsNone(result['window']['baselineEnd'])

    def test_zero_negative_missing_and_counts_are_not_coerced(self):
        report = fixture()
        report['evidence'][0]['rows'] += [
            {'service': 'cart', 'name': 'temperature', 'baseline': -3, 'current': -1,
             'baseline_n': 5, 'current_n': 8, 'changed': False},
            {'service': 'cart', 'name': 'missing', 'baseline': None, 'current': True,
             'baseline_n': None, 'current_n': True},
        ]
        result = self.derive(report)
        comparisons = {item['metric']: item for item in result['comparisons']}
        self.assertEqual(comparisons['requests']['baseline'], 0)
        self.assertEqual(comparisons['temperature']['baseline'], -3)
        self.assertFalse(comparisons['temperature']['changed'])
        self.assertIsNone(comparisons['missing']['baseline'])
        self.assertIsNone(comparisons['missing']['current'])
        self.assertIsNone(comparisons['missing']['currentN'])
        self.assertEqual(comparisons['Mean span duration']['unit'], 'ms')
        self.assertEqual(comparisons['requests']['unit'], 'source units')
        self.assertEqual(result['coverage'][1]['observations'], 0)
        self.assertIsNone(result['coverage'][1]['first'])

    def test_unbacked_or_narrative_values_are_not_used(self):
        report = fixture()
        report['evidence'][0]['query_id'] = 'missing-query'
        report['evidence'][1]['rows'] = []
        report['evidence'][1]['summary'] = 'baseline 10, current 100, 4 samples'
        report['coverage'] = [{'kind': 'metrics', 'first_ts': -1000, 'last_ts': 99999, 'observations': 50}]
        result = self.derive(report)
        self.assertEqual(result['comparisons'], [])
        self.assertEqual(result['window']['start'], 100)
        self.assertEqual(result['window']['end'], 200)
        self.assertTrue(result['warnings'])

    def test_explicit_recorded_windows_are_validated_and_old_bounds_are_not_guessed(self):
        report = fixture()
        report['analysis_window'] = {'start_ts': 90, 'end_ts': 210, 'baseline_end_ts': 130,
                                     'comparison_start_ts': 170, 'query_id': 'Q01'}
        window = self.derive(report)['window']
        self.assertFalse(window['inferredExtent'])
        self.assertEqual((window['start'], window['baselineEnd'], window['comparisonStart'], window['end']),
                         (90, 130, 170, 210))
        for mutation in [{'query_id': 'Q99'}, {'baseline_end_ts': 220}, {'comparison_start_ts': None},
                         {'start_ts': True}, {'end_ts': 89}, {'baseline_end_ts': 90},
                         {'comparison_start_ts': 210}]:
            with self.subTest(mutation=mutation):
                broken = deepcopy(report)
                broken['analysis_window'].update(mutation)
                result = self.derive(broken)
                self.assertTrue(result['window']['inferredExtent'])
                self.assertIsNone(result['window']['baselineEnd'])
                self.assertTrue(result['warnings'])

    def test_duplicate_references_do_not_double_counts_or_destroy_cycles(self):
        report = fixture()
        repeated = deepcopy(report['evidence'][0])
        repeated.update(id='E06', kind='counterevidence')
        report['evidence'].append(repeated)
        report['evidence'][-2]['rows'] += [
            {'caller': 'web', 'callee': 'cart', 'spans': 4, 'child_mean_ms': 3},
            {'caller': 'cart', 'callee': 'web', 'spans': 2, 'child_mean_ms': 1},
            {'caller': 'cart', 'callee': 'cart', 'spans': 1, 'child_mean_ms': 0},
            {'caller': 'invalid', 'callee': 'cart', 'spans': -1, 'child_mean_ms': 0},
        ]
        result = self.derive(report)
        self.assertEqual(len(result['comparisons']), 2)
        self.assertEqual(len(result['edges']), 3)
        self.assertEqual(result['services'], ['cart', 'web'])
        self.assertEqual(result['edges'][2]['childMeanMs'], 0)

    def test_limits_are_visible_and_empty_or_malformed_data_is_safe(self):
        for report in [None, [], {}, {'queries': [], 'evidence': [None, 4]}]:
            result = self.derive(report)
            self.assertEqual(result['comparisons'], [])
            self.assertEqual(result['edges'], [])
            self.assertIsNone(result['window'])
        report = fixture()
        report['evidence'][0]['rows'] = [
            {'service': 'cart', 'name': f'metric-{i}', 'baseline': i, 'current': i + 1,
             'baseline_n': 4, 'current_n': 5} for i in range(110)]
        report['evidence'][4]['rows'] = [
            {'caller': 'web', 'callee': f'service-{i}', 'spans': i + 1, 'child_mean_ms': 1}
            for i in range(90)]
        result = self.derive(report)
        self.assertEqual(len(result['comparisons']), 100)
        self.assertEqual(len(result['edges']), 80)
        self.assertTrue(any('100' in message for message in result['warnings']))
        self.assertTrue(any('80' in message for message in result['warnings']))

    def test_nonfinite_in_memory_values_remain_missing(self):
        code = """
const {buildVisualData} = require('./incidentcourt/static/visual-data.js');
const result = buildVisualData({queries:[{id:'Q01'}],evidence:[{id:'E01',query_id:'Q01',kind:'metrics',service:'cart',
rows:[{name:'cpu',baseline:Infinity,current:NaN,baseline_n:3,current_n:3}]}]});
process.stdout.write(JSON.stringify(result.comparisons[0]));
"""
        process = subprocess.run(['node', '-e', code], cwd=ROOT, capture_output=True, text=True, check=True)
        row = json.loads(process.stdout)
        self.assertIsNone(row['baseline'])
        self.assertIsNone(row['current'])

    def test_finite_timestamps_outside_date_range_do_not_reach_the_renderer(self):
        report = fixture()
        report['evidence'][2]['rows'][0].update(first_ts=1e99, last_ts=2e99)
        report['analysis_window'] = {'start_ts': 1e99, 'baseline_end_ts': 2e99,
                                     'comparison_start_ts': 3e99, 'end_ts': 4e99, 'query_id': 'Q01'}
        result = self.derive(report)
        self.assertIsNone(result['window'])
        self.assertEqual([item['kind'] for item in result['coverage']], ['logs'])
        self.assertTrue(any('time bounds' in message for message in result['warnings']))


if __name__ == '__main__':
    unittest.main()
