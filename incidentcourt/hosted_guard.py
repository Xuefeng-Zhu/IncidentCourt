"""A durable single-run allowance for hosted browser investigations.

The budget records gateway requests before dispatch and checks each real receipt
before permitting another. A zero cost reported after a request cannot prevent
that request from being billed, or prove an account's billing status. The caller
must establish the approved free-credit-only route independently before use.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .integrations import IntegrationError, PROJECT_ROOT

MAX_GATEWAY_CALLS = 3
UPSTREAM_ATTEMPTS_PER_CALL = 3
MAX_UPSTREAM_ATTEMPTS = 9
MAX_OUTPUT_TOKENS = 600
_NONCE = re.compile(r"^[a-f0-9]{32}$")
_INCIDENT = re.compile(r"^IC-\d{3}$")


@dataclass(frozen=True)
class HostedRunPolicy:
    """Explicit local authorization for one incident on one pinned route."""

    nonce: str
    incident_id: str
    approved_route: str

    @property
    def nonce_hash(self):
        return hashlib.sha256(self.nonce.encode('ascii')).hexdigest()

    @classmethod
    def from_env(cls, client):
        nonce = os.environ.get('INCIDENTCOURT_HOSTED_RUN_NONCE', '')
        incident_id = os.environ.get('INCIDENTCOURT_HOSTED_RUN_INCIDENT', '')
        approved_route = os.environ.get('INCIDENTCOURT_APPROVED_HOSTED_ROUTE', '')
        if not (_NONCE.fullmatch(nonce) and _INCIDENT.fullmatch(incident_id)
                and approved_route.startswith('nvidia/nemotron-')
                and client.configured and client.allow_inference and client.hosted_only is True
                and client.model == approved_route):
            raise IntegrationError('A one-run hosted NVIDIA authorization is required; inference remains paused.')
        if approved_route.startswith(('nvidia/nemotron-3-', 'nvidia/nemotron-3.')) and client.enable_thinking is not False:
            raise IntegrationError('The approved NVIDIA Nemotron route requires thinking disabled.')
        assert_bounded_execution(client)
        return cls(nonce, incident_id, approved_route)


def assert_bounded_execution(client):
    """Require an enforceable per-request control, not a user-set assertion."""
    check = getattr(client, 'assert_bounded_execution', None)
    if not callable(check):
        raise IntegrationError('Nightshift cannot enforce the requested upstream-attempt bound for this browser run.')
    check()


class BudgetedNightshiftClient:
    """Pass through to Nightshift and verify each bounded runtime response."""

    def __init__(self, client, policy, run_id, output_root=None):
        if not re.fullmatch(r'[a-f0-9]{20}', str(run_id)):
            raise ValueError('Invalid workspace run id')
        self.client, self.policy = client, policy
        self.attempts = 0
        self.stopped = False
        self.receipt_ids, self.decision_ids = set(), set()
        base = Path(output_root) if output_root is not None else PROJECT_ROOT / 'artifacts/private/hosted-browser-runs'
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.output = base / run_id
        self.output.mkdir(mode=0o700, exist_ok=False)
        self.client.receipt_directory = self.output / 'provider-calls'
        self.journal = {'format': 1, 'scope': 'one browser investigation', 'run_id': run_id,
                        'incident_id': policy.incident_id, 'approved_route': policy.approved_route,
                        'gateway_generation_attempts': 0, 'max_gateway_calls': MAX_GATEWAY_CALLS,
                        'conservative_upstream_attempt_bound': 0,
                        'max_upstream_attempts': MAX_UPSTREAM_ATTEMPTS,
                        'actual_upstream_attempts': None, 'max_output_tokens': MAX_OUTPUT_TOKENS,
                        'provider_dispatch_stopped': False, 'calls': []}
        self._flush()

    def _flush(self):
        temporary = self.output / 'journal.json.part'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as target:
            json.dump(self.journal, target, indent=2, allow_nan=False)
            target.write('\n')
        temporary.replace(self.output / 'journal.json')

    def _validate_receipt(self, result, nonce, limit):
        if not isinstance(result, dict):
            raise IntegrationError('Nightshift did not return a verifiable response.')
        path_value = result.get('call_receipt')
        if not isinstance(path_value, str) or not path_value:
            raise IntegrationError('Nightshift did not retain its private call receipt.')
        path = Path(path_value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        try:
            path = path.resolve(strict=True)
            allowed = self.client.receipt_directory.resolve(strict=True)
            if path.parent != allowed or not path.is_file() or path.stat().st_size > 2_000_000:
                raise ValueError()
            call = json.loads(path.read_text())
        except (OSError, ValueError, TypeError):
            raise IntegrationError('Nightshift did not retain a valid private call receipt.') from None
        try:
            request, response = call['request'], call['response']
            receipt = response['runtime_receipt']
            winning = receipt['winning_route']
            actual = receipt['cost']['actual_usd']
            request_limit = request.get('max_tokens')
            output_tokens = response.get('usage', {}).get('completion_tokens', receipt.get('usage', {}).get('output_tokens'))
            valid = (call.get('status') == 'completed' and call.get('dispatched') is True
                and call.get('provider') == 'nightshift' and call.get('call_id') == result.get('call_id')
                and request.get('model') == self.policy.approved_route
                and isinstance(request_limit, int) and not isinstance(request_limit, bool) and 1 <= request_limit <= limit
                and nonce in json.dumps(request.get('messages', []))
                and response.get('served_model') == self.policy.approved_route
                and receipt.get('schema') == 'meterless.compute_receipt.v1'
                and receipt.get('requested_model') == self.policy.approved_route
                and receipt.get('routed_model') == self.policy.approved_route
                and winning.get('provider') == 'nvidia' and winning.get('model') == self.policy.approved_route
                and receipt.get('from_cache') is False and receipt.get('cache', {}).get('status') == 'miss'
                and receipt.get('privacy', {}).get('path') in ('cloud-ok', 'cloud')
                and isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == 0
                and isinstance(output_tokens, (int, float)) and not isinstance(output_tokens, bool)
                and 0 < output_tokens <= request_limit
                and response.get('runtime_receipt') == result.get('runtime_receipt')
                and response.get('served_model') == result.get('served_model')
                and response.get('content') == result.get('content')
                and isinstance(receipt.get('receipt_id'), str) and bool(receipt['receipt_id'])
                and isinstance(receipt.get('decision_id'), str) and bool(receipt['decision_id'])
                and receipt['receipt_id'] not in self.receipt_ids
                and receipt['decision_id'] not in self.decision_ids)
            if self.policy.approved_route.startswith(('nvidia/nemotron-3-', 'nvidia/nemotron-3.')):
                valid = valid and request.get('chat_template_kwargs', {}).get('enable_thinking') is False
            if not valid:
                raise ValueError()
        except (KeyError, AttributeError, TypeError, ValueError):
            raise IntegrationError('Nightshift did not report the approved uncached hosted route and zero estimated cost; further calls are stopped.') from None
        self.receipt_ids.add(receipt['receipt_id'])
        self.decision_ids.add(receipt['decision_id'])
        return {'call_id': call['call_id'], 'receipt_id': receipt['receipt_id'],
                'decision_id': receipt['decision_id'], 'actual_usd': actual,
                'output_tokens': output_tokens, 'max_output_tokens': request_limit}

    def complete(self, messages, max_tokens=1200):
        if self.stopped or self.attempts >= MAX_GATEWAY_CALLS:
            raise IntegrationError('The one-run hosted generation allowance is exhausted or stopped.')
        try:
            if not (isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and 1 <= max_tokens <= 4096):
                raise ValueError('Invalid requested output-token limit')
            if not (self.client.configured and self.client.allow_inference and self.client.hosted_only is True
                    and self.client.model == self.policy.approved_route):
                raise IntegrationError('Nightshift no longer matches the approved hosted configuration.')
            if (self.policy.approved_route.startswith(('nvidia/nemotron-3-', 'nvidia/nemotron-3.'))
                    and self.client.enable_thinking is not False):
                raise IntegrationError('The approved NVIDIA Nemotron route requires thinking disabled.')
            assert_bounded_execution(self.client)
            limit = min(max_tokens, MAX_OUTPUT_TOKENS)
            self.attempts += 1
            self.journal['gateway_generation_attempts'] = self.attempts
            self.journal['conservative_upstream_attempt_bound'] = self.attempts * UPSTREAM_ATTEMPTS_PER_CALL
            self._flush()  # A crash or timeout still consumes this attempt.
            nonce = 'incidentcourt-browser-' + uuid.uuid4().hex
            dispatched = [dict(item) for item in messages]
            marker = '\nVerification context nonce: ' + nonce + '. Keep the requested response schema unchanged; do not echo this nonce.'
            if dispatched and dispatched[0].get('role') == 'system' and isinstance(dispatched[0].get('content'), str):
                dispatched[0]['content'] += marker
            else:
                dispatched.insert(0, {'role': 'system', 'content': marker.strip()})
            result = self.client.complete(dispatched, max_tokens=limit)
            proof = self._validate_receipt(result, nonce, limit)
            self.journal['calls'].append(proof)
            self._flush()
            return result
        except BaseException:
            self.stopped = True
            self.journal['provider_dispatch_stopped'] = True
            try:
                self._flush()
            except Exception:
                pass  # Preserve the original failure; the in-memory guard stays stopped.
            raise
