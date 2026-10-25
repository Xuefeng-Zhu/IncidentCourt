"""Register the prepared anonymous files in the intended existing SQL instance.

Default: display a sanitized plan summary only. --execute performs the five
owned CREATE statements and count checks, never drops/replaces existing objects,
changes IAM, creates credentials or changes infrastructure.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid
import os

from incidentcourt.config import ROOT
from incidentcourt.store import Zetaris, DataError
from incidentcourt.zetaris_contract import decode_rows
from scripts.prepare_zetaris_import import make_plan, COLUMNS, MAX_ROWS, MAX_BYTES


def validate_plan(plan):
    if not isinstance(plan,dict):
        raise ValueError('Registration plan must be an object.')
    namespace=plan.get('namespace','')
    if not isinstance(namespace,str) or not re.fullmatch(r'incidentcourt_live_[a-zA-Z0-9_]{1,45}',namespace):
        raise ValueError('Live registration requires a uniquely named IncidentCourt namespace.')
    if plan.get('source_mode') not in ('sample','full'):
        raise ValueError('Use the bounded project registration plan.')
    files=plan.get('files',{})
    if not isinstance(files,dict) or set(files)!=set(COLUMNS):
        raise ValueError('Exactly four anonymous telemetry files are required.')
    counts={}
    total_bytes=0
    for table,entry in files.items():
        if not isinstance(entry,dict) or entry.get('file')!=table+'.parquet':
            raise ValueError('Prepared telemetry filenames are invalid.')
        count,size=entry.get('rows'),entry.get('bytes')
        if type(count) is not int or count<1 or type(size) is not int or size<1:
            raise ValueError('Prepared telemetry bounds are invalid.')
        if not isinstance(entry.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',entry['sha256']):
            raise ValueError('Prepared telemetry hashes are invalid.')
        counts[table]=count;total_bytes+=size
    if not 0<sum(counts.values())<=MAX_ROWS or not 0<total_bytes<=MAX_BYTES:
        raise ValueError('Prepared telemetry exceeds the project budget.')
    if plan.get('total_rows')!=sum(counts.values()) or plan.get('total_bytes')!=total_bytes:
        raise ValueError('Prepared telemetry totals are inconsistent.')
    canonical=make_plan(namespace,plan.get('engine_path'),files,counts,plan.get('storage_region'))
    if plan.get('statements')!=canonical['statements'] or plan.get('verification_queries')!=canonical['verification_queries']:
        raise ValueError('Registration statements or count checks differ from the canonical owned plan.')
    return namespace


def execute(plan, output):
    namespace=validate_plan(plan)
    directory=Path(output)
    directory.mkdir(parents=True,exist_ok=False,mode=0o700)
    receipt={'state':'running','namespace':namespace,'source_mode':plan['source_mode'],
             'started_at':datetime.now(timezone.utc).isoformat(),'actions':[],'checks':[]}
    path=directory/'receipt.json'

    def save():
        path.write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n')
        path.chmod(0o600)

    def sql_request(client, sql):
        body={'sql':sql,'limit':1000}
        if os.environ.get('ZETARIS_ENGINE_ID'):
            body['engineId']=os.environ['ZETARIS_ENGINE_ID']
        result=client.request('/api/proxy/sql-editor/sqls/run-query',body)
        (directory/f"action-{len(receipt['actions'])}.json").write_text(json.dumps(result,indent=2))
        if isinstance(result,dict) and (result.get('error') or result.get('errors') or result.get('success') is False or str(result.get('status','')).lower() in ('error','failed','failure')):
            raise DataError('Zetaris reported a registration execution error; inspect the private response.')
        return result

    save()
    try:
        client=Zetaris()
        receipt['select_one']=client.query('SELECT 1 AS connected')
        save()
        if receipt['select_one']!=[{'connected':1}]:
            raise DataError('Zetaris SELECT 1 did not return the expected connectivity proof; no DDL was sent.')
        # No statement is split/repaired/retried. Unexpected DDL envelopes are
        # retained; subsequent real SELECTs establish whether registration worked.
        for item in plan['statements']:
            sql=item['sql']
            if not isinstance(sql,str) or not sql.startswith('CREATE LIGHTNING ') or ';' in sql:
                raise ValueError('Registration plan has an unexpected statement.')
            action={'kind':item['kind'],'table':item.get('table'),'sql':sql,'state':'started'}
            receipt['actions'].append(action); save()
            response=sql_request(client,sql)
            (directory/f"action-{len(receipt['actions'])}.json").write_text(json.dumps(response,indent=2))
            action['state']='returned'; save()
        for expected in plan['verification_queries']:
            rows=client.query(expected['sql'])
            count=rows[0].get('observations') if len(rows)==1 else None
            check={**expected,'actual_observations':count,'passed':count==expected['expected_observations']}
            receipt['checks'].append(check); save()
            if not check['passed']:
                raise DataError('Registered telemetry count did not match its prepared source.')
        receipt['state']='passed'
        receipt['table_prefix']=namespace+'.'
        save()
        return receipt
    except Exception as error:
        receipt['state']='failed'
        receipt['failure']={'category':type(error).__name__,'detail':str(error) if isinstance(error,DataError) else 'Registration validation failed.'}
        save()
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'data/staging/zetaris/registration-plan.json')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text())
    if not args.execute:
        print(json.dumps({'state':'prepared_only','namespace':plan['namespace'],'statements':len(plan['statements']),
                          'total_rows':plan['total_rows'],'total_bytes':plan['total_bytes']},indent=2))
        return
    directory=ROOT/'artifacts/private'/('zetaris-registration-'+uuid.uuid4().hex[:12])
    try:
        result=execute(plan,directory)
        print(json.dumps({'state':result['state'],'tables_verified':len(result['checks']),'receipt':str(directory/'receipt.json')}))
    except Exception:
        raise SystemExit('Zetaris registration failed; retained private receipt: '+str(directory/'receipt.json')) from None


if __name__=='__main__':main()
