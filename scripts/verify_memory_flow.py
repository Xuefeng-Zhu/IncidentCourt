#!/usr/bin/env python3
"""Verify historical-memory consumption on anonymous real telemetry.

This is a behavior demonstration, not fault-localization accuracy evaluation.
Only the public telemetry SQLite database is read. Evaluator answers, original
case paths and injection timestamps are never opened. Fresh independent memory
stores prevent an existing investigation from contaminating the demonstration.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from incidentcourt.integrations import HMemStore
from incidentcourt.investigate import investigate
from incidentcourt.store import Store


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify(database, history_id, target_id, output):
    store = Store(path=database, mode='local')
    history = store.incident(history_id)
    target = store.incident(target_id)
    if history_id == target_id or history['split'] != 'development' or target['split'] != 'holdout':
        raise ValueError('Use a distinct development history incident and holdout target.')
    output.mkdir(parents=True, exist_ok=False)
    cold_memory = HMemStore(output / 'cold-memory')
    warm_memory = HMemStore(output / 'history-memory')
    # Persist the underlying real query receipts in this dedicated run folder.
    with patch('incidentcourt.investigate.ROOT', output):
        cold = investigate(target_id, store=Store(path=database, mode='local'), memory=cold_memory)
        previous = investigate(history_id, store=Store(path=database, mode='local'), memory=warm_memory)
        before = warm_memory.status()
        warm = investigate(target_id, store=Store(path=database, mode='local'), memory=warm_memory)
        after = warm_memory.status()
    baseline_sql = {q['sql'] for q in cold['queries']}
    extra_queries = [q for q in warm['queries'] if q['sql'] not in baseline_sql]
    memory_queries = [q for q in extra_queries if 'memory' in q['purpose'].lower()]
    matches = warm['memory']['matches']
    source_incidents = sorted({m.get('memory', m).get('missionId') for m in matches})
    checks = {
        'history_is_distinct_development_incident': True,
        'target_is_holdout': True,
        'cold_has_no_recalled_history': not cold['memory']['matches'],
        'warm_recalls_real_history': bool(matches) and source_incidents == [history_id],
        'recall_adds_actual_current_sql': bool(memory_queries),
        'extra_sql_targets_only_current_incident': bool(memory_queries) and all("incident_id='"+target_id+"'" in q['sql'] for q in memory_queries),
        'holdout_not_promoted_to_history': before['memories'] == after['memories'],
        'no_live_model_or_remote_data_claim': all(r['model']['mode'] == 'local' and r['mode'].startswith('local /') for r in (cold, previous, warm)),
    }
    receipt = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'passed' if all(checks.values()) else 'failed',
        'scope': 'Real RCAEval telemetry; local SQLite + official H-MEM reference; behavior demonstration only. No labels, live Zetaris, live Nightshift, or accuracy score.',
        'telemetry_sha256': sha256(database),
        'history_incident': history_id, 'target_incident': target_id,
        'report_run_ids': {'cold':cold['run_id'], 'history':previous['run_id'], 'warm':warm['run_id']},
        'checks': checks,
        'memory': {'before':before, 'after':after, 'trace':warm['memory']['trace'],
                   'source_incidents':source_incidents, 'matched_memory_ids':[m.get('memory',m)['id'] for m in matches]},
        'query_counts': {'cold':len(cold['queries']), 'warm':len(warm['queries']), 'additional_memory_sql':len(memory_queries)},
        'additional_current_queries':memory_queries,
        'conclusion_changed':cold['likely_service'] != warm['likely_service'],
        'interpretation':'Historical context should change what is checked. The conclusion may remain the same when current evidence warrants it.',
    }
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2, allow_nan=False)+'\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'data/telemetry.sqlite3')
    parser.add_argument('--history', default='IC-001')
    parser.add_argument('--target', default='IC-003')
    parser.add_argument('--output', type=Path, help='New run directory; defaults to ignored artifacts/private/memory-flow-<id>.')
    args = parser.parse_args()
    output = args.output or ROOT / ('artifacts/private/memory-flow-'+uuid.uuid4().hex[:12])
    receipt = verify(args.database.resolve(), args.history, args.target, output.resolve())
    print(json.dumps({'status':receipt['status'], 'history':args.history, 'target':args.target,
        'checks':receipt['checks'], 'query_counts':receipt['query_counts'], 'receipt':str(output / 'receipt.json')}, indent=2))
    return 0 if receipt['status'] == 'passed' else 1

if __name__ == '__main__':
    raise SystemExit(main())
