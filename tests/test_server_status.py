"""Fixture-only status metadata checks; no remote SQL, model call or real receipt."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

from incidentcourt import server

MODEL = 'nvidia/fixture-hosted-model'


def fixture_receipt():
    calls = {}
    for phase, prefix in (('history', 'h'), ('target', 't')):
        calls[phase] = [{'served_model': MODEL, 'provider': 'nvidia', 'from_cache': False,
                         'actual_usd': 0, 'receipt_id': f'fixture-{prefix}-{i}',
                         'decision_id': f'fixture-decision-{prefix}-{i}'} for i in range(3)]
    layers = {name: {'state': 'passed'} for name in server.VERIFICATION_LAYERS}
    layers['expected_sample_counts'] = {'state': 'passed'}
    layers['nightshift_execution'].update(actual_gateway_calls=6, uncached=True, calls_by_phase=calls)
    layers['remote_query_provenance']['actual_queries'] = 45
    return {'status': 'passed', 'updated_at': '2026-10-25T20:00:00+00:00',
            'policy': {'approved_route': MODEL}, 'layers': layers,
            'reports': {'history': {'run_id': 'a' * 12}, 'target': {'run_id': 'b' * 12}},
            'private_prompt': 'fixture-secret-must-never-be-in-status'}


class HistoricalStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / 'artifacts/private/live-flow-fixture/receipt.json'
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(fixture_receipt()))

    def tearDown(self):
        self.temp.cleanup()

    def read(self, model=MODEL, path=None):
        with patch.object(server, 'ROOT', self.root), patch.dict(os.environ, {
            'INCIDENTCOURT_VERIFICATION_RECEIPT': str(path or self.path)}, clear=True):
            return server.last_verification(model)

    def test_only_allowlisted_historical_summary_and_same_route(self):
        summary = self.read()
        self.assertTrue(summary['historical'])
        self.assertEqual(summary['physical_model'], MODEL)
        self.assertEqual(summary['gateway_calls'], 6)
        self.assertEqual(summary['remote_selects'], 45)
        self.assertNotIn('fixture-secret', json.dumps(summary))
        self.assertNotIn('receipt.json', json.dumps(summary))
        self.assertIsNone(self.read(model='nvidia/different-model'))

    def test_failed_pending_wrong_cost_and_duplicate_execution_proofs_are_not_published(self):
        mutations = [lambda r: r.update(status='failed'),
                     lambda r: r['layers']['memory_consumption'].update(state='pending'),
                     lambda r: r['layers']['nightshift_execution'].update(actual_gateway_calls=5),
                     lambda r: r['layers']['nightshift_execution']['calls_by_phase']['target'][0].update(actual_usd=None),
                     lambda r: r['layers']['nightshift_execution']['calls_by_phase']['target'][0].update(actual_usd=.01),
                     lambda r: r['layers']['nightshift_execution']['calls_by_phase']['target'][0].update(receipt_id='fixture-h-0')]
        for mutate in mutations:
            value = fixture_receipt()
            mutate(value)
            self.path.write_text(json.dumps(value))
            with self.subTest(value=value): self.assertIsNone(self.read())

    def test_path_bound_size_and_malformed_json_fail_closed(self):
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps(fixture_receipt()))
        self.assertIsNone(self.read(path=outside))
        link = self.path.parent / 'outside-link.json'
        link.symlink_to(outside)
        self.assertIsNone(self.read(path=link))
        self.path.write_bytes(b' ' * (1024 * 1024 + 1))
        self.assertIsNone(self.read())
        self.path.write_text('{not-json')
        self.assertIsNone(self.read())
        with patch.object(server, 'ROOT', self.root), patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server.last_verification(MODEL))

    def test_hosted_configuration_is_distinct_from_current_inference_authorization(self):
        model_status = {'state': 'configured_unverified', 'requested_model': MODEL,
                        'inference_enabled': False, 'hosted_only': True}
        with patch.object(server, 'ROOT', self.root), patch.dict(os.environ, {
                'SAMPLE_MODE': 'false', 'INCIDENTCOURT_DATA_MODE': 'zetaris',
                'INCIDENTCOURT_VERIFICATION_RECEIPT': str(self.path)}, clear=True), \
             patch.object(server, 'NightshiftClient') as provider, \
             patch.object(server, 'HMemStore') as memory, \
             patch.object(server, 'Store') as store:
            provider.from_env.return_value.status.return_value = model_status
            memory.return_value.status.return_value = {'state': 'working'}
            value = server.status()
            self.assertEqual(value['model']['mode'], 'configured')
            self.assertFalse(value['model']['inference_enabled'])
            self.assertTrue(value['model']['hosted_only'])
            self.assertIn('authorization is off', value['model']['detail'])
            self.assertNotIn('Local heuristics', value['model']['detail'])
            self.assertTrue(value['last_verified']['historical'])
            self.assertFalse(value['integration_ready'])
            provider.from_env.return_value.status.assert_called_once_with()
            store.assert_not_called()


class UIAuthorizationTests(unittest.TestCase):
    def test_hosted_authoff_blocks_live_generation_but_sample_remains_deterministic(self):
        script = r"""
const assert = require('node:assert/strict');
const {canExecuteInvestigation} = require('./incidentcourt/static/app.js');
const live = {data:{mode:'zetaris'}, model:{mode:'configured',hosted_only:true,inference_enabled:false}};
assert.equal(canExecuteInvestigation(live,'nightshift'),false);
assert.equal(canExecuteInvestigation(live,'local'),false);
const sample = {...live,data:{mode:'sample'}};
assert.equal(canExecuteInvestigation(sample,'local'),true);
assert.equal(canExecuteInvestigation(sample,'nightshift'),false);
assert.equal(canExecuteInvestigation({...live,model:{...live.model,inference_enabled:true}},'nightshift'),true);
"""
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
