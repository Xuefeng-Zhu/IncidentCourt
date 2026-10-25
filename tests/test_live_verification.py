"""Fixture-only acceptance logic tests. No live SQL, inference or integration proof.

The synthetic provider receipts here are test inputs, never saved as live proof.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.verify_live_flow import (
    BudgetedClient, Journal, RecordingStore, REFERENCE_REVISION, ROOT, Store,
    VerificationError, expected_sample_counts, validate_call, validate_citations,
    validate_configuration, validate_memory_effect, validate_model_execution, validate_report,
)


def policy(**changes):
    return {'allow_hosted_inference': True, 'approved_route': 'nvidia/fixture-nemotron',
            'expected_provider': 'nvidia', 'expected_model': 'nvidia/fixture-nemotron',
            'max_calls': 6, 'max_output_tokens': 200, 'max_upstream_attempts': 18, **changes}


def provider_call(number=1):
    return {'call_id': f'fixture-call-{number}', 'created_at': f'2026-10-25T12:00:0{number}Z',
            'provider': 'nightshift', 'dispatched': True, 'status': 'completed',
            'request': {'model': 'nvidia/fixture-nemotron', 'max_tokens': 200},
            'response': {'content': 'Synthetic fixture response, not live inference.',
                'served_model': 'nvidia/fixture-nemotron', 'usage': {'completion_tokens': 100},
                'runtime_receipt': {'schema': 'meterless.compute_receipt.v1',
                    'receipt_id': f'fixture-receipt-{number}', 'decision_id': f'fixture-decision-{number}',
                    'requested_model': 'nvidia/fixture-nemotron', 'routed_model': 'nvidia/fixture-nemotron',
                    'winning_route': {'provider': 'nvidia', 'model': 'nvidia/fixture-nemotron'},
                    'from_cache': False, 'cache': {'status': 'miss'},
                    'privacy': {'path': 'cloud'}, 'cost': {'actual_usd': 0}}}}


def report():
    calls = [provider_call(number) for number in (1, 2, 3)]
    proof = lambda call: {'call_id': call['call_id'], 'served_model': call['response']['served_model'],
                          'live_inference_verified': True}
    return {'incident_id': 'IC-003', 'mode': 'zetaris / nightshift',
            'queries': [{'id': 'Q01', 'backend': 'zetaris', 'purpose': 'Memory follow-up: verify current measurements',
                         'sql': "SELECT service FROM incidentcourt.metrics WHERE incident_id='IC-003'"}],
            'evidence': [{'id': 'E01', 'query_id': 'Q01'}],
            'steps': [{'action': 'Nightshift selects a hypothesis', 'evidence_ids': ['E01']},
                      {'action': 'Nightshift requests a follow-up', 'evidence_ids': ['E01']},
                      {'action': 'Recheck retrieved measurements', 'query_id': 'Q01', 'evidence_ids': ['E01']}],
            'timeline': [{'evidence_ids': ['E01']}], 'supporting_evidence_ids': ['E01'],
            'counterevidence': [{'evidence_ids': ['E01']}],
            'provider_explanation': {'summary': 'Synthetic fixture narrative.', 'evidence_ids': ['E01']},
            'model': {'mode': 'nightshift', 'decision_calls': [proof(call) for call in calls[:2]], **proof(calls[2])},
            'memory': {'matches': [{'memory': {'id': 'mem-fixture', 'missionId': 'IC-001',
                       'tags': ['verified-investigation', 'split:development']}}],
                       'trace': {'engine': 'Meterless H-MEM reference', 'revision': REFERENCE_REVISION,
                                 'excludedIncident': 'IC-003', 'queries': [{'excludedIncident': 'IC-003'}]}}}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.client = type('FixtureClient', (), {'configured': True, 'allow_inference': True,
                                                'model': 'nvidia/fixture-nemotron', 'hosted_only': True})()

    def test_requires_real_data_model_optin_and_bounded_approved_route(self):
        validate_configuration('zetaris', 'nightshift', self.client, policy())
        for mode, model, changes in [('local', 'nightshift', {}), ('sample', 'nightshift', {}),
             ('zetaris', 'local', {}), ('zetaris', 'nightshift', {'allow_hosted_inference': False}),
             ('zetaris', 'nightshift', {'approved_route': 'meterless/local'}),
             ('zetaris', 'nightshift', {'approved_route': 'meterless/other'}),
             ('zetaris', 'nightshift', {'max_calls': 7}),
             ('zetaris', 'nightshift', {'max_upstream_attempts': 19}),
             ('zetaris', 'nightshift', {'max_upstream_attempts': 17}),
             ('zetaris', 'nightshift', {'max_output_tokens': 0})]:
            with self.subTest(mode=mode, model=model, changes=changes), self.assertRaises(VerificationError):
                validate_configuration(mode, model, self.client, policy(**changes))
        self.client.allow_inference = False
        with self.assertRaises(VerificationError):
            validate_configuration('zetaris', 'nightshift', self.client, policy())

    def test_public_sample_manifest_counts_are_exact_and_no_answers_opened(self):
        manifest = json.loads((ROOT / 'data/public/sample-manifest.json').read_text())
        totals, incidents = expected_sample_counts(manifest)
        self.assertEqual(totals['incidents'], 3)
        self.assertEqual(totals['metrics'], sum(item['rows']['metrics'] for item in manifest['incidents']))
        self.assertEqual(incidents['IC-001']['traces'], 5000)
        bad = deepcopy(manifest)
        bad['incidents'][0]['rows']['logs'] = -1
        with self.assertRaises(VerificationError): expected_sample_counts(bad)


class ProviderProofTests(unittest.TestCase):
    def test_only_exact_zero_actual_hosted_cost_is_accepted(self):
        self.assertEqual(validate_call(provider_call(), policy())['actual_usd'], 0)
        for amount in (None, 0.002, -1, '0', True):
            call = provider_call()
            call['response']['runtime_receipt']['cost']['actual_usd'] = amount
            with self.subTest(amount=amount), self.assertRaises(VerificationError):
                validate_call(call, policy())
        missing = provider_call()
        missing['response']['runtime_receipt'].pop('cost')
        with self.assertRaises(VerificationError): validate_call(missing, policy())

    def test_cache_local_fallback_wrong_model_and_unbounded_tokens_are_rejected(self):
        changes = [
            lambda c: c['response']['runtime_receipt'].update(from_cache=True),
            lambda c: c['response'].update(served_model='webllm/fixture-local'),
            lambda c: c['response']['runtime_receipt']['winning_route'].update(provider='webllm'),
            lambda c: c['response']['runtime_receipt'].update(routed_model='different/model'),
            lambda c: c['response']['runtime_receipt']['privacy'].update(path='local-only'),
            lambda c: c['request'].update(max_tokens=201),
            lambda c: c['response']['usage'].update(completion_tokens=201),
            lambda c: c['request'].update(model='meterless/local'),
        ]
        for change in changes:
            call = provider_call()
            change(call)
            with self.subTest(call=call), self.assertRaises(VerificationError): validate_call(call, policy())
        with self.assertRaises(VerificationError):
            validate_call(provider_call(), policy(expected_model='another/approved-model'))

    def test_nvidia_nemotron_receipts_require_thinking_disabled(self):
        call = provider_call()
        model = 'nvidia/nemotron-3.5-fixture'
        call['request']['model'] = model
        call['response']['served_model'] = model
        receipt = call['response']['runtime_receipt']
        receipt.update(requested_model=model, routed_model=model)
        receipt['winning_route']['model'] = model
        approved = policy(approved_route=model, expected_model=model)
        with self.assertRaises(VerificationError): validate_call(call, approved)
        call['request']['chat_template_kwargs'] = {'enable_thinking': True}
        with self.assertRaises(VerificationError): validate_call(call, approved)
        call['request']['chat_template_kwargs']['enable_thinking'] = False
        validate_call(call, approved)

    def test_three_distinct_report_calls_must_match_actual_receipts(self):
        value = report()
        proofs = validate_model_execution(value, [provider_call(i) for i in (1, 2, 3)], policy())
        self.assertEqual(len(proofs), 3)
        with self.assertRaises(VerificationError): validate_model_execution(value, [provider_call(1)], policy())
        calls = [provider_call(i) for i in (1, 2, 3)]
        calls[2]['response']['runtime_receipt']['receipt_id'] = calls[0]['response']['runtime_receipt']['receipt_id']
        with self.assertRaises(VerificationError): validate_model_execution(value, calls, policy())

    def test_attempt_ceiling_is_enforced_before_next_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'fixture-only')
            path = Path(directory) / 'fixture-call.json'
            class FixtureAdapter:
                count = 0
                def complete(self, messages, max_tokens):
                    self.count += 1
                    call = provider_call(self.count)
                    call['request']['max_tokens'] = max_tokens
                    call['request']['messages'] = messages
                    path.write_text(json.dumps(call))
                    return {'call_receipt': str(path), 'runtime_receipt': call['response']['runtime_receipt']}
            adapter = FixtureAdapter()
            proxy = BudgetedClient(adapter, policy(max_calls=2), journal)
            proxy.complete([{'role': 'user', 'content': 'Fixture input.'}], max_tokens=1200)
            self.assertEqual(json.loads(path.read_text())['request']['max_tokens'], 200)
            proxy.complete([{'role': 'user', 'content': 'Second fixture input.'}], max_tokens=1200)
            with self.assertRaises(VerificationError): proxy.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
            self.assertEqual(adapter.count, 2)
            self.assertEqual(len(set(proxy.nonces)), 2)
            self.assertEqual(journal.value['gateway_generation_attempts'], 2)

    def test_nonzero_or_missing_returned_cost_stops_before_any_additional_dispatch(self):
        for amount in (None, 0.002):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as directory:
                journal = Journal(Path(directory) / 'fixture-only')
                path = Path(directory) / 'fixture-call.json'
                class FixtureAdapter:
                    count = 0
                    def complete(self, messages, max_tokens):
                        self.count += 1
                        call = provider_call(self.count)
                        call['request']['messages'] = messages
                        call['request']['max_tokens'] = max_tokens
                        path.write_text(json.dumps(call))
                        # Deliberately differ from the zero-cost saved fixture:
                        # the returned proof must be checked immediately too.
                        returned = deepcopy(call['response']['runtime_receipt'])
                        returned['cost']['actual_usd'] = amount
                        return {'call_receipt': str(path), 'runtime_receipt': returned}
                adapter = FixtureAdapter()
                proxy = BudgetedClient(adapter, policy(), journal)
                with self.assertRaises(VerificationError):
                    proxy.complete([{'role': 'user', 'content': 'Fixture input.'}])
                self.assertTrue(path.is_file(), 'The failed actual call proof must be retained')
                self.assertTrue(journal.value['provider_dispatch_stopped'])
                with self.assertRaises(VerificationError):
                    proxy.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
                self.assertEqual(adapter.count, 1)
                self.assertEqual(journal.value['gateway_generation_attempts'], 1)


class EvidenceTests(unittest.TestCase):
    def test_all_current_citations_and_remote_query_provenance_must_resolve(self):
        value = report()
        self.assertEqual(validate_report(value, 'IC-003')['evidence'], 1)
        for mutate in [lambda r: r['queries'][0].update(backend='local'),
                       lambda r: r['evidence'][0].update(query_id='unknown'),
                       lambda r: r['provider_explanation'].update(evidence_ids=['E99']),
                       lambda r: r.update(supporting_evidence_ids=['E99']),
                       lambda r: r.update(mode='local / nightshift')]:
            changed = deepcopy(value)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaises(VerificationError): validate_report(changed, 'IC-003')
        # Historical evidence belongs to its own run and must not be mistaken for
        # a current-report citation during recursive reference validation.
        value['memory']['evidence_ids'] = ['historical-run:E20']
        validate_citations(value)

    def test_memory_requires_real_current_sql_and_official_read_ledger(self):
        value = report()
        before = {'memories': 1}
        after = {'memories': 1}
        creates = [{'action': 'create', 'memoryId': 'mem-fixture', 'newState': {'missionId': 'IC-001'}}]
        reads = [{'action': 'read', 'memoryId': 'mem-fixture', 'details': {'excludedIncident': 'IC-003'}}]
        check = lambda r, a, ledger: validate_memory_effect(r, 'IC-001', 'IC-003', before, a, creates, ledger)
        self.assertEqual(check(value, after, creates + reads)['new_read_entries'], 1)
        with self.assertRaises(VerificationError): check(value, after, creates)
        wrong = deepcopy(value)
        wrong['queries'][0]['sql'] = "SELECT service FROM metrics WHERE incident_id='IC-001'"
        with self.assertRaises(VerificationError): check(wrong, after, creates + reads)
        with self.assertRaises(VerificationError): check(value, {'memories': 2}, creates + reads)
        with self.assertRaises(VerificationError):
            check(value, after, creates + reads + [{'action': 'create', 'newState': {'missionId': 'IC-003'}}])
        wrong = deepcopy(value)
        wrong['memory']['matches'][0]['memory']['missionId'] = 'IC-003'
        with self.assertRaises(VerificationError): check(wrong, after, creates + reads)


class DurableFailureTests(unittest.TestCase):
    def test_failure_keeps_passed_layers_and_partial_actual_call_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'fixture-only'
            journal = Journal(output)
            journal.passed('zetaris_connection', select_one=True)
            journal.enter('target_investigation')
            folder = output / 'provider-calls' / 'target'
            folder.mkdir(parents=True)
            (folder / 'fixture-call.json').write_text(json.dumps(provider_call()))
            journal.fail(RuntimeError('fixture-secret-must-not-appear'))
            saved = json.loads((output / 'receipt.json').read_text())
            self.assertEqual(saved['status'], 'failed')
            self.assertEqual(saved['layers']['zetaris_connection']['state'], 'passed')
            self.assertEqual(saved['layers']['target_investigation']['state'], 'failed')
            self.assertEqual(len(saved['provider_calls']), 1)
            self.assertNotIn('fixture-secret-must-not-appear', json.dumps(saved))
            self.assertTrue((folder / 'fixture-call.json').is_file())
            with self.assertRaises(FileExistsError): Journal(output)

    def test_failed_sql_attempt_is_saved_and_error_content_is_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory) / 'fixture-only')
            def fake_init(instance):
                instance.mode = 'zetaris'
            with patch.object(Store, '__init__', fake_init), patch.object(Store, 'query', side_effect=RuntimeError('fixture-secret')):
                store = RecordingStore(journal, 'history')
                with self.assertRaises(RuntimeError): store.query('Fixture-only query', 'SELECT 1 AS connected')
            attempt = json.loads((journal.output / 'receipt.json').read_text())['query_attempts'][0]
            self.assertEqual(attempt['state'], 'failed')
            self.assertEqual(attempt['sql'], 'SELECT 1 AS connected')
            self.assertNotIn('fixture-secret', json.dumps(attempt))


if __name__ == '__main__':
    unittest.main()
