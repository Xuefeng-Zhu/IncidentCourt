"""One-run hosted authorization tests using only temporary fixture data.

These tests never contact Zetaris, Nightshift, or NVIDIA and cannot establish
anything about an account's billing or free-credit balance.
"""

import os
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

from incidentcourt.hosted_guard import BudgetedNightshiftClient, HostedRunPolicy
from incidentcourt.integrations import IntegrationError, NightshiftClient
from incidentcourt import server
from incidentcourt.workspace import JobManager, Workspace, WorkspaceError
from tests.test_live_verification import provider_call


MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'
NONCE = '1234567890abcdef1234567890abcdef'
ENV = {
    'NIGHTSHIFT_ALLOW_INFERENCE': '1',
    'NIGHTSHIFT_HOSTED_ONLY': '1',
    'INCIDENTCOURT_APPROVED_HOSTED_ROUTE': MODEL,
    'INCIDENTCOURT_HOSTED_RUN_NONCE': NONCE,
    'INCIDENTCOURT_HOSTED_RUN_INCIDENT': 'IC-003',
}


def fixture_client(**changes):
    values = dict(configured=True, allow_inference=True, hosted_only=True,
                  enable_thinking=False, model=MODEL,
                  assert_bounded_execution=lambda: None)
    values.update(changes)
    return SimpleNamespace(**values)


def fixture_policy(**changes):
    env = {**ENV, **changes}
    client = fixture_client(allow_inference=env.get('NIGHTSHIFT_ALLOW_INFERENCE') == '1',
                            hosted_only=env.get('NIGHTSHIFT_HOSTED_ONLY') == '1')
    with patch.dict(os.environ, env, clear=True):
        return HostedRunPolicy.from_env(client)


class SyntheticAdapter:
    """Only writes fake receipt files in the test's temporary directory."""
    def __init__(self, mutate=None, failure=None, mutate_result=None):
        self.count = 0
        self.mutate = mutate
        self.failure = failure
        self.mutate_result = mutate_result
        self.requests = []
        self.model = MODEL
        self.configured = self.allow_inference = self.hosted_only = True
        self.enable_thinking = False
        self.output_token_limit = None

    def assert_bounded_execution(self):
        # This fake explicitly promises bounded execution for fixture tests only.
        # The real staged runtime has no comparable verifiable contract.
        return None

    def complete(self, messages, max_tokens=1200):
        self.count += 1
        self.requests.append(max_tokens)
        if self.failure:
            raise IntegrationError('synthetic adapter failure')
        call = provider_call(self.count)
        call['request'].update(model=MODEL, max_tokens=max_tokens,
                               messages=deepcopy(messages),
                               chat_template_kwargs={'enable_thinking': False})
        proof = call['response']
        proof['served_model'] = MODEL
        receipt = proof['runtime_receipt']
        receipt.update(requested_model=MODEL, routed_model=MODEL)
        receipt['winning_route']['model'] = MODEL
        receipt['privacy']['path'] = 'cloud-ok'
        if self.mutate:
            self.mutate(call, self.count)
        self.receipt_directory.mkdir(parents=True, exist_ok=True)
        path = self.receipt_directory / f'fixture-call-{self.count}.json'
        path.write_text(json.dumps(call))
        result = {**deepcopy(proof), 'provider': 'nightshift', 'requested_model': MODEL,
                  'call_id': call['call_id'], 'call_receipt': str(path),
                  'live_inference_verified': True}
        if self.mutate_result:
            self.mutate_result(result, self.count)
        return result


class HostedPolicyTests(unittest.TestCase):
    def test_requires_explicit_route_incident_and_one_use_nonce(self):
        fixture_policy()
        changes = [
            {'NIGHTSHIFT_ALLOW_INFERENCE': '0'},
            {'NIGHTSHIFT_HOSTED_ONLY': '0'},
            {'INCIDENTCOURT_APPROVED_HOSTED_ROUTE': ''},
            {'INCIDENTCOURT_APPROVED_HOSTED_ROUTE': 'nvidia/another-model'},
            {'INCIDENTCOURT_APPROVED_HOSTED_ROUTE': 'meterless/auto'},
            {'INCIDENTCOURT_HOSTED_RUN_NONCE': ''},
            {'INCIDENTCOURT_HOSTED_RUN_NONCE': 'not-a-valid-nonce'},
            {'INCIDENTCOURT_HOSTED_RUN_INCIDENT': ''},
            {'INCIDENTCOURT_HOSTED_RUN_INCIDENT': "IC-003' OR 1=1"},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises((RuntimeError, ValueError)):
                fixture_policy(**change)

    def test_client_must_be_configured_hosted_exact_route_and_thinking_off(self):
        cases = [
            {'configured': False}, {'allow_inference': False}, {'hosted_only': False},
            {'enable_thinking': True}, {'enable_thinking': None},
            {'model': 'nvidia/another-model'}, {'model': 'meterless/auto'},
        ]
        for change in cases:
            with self.subTest(change=change), patch.dict(os.environ, ENV, clear=True):
                with self.assertRaises((RuntimeError, ValueError)):
                    HostedRunPolicy.from_env(fixture_client(**change))

    def test_missing_noncallable_or_failed_execution_attestation_rejects_authorization(self):
        def reject():
            raise IntegrationError('Unbounded synthetic runtime')
        for proof in (None, False, reject):
            with self.subTest(proof=proof), patch.dict(os.environ, ENV, clear=True):
                with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                    HostedRunPolicy.from_env(fixture_client(assert_bounded_execution=proof))
        client = fixture_client()
        del client.assert_bounded_execution
        with patch.dict(os.environ, ENV, clear=True), self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            HostedRunPolicy.from_env(client)

    def test_real_runtime_has_no_bounded_execution_proof_and_cannot_dispatch(self):
        client = NightshiftClient(token='fixture-token', model=MODEL, allow_inference=True,
                                  hosted_only=True, enable_thinking=False)
        with patch.object(client, '_request') as request, patch.dict(os.environ, ENV, clear=True):
            with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                HostedRunPolicy.from_env(client)
            request.assert_not_called()


class HostedAllowancePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'workspace.sqlite3'
        self.context = {'id': 'zetaris-fixture', 'mode': 'zetaris', 'label': 'Fixture'}
        self.workspace = Workspace(self.path, self.context)
        self.workspace.cache_cases([{'id': 'IC-003', 'title': 'Fixture case'},
                                    {'id': 'IC-001', 'title': 'Other fixture case'}])

    def test_invalid_incident_does_not_consume_authorized_run(self):
        policy = fixture_policy()
        with self.assertRaises((WorkspaceError, RuntimeError, ValueError)):
            self.workspace.reserve('IC-001', 'fixture-key-wrong-001', policy=policy)
        run_id = self.workspace.reserve('IC-003', 'fixture-key-right-001', policy=policy)
        self.assertEqual(self.workspace.investigation(run_id)['incident_id'], 'IC-003')

    def test_unattested_runtime_does_not_consume_nonce_or_start_worker(self):
        client = NightshiftClient(token='fixture-token', model=MODEL, allow_inference=True,
                                  hosted_only=True, enable_thinking=False)
        runner = Mock()
        manager = JobManager(self.workspace, threading.Lock(), runner=runner)
        with patch.dict(os.environ, ENV, clear=True), \
             patch.object(NightshiftClient, 'from_env', return_value=client), \
             patch.object(client, '_request') as request:
            with self.assertRaises((WorkspaceError, IntegrationError, RuntimeError, ValueError)):
                manager.start('IC-003', 'fixture-unattested-0001', 'nightshift')
            request.assert_not_called()
        runner.assert_not_called()
        self.assertEqual(self.workspace.investigations(), [])
        with self.workspace.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM hosted_run_allowances').fetchone()[0], 0)

    def test_used_nonce_cannot_be_reused_after_failure_or_restart(self):
        policy = fixture_policy()
        run_id = self.workspace.reserve('IC-003', 'fixture-key-initial-01', policy=policy)
        self.workspace.failed(run_id)
        reopened = Workspace(self.path, self.context)
        reopened.recover()
        with self.assertRaises((WorkspaceError, RuntimeError, ValueError)):
            reopened.reserve('IC-003', 'fixture-key-reuse-001', policy=fixture_policy())
        self.assertEqual(len(reopened.investigations()), 1)

    def test_same_nonce_cannot_race_in_separate_workspace_instances(self):
        policy = fixture_policy()
        other = Workspace(self.path, self.context)
        barrier = threading.Barrier(2)
        results = []
        def reserve(workspace, key):
            barrier.wait(timeout=5)
            try:
                results.append(('success', workspace.reserve('IC-003', key, policy=policy)))
            except (WorkspaceError, RuntimeError, ValueError) as exc:
                results.append(('rejected', type(exc).__name__))
        threads = [threading.Thread(target=reserve, args=(workspace, key)) for workspace, key in
                   ((self.workspace, 'fixture-key-race-0001'), (other, 'fixture-key-race-0002'))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sorted(kind for kind, _ in results), ['rejected', 'success'])
        self.assertEqual(len(self.workspace.investigations()), 1)

    def test_nonce_cannot_be_reused_by_another_source_context(self):
        self.workspace.reserve('IC-003', 'fixture-key-source-001', policy=fixture_policy())
        other = Workspace(self.path, {'id': 'another-zetaris', 'mode': 'zetaris', 'label': 'Other fixture'})
        other.cache_cases([{'id': 'IC-003', 'title': 'Other fixture case'}])
        with self.assertRaises((WorkspaceError, RuntimeError, ValueError)):
            other.reserve('IC-003', 'fixture-key-source-002', policy=fixture_policy())
        self.assertEqual(other.investigations(), [])

    def test_idempotent_replay_does_not_consume_a_second_allowance(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def runner(incident_id, **kwargs):
            calls.append(incident_id)
            entered.set()
            release.wait(5)
            raise RuntimeError('intentional fixture failure')
        manager = JobManager(self.workspace, threading.Lock(), runner=runner)
        adapter = SyntheticAdapter()
        key = 'fixture-one-run-key-001'
        with patch.dict(os.environ, ENV, clear=True), \
             patch('incidentcourt.integrations.NightshiftClient.from_env', return_value=adapter), \
             patch('incidentcourt.hosted_guard.PROJECT_ROOT', Path(self.temp.name)):
            run_id = manager.start('IC-003', key, 'nightshift')
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(manager.start('IC-003', key, 'nightshift'), run_id)
                with self.assertRaises(WorkspaceError):
                    manager.start('IC-003', 'fixture-one-run-key-002', 'nightshift')
                thread = manager.threads[run_id]
            finally:
                release.set()
            thread.join(5)
            self.assertEqual(manager.start('IC-003', key, 'nightshift'), run_id)
            with self.assertRaises(WorkspaceError):
                manager.start('IC-003', 'fixture-one-run-key-003', 'nightshift')
        self.assertEqual(calls, ['IC-003'])
        self.assertEqual(adapter.count, 0)
        self.assertEqual(self.workspace.investigation(run_id)['state'], 'failed')

    def test_setup_failures_preserve_consumed_allowance_and_release_lock(self):
        failures = [
            ('guard construction', 'incidentcourt.hosted_guard.BudgetedNightshiftClient', OSError('fixture failure')),
            ('thread start', 'incidentcourt.workspace.threading.Thread.start', RuntimeError('fixture failure')),
        ]
        for label, target, failure in failures:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workspace = Workspace(root / 'workspace.sqlite3', self.context)
                workspace.cache_cases([{'id': 'IC-003', 'title': 'Fixture case'}])
                lock = threading.Lock()
                runner = Mock()
                manager = JobManager(workspace, lock, runner=runner)
                adapter = SyntheticAdapter()
                with patch.dict(os.environ, ENV, clear=True), \
                     patch('incidentcourt.integrations.NightshiftClient.from_env', return_value=adapter), \
                     patch('incidentcourt.hosted_guard.PROJECT_ROOT', root), \
                     patch(target, side_effect=failure):
                    with self.assertRaises((OSError, RuntimeError)):
                        manager.start('IC-003', 'fixture-setup-failure-01', 'nightshift')
                self.assertEqual([entry['state'] for entry in workspace.investigations()], ['failed'])
                self.assertFalse(workspace.allowance_available(fixture_policy()))
                self.assertIsNone(manager.active_id)
                self.assertEqual(manager.threads, {})
                self.assertFalse(lock.locked())
                runner.assert_not_called()
                self.assertEqual(adapter.count, 0)


class BudgetedNightshiftClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def client(self, adapter=None):
        adapter = adapter or SyntheticAdapter()
        return adapter, BudgetedNightshiftClient(adapter, fixture_policy(), 'a' * 20,
                                                 output_root=self.root)

    def test_exactly_three_calls_are_allowed_and_each_has_a_token_cap(self):
        adapter, guarded = self.client()
        for _ in range(3):
            result = guarded.complete([{'role': 'user', 'content': 'Fixture input.'}], max_tokens=1200)
            self.assertEqual(result['served_model'], MODEL)
            self.assertEqual(result['runtime_receipt']['cost']['actual_usd'], 0)
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'A fourth call must not dispatch.'}])
        self.assertEqual(adapter.count, 3)
        self.assertTrue(all(1 <= maximum <= 600 for maximum in adapter.requests))
        self.assertEqual(len(list(adapter.receipt_directory.glob('*.json'))), 3)

    def test_a_lower_adapter_token_cap_is_accepted_but_an_overrun_is_not(self):
        class LowerCapAdapter(SyntheticAdapter):
            def complete(self, messages, max_tokens=1200):
                return super().complete(messages, max_tokens=min(max_tokens, 200))
        adapter, guarded = self.client(LowerCapAdapter())
        guarded.complete([{'role': 'user', 'content': 'Fixture input.'}], max_tokens=1200)
        saved = json.loads(next(adapter.receipt_directory.glob('*.json')).read_text())
        self.assertEqual(saved['request']['max_tokens'], 200)
        with tempfile.TemporaryDirectory() as directory:
            class OverrunAdapter(SyntheticAdapter):
                def complete(self, messages, max_tokens=1200):
                    return super().complete(messages, max_tokens=max_tokens + 1)
            overrun = OverrunAdapter()
            blocked = BudgetedNightshiftClient(overrun, fixture_policy(), 'd' * 20, output_root=Path(directory))
            with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                blocked.complete([{'role': 'user', 'content': 'Fixture input.'}], max_tokens=1200)
            with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                blocked.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
            self.assertEqual(overrun.count, 1)

    def test_bad_or_missing_cost_never_allows_a_second_dispatch(self):
        for amount in (None, 0.01, True, '0'):
            with self.subTest(amount=amount), tempfile.TemporaryDirectory() as directory:
                adapter = SyntheticAdapter(lambda call, _: call['response']['runtime_receipt']['cost'].update(actual_usd=amount))
                guarded = BudgetedNightshiftClient(adapter, fixture_policy(), 'b' * 20, output_root=Path(directory))
                with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                    guarded.complete([{'role': 'user', 'content': 'Fixture input.'}])
                self.assertEqual(len(list(adapter.receipt_directory.glob('*.json'))), 1)
                with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                    guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
                self.assertEqual(adapter.count, 1)

    def test_returned_cost_must_match_independently_saved_receipt(self):
        adapter = SyntheticAdapter(mutate_result=lambda result, _:
                                   result['runtime_receipt']['cost'].update(actual_usd=0.01))
        adapter, guarded = self.client(adapter)
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Fixture input.'}])
        saved = json.loads(next(adapter.receipt_directory.glob('*.json')).read_text())
        self.assertEqual(saved['response']['runtime_receipt']['cost']['actual_usd'], 0)
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
        self.assertEqual(adapter.count, 1)

    def test_attempt_is_recorded_durably_before_adapter_dispatch(self):
        class JournalCheckingAdapter(SyntheticAdapter):
            def complete(self, messages, max_tokens=1200):
                journal = json.loads((self.receipt_directory.parent / 'journal.json').read_text())
                if (journal['gateway_generation_attempts'], journal['conservative_upstream_attempt_bound']) != (self.count + 1, (self.count + 1) * 3):
                    raise AssertionError('Attempt was not journaled before provider dispatch')
                return super().complete(messages, max_tokens=max_tokens)
        adapter, guarded = self.client(JournalCheckingAdapter())
        for _ in range(3):
            guarded.complete([{'role': 'user', 'content': 'Fixture input.'}])
        self.assertEqual(adapter.count, 3)

    def test_cached_wrong_route_and_missing_proof_are_sticky_failures(self):
        mutations = [
            lambda c, _: c['response']['runtime_receipt'].update(from_cache=True),
            lambda c, _: c['response']['runtime_receipt']['winning_route'].update(provider='webllm'),
            lambda c, _: c['response'].update(served_model='nvidia/another-model'),
            lambda c, _: c['response']['runtime_receipt'].pop('schema'),
            lambda c, _: c['response']['runtime_receipt'].pop('privacy'),
            lambda c, _: c['response']['runtime_receipt']['privacy'].update(path='local-only'),
            lambda c, _: c['request'].update(messages=[{'role': 'user', 'content': 'Lost nonce'}]),
            lambda c, _: c['request']['chat_template_kwargs'].update(enable_thinking=True),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as directory:
                adapter = SyntheticAdapter(mutate)
                guarded = BudgetedNightshiftClient(adapter, fixture_policy(), 'c' * 20, output_root=Path(directory))
                with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                    guarded.complete([{'role': 'user', 'content': 'Fixture input.'}])
                with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                    guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
                self.assertEqual(adapter.count, 1)

    def test_ambiguous_adapter_exception_is_not_retried(self):
        adapter, guarded = self.client(SyntheticAdapter(failure=True))
        for _ in range(2):
            with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
                guarded.complete([{'role': 'user', 'content': 'Fixture input.'}])
        self.assertEqual(adapter.count, 1)

    def test_execution_attestation_is_rechecked_before_each_dispatch(self):
        adapter, guarded = self.client()
        guarded.complete([{'role': 'user', 'content': 'First fixture call.'}])
        adapter.assert_bounded_execution = Mock(side_effect=IntegrationError('runtime no longer bounded'))
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
        adapter.assert_bounded_execution = Mock(return_value=None)
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Still must not dispatch.'}])
        self.assertEqual(adapter.count, 1)

    def test_thinking_cannot_be_enabled_between_calls(self):
        adapter, guarded = self.client()
        guarded.complete([{'role': 'user', 'content': 'First fixture call.'}])
        adapter.enable_thinking = True
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
        adapter.enable_thinking = False
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Still must not dispatch.'}])
        self.assertEqual(adapter.count, 1)

    def test_reused_provider_receipt_cannot_be_promoted_to_a_second_success(self):
        def duplicate_receipt(call, number):
            if number > 1:
                call['response']['runtime_receipt']['receipt_id'] = 'fixture-receipt-1'
        adapter, guarded = self.client(SyntheticAdapter(duplicate_receipt))
        guarded.complete([{'role': 'user', 'content': 'First fixture call.'}])
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Second fixture call.'}])
        with self.assertRaises((IntegrationError, RuntimeError, ValueError)):
            guarded.complete([{'role': 'user', 'content': 'Must not dispatch.'}])
        self.assertEqual(adapter.count, 2)


class LegacyRouteTests(unittest.TestCase):
    def test_legacy_hosted_routes_do_not_bypass_one_run_guard(self):
        for path in ('/run', '/api/investigate'):
            with self.subTest(path=path), patch.object(server, 'investigate') as investigate:
                handler = object.__new__(server.Handler)
                body = json.dumps({'incident_id': 'IC-003', 'model': 'nightshift'}).encode()
                handler.server = SimpleNamespace(server_port=8317)
                handler.headers = {'Host': '127.0.0.1:8317', 'Content-Type': 'application/json',
                                   'Content-Length': str(len(body))}
                handler.rfile = BytesIO(body)
                handler.path = path
                handler.reply = Mock()
                with patch.dict(os.environ, ENV, clear=True):
                    handler.do_POST()
                investigate.assert_not_called()
                self.assertGreaterEqual(handler.reply.call_args.args[1], 400)


class HostedStatusTests(unittest.TestCase):
    def test_status_does_not_offer_run_for_unattested_runtime(self):
        client = NightshiftClient(token='fixture-token', model=MODEL, allow_inference=True,
                                  hosted_only=True, enable_thinking=False)
        env = {**ENV, 'INCIDENTCOURT_DATA_MODE': 'zetaris', 'SAMPLE_MODE': 'false'}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(NightshiftClient, 'from_env', return_value=client), \
             patch.object(client, '_request') as request, \
             patch.object(server, 'HMemStore') as memory, \
             patch.object(server, 'workspace_service') as service:
            memory.return_value.status.return_value = {'state': 'working'}
            result = server.status()
            request.assert_not_called()
            service.assert_not_called()
        self.assertFalse(result['model']['inference_enabled'])
        self.assertIsNone(result['model']['approved_incident'])
        self.assertFalse(result['integration_ready'])

    def test_status_only_offers_an_unused_verified_allowance(self):
        env = {**ENV, 'INCIDENTCOURT_DATA_MODE': 'zetaris', 'SAMPLE_MODE': 'false'}
        client = fixture_client()
        client.status = Mock(return_value={'state': 'configured_unverified', 'requested_model': MODEL,
                                           'inference_enabled': True, 'hosted_only': True})
        workspace = Mock()
        with patch.dict(os.environ, env, clear=True), \
             patch.object(NightshiftClient, 'from_env', return_value=client), \
             patch.object(server, 'HMemStore') as memory, \
             patch.object(server, 'workspace_service', return_value=(workspace, Mock())):
            memory.return_value.status.return_value = {'state': 'working'}
            workspace.allowance_available.return_value = True
            available = server.status()['model']
            self.assertTrue(available['inference_enabled'])
            self.assertEqual(available['approved_incident'], 'IC-003')
            workspace.allowance_available.return_value = False
            consumed = server.status()['model']
            self.assertFalse(consumed['inference_enabled'])
            self.assertIsNone(consumed['approved_incident'])


class WorkspaceRunButtonTests(unittest.TestCase):
    def test_workspace_ui_only_offers_the_approved_run(self):
        script = r"""
const assert = require('node:assert/strict');
const {canStartWorkspaceInvestigation: canStart} = require('./incidentcourt/static/app.js');
const approved = {model: {inference_enabled: true, hosted_only: true, approved_incident: 'IC-003'}};
assert.equal(canStart(null, 'sample', 'IC-001'), true);
assert.equal(canStart({}, 'sample', null), true);
assert.equal(canStart(null, 'zetaris', 'IC-003'), false);
assert.equal(canStart({}, 'zetaris', 'IC-003'), false);
assert.equal(canStart(approved, 'zetaris', 'IC-003'), true);
assert.equal(canStart(approved, 'zetaris', null), true);
assert.equal(canStart(approved, 'zetaris', 'IC-001'), false);
assert.equal(canStart(approved, 'local', 'IC-003'), false);
assert.equal(canStart(approved, undefined, 'IC-003'), false);
assert.equal(canStart({model: {...approved.model, inference_enabled: false}}, 'zetaris', 'IC-003'), false);
assert.equal(canStart({model: {...approved.model, hosted_only: false}}, 'zetaris', 'IC-003'), false);
for (const value of [undefined, null, '', 'IC-03', 'IC-003-extra', 'IC-003\' OR 1=1']) {
  assert.equal(canStart({model: {...approved.model, approved_incident: value}}, 'zetaris', 'IC-003'), false);
}
"""
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=5, check=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
