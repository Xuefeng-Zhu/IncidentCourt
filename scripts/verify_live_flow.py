#!/usr/bin/env python3
"""Verify the real Zetaris → Nightshift → H-MEM investigation path.

This command performs real read-only SQL and at most six actual hosted model
calls through Nightshift. Usage needs separate user authorization; a CLI flag
does not grant it. The verifier does not retry, repair, replay or fall back locally. Nightshift itself
may retry a transient upstream failure twice (at most three provider attempts per
gateway call); that separate approved allowance is recorded explicitly.
Only free hosted usage with exactly $0 reported actual cost is accepted.
Failed/partial receipts are retained. Merely importing this module performs no network or inference.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from incidentcourt.integrations import HMemStore, NightshiftClient, REFERENCE_REVISION
from incidentcourt.investigate import investigate
from incidentcourt.store import Store, literal

TABLES = ('incidents', 'metrics', 'logs', 'traces')
UPSTREAM_ATTEMPTS_PER_GATEWAY_CALL = 3
LAYERS = ('configuration', 'zetaris_connection', 'application_tables',
          'expected_sample_counts', 'history_investigation', 'target_investigation',
          'remote_query_provenance', 'citations', 'nightshift_execution',
          'memory_consumption', 'holdout_isolation')


class VerificationError(RuntimeError):
    """Only static, nonsecret acceptance failures use this exception."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_failure(error):
    # Adapter exceptions are normally sanitized, but never trust arbitrary error
    # strings (including HTTP bodies, hostnames, config, or fixture secret values).
    return {'category': type(error).__name__,
            'detail': str(error) if isinstance(error, VerificationError)
                      else 'Execution failed; inspect the retained private call and query receipts.'}


class Journal:
    def __init__(self, output, expected_sample=False):
        self.output = Path(output).resolve()
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.value = {'format': 1, 'scope': 'real Zetaris SQL + actual hosted Nightshift inference + official Meterless H-MEM',
                      'status': 'running', 'created_at': now(), 'updated_at': now(),
                      'layers': {name: {'state': 'pending'} for name in LAYERS},
                      'query_attempts': [], 'provider_calls': [], 'reports': {}}
        if not expected_sample:
            self.value['layers']['expected_sample_counts'] = {'state': 'not_requested'}
        self.active = 'configuration'
        self.flush()

    def flush(self):
        self.value['updated_at'] = now()
        path = self.output / 'receipt.json'
        temporary = self.output / 'receipt.json.part'
        temporary.write_text(json.dumps(self.value, indent=2, allow_nan=False) + '\n')
        temporary.chmod(0o600)
        temporary.replace(path)

    def enter(self, layer):
        self.active = layer
        self.value['layers'][layer] = {'state': 'running'}
        self.flush()

    def passed(self, layer, **details):
        self.value['layers'][layer] = {'state': 'passed', **details}
        self.flush()

    def collect_calls(self):
        calls = []
        for path in (self.output / 'provider-calls').glob('*/*.json'):
            # The actual adapter writes an exclusive, private receipt after every
            # call, even when investigate() never reaches a completed report.
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                calls.append({'phase': path.parent.name, 'file': str(path.relative_to(self.output)),
                              'status': 'unreadable_partial_receipt', 'created_at': None, 'call_id': path.stem})
                continue
            calls.append({'phase': path.parent.name, 'file': str(path.relative_to(self.output)),
                          'call_id': value.get('call_id'), 'created_at': value.get('created_at'),
                          'status': value.get('status'), 'dispatched': value.get('dispatched'),
                          'served_model': value.get('response', {}).get('served_model'),
                          'runtime_receipt': value.get('response', {}).get('runtime_receipt', {}),
                          'output_sha256': sha256(value.get('response', {}).get('content'))})
        self.value['provider_calls'] = sorted(calls, key=lambda item: (str(item['created_at']), str(item['call_id'])))
        self.flush()
        return calls

    def fail(self, error, interrupted=False):
        self.collect_calls()
        self.value.update(status='interrupted' if interrupted else 'failed', failed_layer=self.active,
                          failure=safe_failure(error))
        self.value['layers'][self.active] = {'state': 'failed', **safe_failure(error)}
        self.flush()


class RecordingStore(Store):
    """Pass-through real Store with durable attempted SQL and returned-row proofs."""
    def __init__(self, journal, phase):
        super().__init__()
        self.journal, self.phase = journal, phase
        require(self.mode == 'zetaris', 'Live verification requires the configured Store mode to be zetaris.')

    def query(self, purpose, sql):
        attempt = {'phase': self.phase, 'purpose': purpose, 'sql': sql, 'backend': self.mode,
                   'started_at': now(), 'state': 'started'}
        self.journal.value['query_attempts'].append(attempt)
        self.journal.flush()
        try:
            rows, query_id = super().query(purpose, sql)
            attempt.update(state='completed', query_id=query_id, row_count=len(rows),
                           rows_sha256=sha256(rows), rows=rows, finished_at=now())
            self.journal.flush()
            return rows, query_id
        except BaseException as error:
            attempt.update(state='failed', finished_at=now(), failure=safe_failure(error))
            self.journal.flush()
            raise


def validate_configuration(mode, model, client, policy):
    require(mode == 'zetaris', 'Live verification requires the configured Store mode to be zetaris.')
    require(model == 'nightshift', 'Live verification requires the nightshift model mode.')
    require(policy.get('allow_hosted_inference') is True,
            'Hosted inference requires an explicit verification opt-in after external user approval.')
    require(client.configured and client.allow_inference,
            'Nightshift needs an existing authenticated runtime and explicitly enabled inference.')
    require(getattr(client, 'hosted_only', False) is True,
            'Nightshift hosted-only enforcement must be enabled before verification.')
    route = policy.get('approved_route')
    require(isinstance(route, str) and '/' in route and not route.startswith('meterless/')
            and route.split('/', 1)[0].lower() in HOSTED_PROVIDERS and route == client.model,
            'Configured Nightshift route must match an explicitly approved hosted provider/model ID.')
    if route.startswith(('nvidia/nemotron-3-', 'nvidia/nemotron-3.')):
        require(getattr(client, 'enable_thinking', None) is False,
                'Disable NVIDIA Nemotron thinking explicitly before the bounded verification.')
    require(not policy.get('expected_provider') or policy['expected_provider'] == route.split('/', 1)[0].lower(),
            'The expected provider pin differs from the approved route.')
    require(not policy.get('expected_model') or policy['expected_model'] == route,
            'The expected physical-model pin differs from the approved route.')
    require(isinstance(policy.get('max_calls'), int) and 1 <= policy['max_calls'] <= 6,
            'The live verification call budget must be between one and six attempts.')
    upstream = policy.get('max_upstream_attempts')
    require(isinstance(upstream, int) and not isinstance(upstream, bool)
            and policy['max_calls'] * UPSTREAM_ATTEMPTS_PER_GATEWAY_CALL <= upstream <= 18,
            'The approved upstream-attempt allowance must cover up to three attempts per gateway call and cannot exceed18.')
    require(isinstance(policy.get('max_output_tokens'), int) and 1 <= policy['max_output_tokens'] <= 4096,
            'The live verification output-token budget must be bounded to at most4096 per call.')


def observation_count(rows):
    require(isinstance(rows, list) and len(rows) == 1, 'An application count query did not return one row.')
    value = rows[0].get('observations')
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0 and int(value) == value,
            'An application count query returned an invalid count.')
    return int(value)


def expected_sample_counts(manifest):
    incidents = manifest.get('incidents')
    require(isinstance(incidents, list) and bool(incidents), 'Sample manifest has no incident inventory.')
    require(manifest.get('synthetic') is False, 'Expected-sample verification requires the real telemetry sample manifest.')
    totals = {'incidents': len(incidents), **{table: 0 for table in TABLES[1:]}}
    per_incident = {}
    for row in incidents:
        iid = row.get('id')
        require(isinstance(iid, str) and iid not in per_incident, 'Sample manifest incident identifiers are invalid.')
        per_incident[iid] = {}
        for table in TABLES[1:]:
            count = observation_count([{'observations': row.get('rows', {}).get(table)}])
            per_incident[iid][table] = count
            totals[table] += count
    return totals, per_incident


def validate_citations(report):
    evidence, queries = report.get('evidence'), report.get('queries')
    require(isinstance(evidence, list) and bool(evidence), 'The investigation has no evidence records.')
    require(isinstance(queries, list) and bool(queries), 'The investigation has no query receipts.')
    ids = [item.get('id') for item in evidence]
    qids = [item.get('id') for item in queries]
    require(all(isinstance(item, str) and item for item in ids) and len(ids) == len(set(ids)),
            'Evidence identifiers are missing or duplicated.')
    require(all(isinstance(item, str) and item for item in qids) and len(qids) == len(set(qids)),
            'Query identifiers are missing or duplicated.')
    valid = set(ids)
    require(all(item.get('query_id') in qids for item in evidence), 'Evidence references an unknown query.')

    def walk(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in {'evidence_ids', 'supporting_evidence_ids', 'counterevidence_ids'}:
                    require(isinstance(nested, list) and all(item in valid for item in nested),
                            'A report citation does not resolve to current evidence.')
                elif key != 'memory':
                    # Historical memory has prior-run provenance; never treat its
                    # references as citations to the current incident.
                    walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)
    walk(report)
    explanation = report.get('provider_explanation', {})
    require(isinstance(explanation.get('summary'), str) and bool(explanation['summary'].strip())
            and bool(explanation.get('evidence_ids')), 'The live explanation lacks a narrative or evidence citations.')
    return {'evidence': len(ids), 'queries': len(qids), 'all_current_citations_resolve': True}


def validate_report(report, incident_id):
    require(report.get('incident_id') == incident_id, 'The investigation returned the wrong incident.')
    require(report.get('mode') == 'zetaris / nightshift', 'The investigation did not execute the required live data/model modes.')
    require(report.get('model', {}).get('mode') == 'nightshift', 'The report does not contain Nightshift model execution.')
    queries = report.get('queries', [])
    require(bool(queries) and all(item.get('backend') == 'zetaris' for item in queries),
            'At least one investigation query used a non-Zetaris backend.')
    require(any('Nightshift selects a hypothesis' == item.get('action') for item in report.get('steps', []))
            and any('Nightshift requests a follow-up' == item.get('action') for item in report.get('steps', [])),
            'The model did not participate in both planning and evidence critique.')
    return validate_citations(report)


LOCAL_PROVIDERS = {'webllm', 'local', 'ollama', 'llama.cpp', 'llamacpp', 'lmstudio', 'mlx'}
HOSTED_PROVIDERS = {'openai', 'anthropic', 'nvidia', 'nvidia-nim', 'nim', 'google', 'gemini', 'groq',
                    'openrouter', 'azure', 'azure-openai', 'aws', 'bedrock', 'mistral', 'together',
                    'fireworks', 'cerebras', 'deepseek', 'cohere', 'xai', 'huggingface'}


def validate_call(call, policy):
    require(call.get('status') == 'completed' and call.get('dispatched') is True,
            'A required Nightshift call did not complete.')
    request = call.get('request', {})
    require(call.get('provider') == 'nightshift' and request.get('model') == policy['approved_route']
            and request.get('model') != 'meterless/local',
            'A Nightshift call did not use the authorized hosted route.')
    limit = request.get('max_tokens')
    require(isinstance(limit, int) and 1 <= limit <= policy['max_output_tokens'],
            'A Nightshift request exceeded the approved output-token bound.')
    if policy['approved_route'].startswith(('nvidia/nemotron-3-', 'nvidia/nemotron-3.')):
        require(request.get('chat_template_kwargs', {}).get('enable_thinking') is False,
                'The pinned NVIDIA Nemotron request did not disable thinking as approved.')
    proof = call.get('response', {})
    receipt = proof.get('runtime_receipt', {})
    physical = proof.get('served_model')
    require(isinstance(physical, str) and bool(physical)
            and not physical.lower().startswith(('meterless/', 'webllm/', 'local/', 'ollama/', 'mlx/')),
            'Nightshift did not report a physical hosted model.')
    require(receipt.get('schema') == 'meterless.compute_receipt.v1'
            and receipt.get('requested_model') == policy['approved_route'],
            'Nightshift returned no recognized compute receipt for the approved route.')
    route = receipt.get('winning_route', {})
    provider = str(route.get('provider', '')).lower()
    expected_provider = policy.get('expected_provider')
    require(provider and provider not in LOCAL_PROVIDERS
            and (provider in HOSTED_PROVIDERS or provider == expected_provider),
            'Nightshift did not attest to a recognized or explicitly pinned hosted provider.')
    require(receipt.get('routed_model') == physical and route.get('model') == physical
            and physical == policy['approved_route'] and provider == physical.split('/', 1)[0].lower(),
            'Nightshift physical model and winning-route proof disagree.')
    require(not expected_provider or provider == expected_provider,
            'Nightshift served a different provider from the approved physical provider.')
    require(not policy.get('expected_model') or physical == policy['expected_model'],
            'Nightshift served a different physical model from the approved model.')
    require(isinstance(receipt.get('receipt_id'), str) and bool(receipt['receipt_id'])
            and isinstance(receipt.get('decision_id'), str) and bool(receipt['decision_id']),
            'Nightshift compute receipt identifiers are missing.')
    require(receipt.get('from_cache') is False and receipt.get('cache', {}).get('status') == 'miss',
            'Nightshift returned a cached or unverified response.')
    require(receipt.get('privacy', {}).get('path') != 'local-only',
            'Nightshift attested to local model execution instead of hosted inference.')
    cost = receipt.get('cost', {})
    actual = cost.get('actual_usd')
    require(isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == 0,
            'Nightshift did not attest to exactly $0 actual provider cost; further calls are refused.')
    output_tokens = proof.get('usage', {}).get('completion_tokens', receipt.get('usage', {}).get('output_tokens'))
    require(isinstance(output_tokens, (int, float)) and not isinstance(output_tokens, bool)
            and 0 < output_tokens <= limit, 'Nightshift generated tokens are missing or exceed the request bound.')
    return {'call_id': call.get('call_id'), 'receipt_id': receipt['receipt_id'],
            'decision_id': receipt['decision_id'], 'served_model': physical, 'provider': provider,
            'actual_usd': actual, 'from_cache': False, 'output_tokens': output_tokens, 'max_output_tokens': limit}


def validate_model_execution(report, calls, policy):
    model = report.get('model', {})
    decisions = model.get('decision_calls', [])
    require(isinstance(decisions, list) and len(decisions) == 2,
            'A live investigation must retain planner and critic call receipts.')
    expected_ids = [item.get('call_id') for item in decisions] + [model.get('call_id')]
    require(len(calls) == 3 and len(set(expected_ids)) == 3 and None not in expected_ids,
            'A live investigation must make exactly three distinct provider calls.')
    by_id = {call.get('call_id'): call for call in calls}
    require(set(by_id) == set(expected_ids), 'Report call identifiers do not match retained actual provider calls.')
    proofs = [validate_call(by_id[call_id], policy) for call_id in expected_ids]
    require(len({proof['receipt_id'] for proof in proofs}) == 3
            and len({proof['decision_id'] for proof in proofs}) == 3,
            'Nightshift reused a compute receipt or decision identifier.')
    for result, proof in zip(decisions + [model], proofs):
        require(result.get('live_inference_verified') is True and result.get('served_model') == proof['served_model'],
                'Report model proof differs from the actual call receipt.')
    return proofs


def validate_memory_effect(report, history_id, target_id, before, after, audit_before, audit_after):
    matches = report.get('memory', {}).get('matches', [])
    require(bool(matches), 'The holdout investigation retrieved no earlier verified findings.')
    records = [item.get('memory', item) for item in matches]
    require(all(item.get('missionId') == history_id and item.get('missionId') != target_id
                and 'split:holdout' not in item.get('tags', []) for item in records),
            'Historical context included the current incident or an ineligible source.')
    trace = report.get('memory', {}).get('trace', {})
    require(trace.get('engine') == 'Meterless H-MEM reference' and trace.get('revision') == REFERENCE_REVISION,
            'Memory retrieval did not retain the official H-MEM reference proof.')
    require(trace.get('excludedIncident') == target_id
            and all(item.get('excludedIncident') == target_id for item in trace.get('queries', [])),
            'Memory retrieval did not exclude the current incident.')
    memory_ids = {item.get('id') for item in records}
    new_audit = audit_after[len(audit_before):]
    reads = [entry for entry in new_audit if entry.get('action') == 'read'
             and entry.get('memoryId') in memory_ids
             and entry.get('details', {}).get('excludedIncident') == target_id]
    require(bool(reads), 'The official memory ledger has no matching current-investigation read.')
    query_ids = {item.get('query_id') for item in report.get('steps', [])
                 if item.get('action') in {'Recheck retrieved measurements', 'Recheck retrieved memory'}}
    followups = [item for item in report.get('queries', []) if item.get('id') in query_ids
                 and 'memory' in item.get('purpose', '').lower()]
    require(bool(followups) and all(item.get('backend') == 'zetaris'
                and "incident_id=" + literal(target_id) in item.get('sql', '').replace(' ', '') for item in followups),
            'Retrieved memory did not trigger current-incident SQL through Zetaris.')
    require(before.get('memories', 0) > 0 and before.get('memories') == after.get('memories'),
            'The holdout investigation changed historical-memory membership.')
    require(not any(entry.get('action') == 'create' for entry in new_audit),
            'The holdout investigation created a historical memory.')
    require(all(entry.get('newState', {}).get('missionId') == history_id for entry in audit_after
                if entry.get('action') == 'create'), 'A holdout or unrelated incident entered fresh historical memory.')
    return {'source_incidents': [history_id], 'matched_memory_ids': sorted(memory_ids),
            'new_read_entries': len(reads), 'actual_current_queries': followups,
            'memory_count_before': before['memories'], 'memory_count_after': after['memories']}


class BudgetedClient:
    """A pass-through to the actual adapter, enforcing attempts before dispatch."""
    def __init__(self, client, policy, journal):
        self.client, self.policy, self.journal = client, policy, journal
        self.attempts = 0
        self.nonces = []
        self.stopped = False

    def complete(self, messages, max_tokens=1200):
        require(not self.stopped, 'Provider dispatch is stopped after an earlier failed acceptance check.')
        require(self.attempts < self.policy['max_calls'], 'The approved gateway generation-call budget is exhausted.')
        require((self.attempts + 1) * UPSTREAM_ATTEMPTS_PER_GATEWAY_CALL <= self.policy['max_upstream_attempts'],
                'The conservative approved upstream-attempt allowance is exhausted.')
        self.attempts += 1
        nonce = 'incidentcourt-verify-' + uuid.uuid4().hex
        self.nonces.append(nonce)
        dispatched_messages = [dict(item) for item in messages]
        marker = '\nVerification context nonce: ' + nonce + '. Keep the requested response schema unchanged; do not echo this nonce.'
        if dispatched_messages and dispatched_messages[0].get('role') == 'system' and isinstance(dispatched_messages[0].get('content'), str):
            dispatched_messages[0]['content'] += marker
        else:
            dispatched_messages.insert(0, {'role': 'system', 'content': marker.strip()})
        self.journal.value['gateway_generation_attempts'] = self.attempts
        self.journal.value['conservative_upstream_attempt_bound'] = self.attempts * UPSTREAM_ATTEMPTS_PER_GATEWAY_CALL
        self.journal.value['actual_upstream_attempts'] = None
        self.journal.value['provider_context_nonces'] = self.nonces
        self.journal.flush()
        try:
            result = self.client.complete(dispatched_messages, max_tokens=min(max_tokens, self.policy['max_output_tokens']))
            # The actual adapter has already saved an immutable private receipt.
            # Check the returned proof immediately, before any subsequent call.
            actual = result.get('runtime_receipt', {}).get('cost', {}).get('actual_usd')
            require(isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == 0,
                    'Nightshift did not attest to exactly $0 actual provider cost; further calls are refused.')
            path = Path(result.get('call_receipt', ''))
            if not path.is_absolute():
                path = ROOT / path
            require(path.is_file(), 'Nightshift did not persist the required private call receipt.')
            actual_call = json.loads(path.read_text())
            require(nonce in canonical(actual_call.get('request', {}).get('messages', [])),
                    'The actual provider request did not retain the fresh verification context.')
            validate_call(actual_call, self.policy)
            return result
        except BaseException as error:
            # This is sticky even if a caller catches the exception: no additional
            # gateway dispatch may follow a nonzero/missing cost or other failure.
            self.stopped = True
            self.journal.value['provider_dispatch_stopped'] = True
            self.journal.value['provider_stop_reason'] = safe_failure(error)
            self.journal.flush()
            raise



@contextmanager
def actual_dependencies(client, output):
    """Bind the real budgeted client and isolate output, without faking calls.

    The proxy forwards complete() unchanged apart from the approved token bound;
    HTTP, Store and H-MEM retain their actual implementations. The scoped binding
    merely directs the application to this budgeted client and private output.
    """
    module = importlib.import_module('incidentcourt.investigate')
    with patch.object(NightshiftClient, 'from_env', return_value=client), patch.object(module, 'ROOT', output):
        yield


def verify(output, *, history_id='IC-001', target_id='IC-003', model='nightshift', expected_sample=False,
           allow_hosted_inference=False, approved_route=None, expected_provider=None, expected_model=None,
           max_calls=6, max_output_tokens=600, max_upstream_attempts=18):
    journal = Journal(output, expected_sample)
    policy = {'allow_hosted_inference': allow_hosted_inference, 'approved_route': approved_route,
              'expected_provider': expected_provider.lower() if expected_provider else None,
              'expected_model': expected_model, 'max_calls': max_calls, 'max_output_tokens': max_output_tokens,
              'max_upstream_attempts': max_upstream_attempts, 'upstream_attempts_per_gateway_call_bound': UPSTREAM_ATTEMPTS_PER_GATEWAY_CALL,
              'required_actual_provider_cost_usd': 0}
    journal.value.update(history_incident=history_id, target_incident=target_id, policy=policy,
                         claimed_layers='SQL/provider/memory behavior only; no accuracy, production, browser or deployment claim')
    journal.flush()
    try:
        client = NightshiftClient.from_env()
        store = RecordingStore(journal, 'preflight')
        validate_configuration(store.mode, model, client, policy)
        budgeted = BudgetedClient(client, policy, journal)
        require(history_id != target_id, 'Use distinct history and holdout incident IDs.')
        journal.passed('configuration', data_mode=store.mode, model_mode=model, route=client.model,
                       hosted_inference_opt_in=True, gateway_attempt_budget=max_calls, upstream_attempt_budget=max_upstream_attempts,
                       per_call_output_bound=max_output_tokens)
        journal.enter('zetaris_connection')
        rows, _ = store.query('Live verification: read-only connectivity', 'SELECT 1 AS connected')
        require(rows == [{'connected': 1}], 'SELECT 1 did not return the expected Zetaris result.')
        journal.passed('zetaris_connection', select_one=True)
        journal.enter('application_tables')
        counts = {}
        for table in TABLES:
            rows, _ = store.query('Live verification: count ' + table,
                                 f'SELECT COUNT(*) AS observations FROM {store.table(table)}')
            counts[table] = observation_count(rows)
            require(counts[table] > 0, 'An application telemetry table is empty.')
        incidents = store.incidents()
        inventory = {item['id']: item for item in incidents}
        require(history_id in inventory and target_id in inventory, 'History or target incident is inaccessible through Zetaris.')
        require(inventory[history_id].get('split') == 'development'
                and inventory[target_id].get('split') == 'holdout', 'Use a development history and a holdout target.')
        journal.passed('application_tables', counts=counts, incident_ids=sorted(inventory))
        if expected_sample:
            journal.enter('expected_sample_counts')
            manifest = json.loads((ROOT / 'data/public/sample-manifest.json').read_text())
            totals, expected = expected_sample_counts(manifest)
            require(counts == totals and set(inventory) == set(expected), 'Remote table counts differ from the committed telemetry sample.')
            for iid, tables in expected.items():
                for table, count in tables.items():
                    rows, _ = store.query('Live verification: sample incident count',
                        f'SELECT COUNT(*) AS observations FROM {store.table(table)} WHERE incident_id={literal(iid)}')
                    require(observation_count(rows) == count, 'A remote incident count differs from the committed telemetry sample.')
            journal.passed('expected_sample_counts', counts=totals, per_incident=expected,
                           manifest_sha256=sha256(manifest), sampled_telemetry=True)

        journal.enter('history_investigation')
        memory = HMemStore(journal.output / 'memory')
        initial_memory = memory.status()
        require(initial_memory.get('state') == 'working' and initial_memory.get('memories') == 0,
                'Live verification needs a working empty official H-MEM store.')
        reports, call_sets, checks = {}, {}, {}
        for phase, iid in (('history', history_id), ('target', target_id)):
            layer = phase + '_investigation'
            journal.enter(layer)
            client.receipt_directory = journal.output / 'provider-calls' / phase
            if phase == 'target':
                before, audit_before = memory.status(), memory.audit()
                journal.value['memory_before_target'] = before
                journal.flush()
            try:
                with actual_dependencies(budgeted, journal.output):
                    report = investigate(iid, model=model, store=RecordingStore(journal, phase), memory=memory)
            finally:
                journal.collect_calls()
            reports[phase] = report
            report_path = journal.output / (phase + '-report.json')
            report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
            report_path.chmod(0o600)
            journal.value['reports'][phase] = {'run_id': report.get('run_id'), 'file': report_path.name,
                                             'sha256': sha256(report)}
            checks[phase] = validate_report(report, iid)
            call_paths = sorted(client.receipt_directory.glob('*.json'))
            calls = sorted((json.loads(path.read_text()) for path in call_paths), key=lambda value: value.get('created_at', ''))
            call_sets[phase] = validate_model_execution(report, calls, policy)
            journal.passed(layer, run_id=report['run_id'], **checks[phase], model_calls=len(calls))
        journal.enter('remote_query_provenance')
        attempts = journal.value['query_attempts']
        require(bool(attempts) and all(item['state'] == 'completed' and item['backend'] == 'zetaris' for item in attempts),
                'At least one remote query attempt failed or used another backend.')
        journal.passed('remote_query_provenance', actual_queries=len(attempts), returned_rows_hashed=True)
        journal.enter('citations')
        journal.passed('citations', reports=checks)
        journal.enter('nightshift_execution')
        all_calls = call_sets['history'] + call_sets['target']
        require(len(set(budgeted.nonces)) == 6, 'Provider verification contexts were not fresh for all six calls.')
        require(len({item['receipt_id'] for item in all_calls}) == 6
                and len({item['decision_id'] for item in all_calls}) == 6,
                'Nightshift reused a compute receipt between investigations.')
        costs = [item['actual_usd'] for item in all_calls]
        journal.passed('nightshift_execution', calls_by_phase=call_sets, actual_gateway_calls=6, conservative_upstream_attempt_bound=18,
                       actual_upstream_attempts=None, actual_provider_cost_usd=sum(costs) if all(value is not None for value in costs) else None,
                       reported_output_tokens=sum(item['output_tokens'] for item in all_calls), uncached=True)
        journal.enter('memory_consumption')
        after, audit_after = memory.status(), memory.audit()
        memory_result = validate_memory_effect(reports['target'], history_id, target_id,
                                              before, after, audit_before, audit_after)
        journal.value['memory_audit'] = audit_after
        journal.passed('memory_consumption', **memory_result)
        journal.enter('holdout_isolation')
        journal.passed('holdout_isolation', target_not_stored=True, earlier_history_only=True,
                       fresh_store=True, memory_count_after=after['memories'])
        journal.value['status'] = 'passed'
        journal.flush()
    except KeyboardInterrupt as error:
        journal.fail(error, interrupted=True)
    except Exception as error:
        journal.fail(error)
    return journal.value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='nightshift', choices=('nightshift',))
    parser.add_argument('--history', default='IC-001')
    parser.add_argument('--target', default='IC-003')
    parser.add_argument('--expected-sample', action='store_true', help='Require exact committed sample table and per-incident counts.')
    parser.add_argument('--allow-hosted-inference', action='store_true', required=True,
                        help='Required after approval of existing free hosted usage with $0 reported actual cost; this flag does not grant spending permission.')
    parser.add_argument('--approved-route', required=True, help='Exact advertised hosted provider/model ID approved for Nightshift usage, e.g.nvidia/model-id; automatic modes are refused.')
    parser.add_argument('--expected-provider', help='Optional approved physical provider; rejects a different winning route.')
    parser.add_argument('--expected-model', help='Optional approved physical model; rejects a different served model.')
    parser.add_argument('--max-calls', type=int, default=6, help='Gateway generation-call ceiling, at most6; Nightshift may make up to three upstream attempts per call.')
    parser.add_argument('--max-upstream-attempts', type=int, default=18,
                        help='Explicit approved worst-case upstream allowance, at most18; must cover three attempts per gateway call.')
    parser.add_argument('--max-output-tokens', type=int, default=600, help='Output-token ceiling per actual request, including the explanation.')
    parser.add_argument('--output', type=Path, help='New private verification directory; an existing directory is never overwritten.')
    args = parser.parse_args()
    output = args.output or ROOT / ('artifacts/private/live-flow-' + uuid.uuid4().hex[:12])
    try:
        receipt = verify(output, history_id=args.history, target_id=args.target, model=args.model,
                         expected_sample=args.expected_sample, allow_hosted_inference=args.allow_hosted_inference,
                         approved_route=args.approved_route, expected_provider=args.expected_provider,
                         expected_model=args.expected_model, max_calls=args.max_calls, max_output_tokens=args.max_output_tokens,
                         max_upstream_attempts=args.max_upstream_attempts)
    except (OSError, ValueError):
        print(json.dumps({'status': 'not_started', 'detail': 'Could not create a new private verification directory.'}))
        return 1
    print(json.dumps({'status': receipt['status'], 'receipt': str(output / 'receipt.json'),
                      'layers': {key: value['state'] for key, value in receipt['layers'].items()},
                      'completed_calls': sum(call.get('status') == 'completed' for call in receipt['provider_calls']),
                      'failed_layer': receipt.get('failed_layer')}, indent=2))
    return 0 if receipt['status'] == 'passed' else 130 if receipt['status'] == 'interrupted' else 1


if __name__ == '__main__':
    raise SystemExit(main())
