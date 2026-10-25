"""Client import validation with synthetic records; no browser, API or inference."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
NODE = """
const fs = require('node:fs');
const {validateSavedRecord} = require('./incidentcourt/static/app.js');
try {
  const report = validateSavedRecord(JSON.parse(fs.readFileSync(0, 'utf8')));
  process.stdout.write(JSON.stringify({ok:true, run_id:report.run_id, summary:report.summary}));
} catch (error) {
  process.stdout.write(JSON.stringify({ok:false, message:error.message}));
}
"""


def fixture_report():
    return {'incident_id': 'IC-003', 'run_id': '012345abcdef', 'mode': 'zetaris / nightshift',
            'summary': 'Synthetic fixture assessment.', 'likely_service': 'fixture-service',
            'confidence': 'limited', 'created_at': '2026-10-25T12:00:00Z',
            'model': {'mode': 'nightshift', 'detail': 'Fixture-only provider proof.'},
            'memory': {'mode': 'official-hmem-reference', 'matches': []},
            'queries': [{'id': 'Q01', 'purpose': 'Fixture measurement query', 'backend': 'zetaris',
                         'sql': "SELECT value FROM metrics WHERE incident_id='IC-003'", 'row_count': 1}],
            'evidence': [{'id': 'E01', 'kind': 'metrics', 'query_id': 'Q01', 'service': 'fixture-service',
                          'summary': 'Fixture measurement.', 'ts': 1, 'rows': [{'value': None, 'service': 'fixture-service'}]}],
            'steps': [{'role': 'Investigator', 'action': 'Inspect measurements', 'detail': 'Fixture reasoning step.',
                       'query_id': 'Q01', 'evidence_ids': ['E01']}],
            'timeline': [{'ts': 1, 'title': 'Fixture observation', 'detail': 'Fixture timeline text.', 'evidence_ids': ['E01']}],
            'alternatives': [], 'limitations': ['Synthetic input for import validation only.'],
            'supporting_evidence_ids': ['E01'], 'counterevidence_ids': [], 'counterevidence': [],
            'provider_explanation': {'summary': 'Fixture cited narrative.', 'evidence_ids': ['E01']}}


class SavedRecordValidationTests(unittest.TestCase):
    def validate(self, value):
        process = subprocess.run(['node', '-e', NODE], cwd=ROOT, input=json.dumps(value),
                                 text=True, capture_output=True, timeout=5, check=True)
        self.assertEqual(process.stderr, '')
        return json.loads(process.stdout)

    def test_accepts_complete_record_preserves_nulls_and_renders_nothing_in_node(self):
        result = self.validate(fixture_report())
        self.assertTrue(result['ok'])
        self.assertEqual(result['run_id'], '012345abcdef')
        # The validator does not evaluate input text. Rendering later uses
        # textContent, so telemetry markup remains inert record content.
        value = fixture_report()
        value['summary'] = '<script>throw new Error("fixture-must-not-execute")</script>'
        result = self.validate(value)
        self.assertTrue(result['ok'])
        self.assertEqual(result['summary'], value['summary'])

    def test_rejects_incomplete_wrong_mode_and_unsupported_structures(self):
        mutations = [lambda r: r.pop('model'), lambda r: r.update(run_id='../private'),
                     lambda r: r.update(mode='live-from-anywhere'), lambda r: r.update(steps=[]),
                     lambda r: r['memory'].update(matches=[None]),
                     lambda r: r['evidence'][0].update(rows=[{'nested': {'not': 'a scalar cell'}}]),
                     lambda r: r['queries'][0].update(backend='local'),
                     lambda r: r['queries'][0].update(row_count=1001),
                     lambda r: r['queries'][0].update(sql='DELETE FROM metrics')]
        for mutate in mutations:
            value = fixture_report()
            mutate(value)
            with self.subTest(value=value): self.assertFalse(self.validate(value)['ok'])
        self.assertFalse(self.validate([])['ok'])

    def test_rejects_unresolved_or_duplicate_provenance_and_absent_explanation(self):
        mutations = [lambda r: r['evidence'][0].update(query_id='Q99'),
                     lambda r: r['steps'][0].update(evidence_ids=['E99']),
                     lambda r: r['provider_explanation'].update(evidence_ids=['E99']),
                     lambda r: r.update(supporting_evidence_ids=['E99']),
                     lambda r: r['evidence'].append(deepcopy(r['evidence'][0])),
                     lambda r: r.pop('provider_explanation')]
        for mutate in mutations:
            value = fixture_report()
            mutate(value)
            with self.subTest(value=value): self.assertFalse(self.validate(value)['ok'])

    def test_rejects_oversized_nested_record_before_rendering(self):
        value = fixture_report()
        value['evidence'][0]['rows'] = [{'value': 1}] * 1001
        self.assertFalse(self.validate(value)['ok'])
        value = fixture_report()
        value['summary'] = 'x' * 12001
        self.assertFalse(self.validate(value)['ok'])


class SavedRecordSelectionRaceTests(unittest.TestCase):
    def test_late_archive_response_cannot_replace_an_imported_record_selection(self):
        script = r"""
const assert = require('node:assert/strict');
const {selectedIncidentId} = require('./incidentcourt/static/app.js');
const incidents = [{id:'IC-001'}, {id:'IC-002'}, {id:'IC-003'}];
// The record is opened while the initial archive request is still pending.
let selection = selectedIncidentId([], 'IC-003', '');
assert.equal(selection, 'IC-003');
// The later response starts with a different case; the record keeps its identity.
selection = selectedIncidentId(incidents, 'IC-003', selection);
assert.equal(selection, 'IC-003');
// An offline or different archive cannot make an imported record depend on it.
assert.equal(selectedIncidentId([{id:'IC-001'}], 'IC-003', selection), 'IC-003');
assert.equal(selectedIncidentId([], 'IC-003', selection), 'IC-003');
// Normal archive selection still follows the user's choice without a report.
assert.equal(selectedIncidentId(incidents, null, 'IC-002'), 'IC-002');
assert.equal(selectedIncidentId(incidents, null, ''), 'IC-001');
assert.deepEqual(incidents, [{id:'IC-001'}, {id:'IC-002'}, {id:'IC-003'}]);
"""
        result = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True,
                                text=True, timeout=5, check=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
