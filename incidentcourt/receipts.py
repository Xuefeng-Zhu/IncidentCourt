"""Bounded, credential-free execution receipts."""
import json
from .config import ROOT


def write_trace(report, directory=None):
    directory=directory or ROOT/'logs'
    directory.mkdir(parents=True,exist_ok=True)
    rows=[]
    for step in report['steps']:
        rows.append({'timestamp':report['created_at'],'run_id':report['run_id'],
                     'agent_name':step['role'],'action':step['action'],'output_summary':step['detail'],
                     'evidence_ids':step['evidence_ids'],'query_id':step.get('query_id'),
                     'mode':report['mode']})
    for query in report['queries']:
        rows.append({'timestamp':report['created_at'],'run_id':report['run_id'],
                     'agent_name':'Investigator','action':'telemetry_query',**query})
    rows.append({'timestamp':report['created_at'],'run_id':report['run_id'],
                 'action':'synthesis','summary':report['summary'],'model':report['model'],
                 'memory_engine':report['memory']['mode'],'memory_matches':len(report['memory']['matches'])})
    (directory/f"trace-{report['run_id']}.jsonl").write_text(''.join(json.dumps(row,allow_nan=False)+'\n' for row in rows))
    print(json.dumps({'run_id':report['run_id'],'mode':report['mode'],'queries':len(report['queries']),
                      'critic_actions':[s['action'] for s in report['steps'] if s['role']=='Evidence Critic'],
                      'memory_matches':len(report['memory']['matches']),'status':'completed'}),flush=True)
