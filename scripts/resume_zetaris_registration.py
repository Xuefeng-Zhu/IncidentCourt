"""Reconcile an existing IncidentCourt namespace, optionally create one missing table.

Default performs live read-only SELECT1, SHOW LIGHTNING TABLES and count checks.
--execute-one TABLE first reconciles current state, sends exactly one canonical
missing FILESTORE CREATE, then rechecks metadata/counts. It never recreates the
Lightning database, drops/replaces tables or retries an uncertain mutation.

Official discovery syntax:
https://github.com/zetaris/getting-started/blob/main/docs/guides/zetaris-lightning-sql-commands.md
For nonempty listings provide the column names actually returned by the instance;
unknown listing schemas fail before any mutation.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import uuid

from incidentcourt.config import ROOT
from incidentcourt.store import DataError,Zetaris
from scripts.prepare_zetaris_import import COLUMNS
from scripts.register_zetaris import validate_plan


def reconcile(client,plan,*,name_column='name',path_column='path',format_column='format'):
    namespace=validate_plan(plan)
    rows=client.query('SELECT 1 AS connected')
    if rows!=[{'connected':1}]:
        raise DataError('Connectivity proof failed; no registration was attempted.')
    listing=client.query('SHOW LIGHTNING TABLES '+namespace)
    if not isinstance(listing,list) or any(not isinstance(r,dict) for r in listing):
        raise DataError('Lightning table listing shape is unrecognized; no registration was attempted.')
    found={}
    for row in listing:
        if not {name_column,path_column,format_column}<=set(row):
            raise DataError('Map the actual Lightning listing name/path/format columns before resuming.')
        name,path,format=row[name_column],row[path_column],row[format_column]
        if name not in COLUMNS or name in found:
            raise DataError('Lightning namespace contains duplicate or unowned table metadata; reconcile manually.')
        expected_path=plan['engine_path']+'/'+name+'.parquet'
        if path!=expected_path or not isinstance(format,str) or format.lower()!='parquet':
            raise DataError('An existing table path or format differs from the canonical source; no mutation is allowed.')
        found[name]={'table':name,'path_matches':True,'format':'parquet'}
    checks=[]
    for check in plan['verification_queries']:
        name=check['table']
        if name not in found:continue
        rows=client.query(check['sql'])
        count=rows[0].get('observations') if len(rows)==1 else None
        if type(count) is not int or count!=check['expected_observations']:
            raise DataError('An existing table count differs from the prepared source; no mutation is allowed.')
        checks.append({'table':name,'actual_observations':count,'expected_observations':check['expected_observations'],'passed':True})
    return {'state':'reconciled','namespace':namespace,'existing_tables':sorted(found),
            'missing_tables':[name for name in COLUMNS if name not in found],
            'metadata_checks':list(found.values()),'count_checks':checks,
            'checked_at':datetime.now(timezone.utc).isoformat()}


def run(plan,output,execute_one=None,*,name_column='name',path_column='path',format_column='format'):
    namespace=validate_plan(plan)
    if execute_one is not None and execute_one not in COLUMNS:
        raise ValueError('Choose exactly one owned telemetry table.')
    output=Path(output);output.mkdir(parents=True,exist_ok=False,mode=0o700)
    path=output/'receipt.json'
    receipt={'state':'started','namespace':namespace,'mode':'read-only' if execute_one is None else 'single-table-resume',
             'requested_table':execute_one,'started_at':datetime.now(timezone.utc).isoformat()}
    def save():
        path.write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n');path.chmod(0o600)
    save()
    try:
        client=Zetaris()
        mappings={'name_column':name_column,'path_column':path_column,'format_column':format_column}
        before=reconcile(client,plan,**mappings)
        receipt['before']=before;save()
        if execute_one is None:
            receipt['state']='reconciled';save();return receipt
        if execute_one not in before['missing_tables']:
            raise DataError('Requested table already exists and will not be recreated.')
        item=next(s for s in plan['statements'] if s.get('table')==execute_one and s['kind']=='register_file')
        receipt['action']={'table':execute_one,'sql':item['sql'],'state':'started'};save()
        body={'sql':item['sql'],'limit':1000}
        if os.environ.get('ZETARIS_ENGINE_ID'):body['engineId']=os.environ['ZETARIS_ENGINE_ID']
        response=client.request('/api/proxy/sql-editor/sqls/run-query',body)
        if isinstance(response,dict) and (response.get('error') or response.get('errors') or response.get('success') is False or str(response.get('status','')).lower() in ('error','failed','failure')):
            raise DataError('Single-table registration returned an execution error; reconcile before another attempt.')
        # Store response shape only; arbitrary server fields can contain sensitive
        # storage diagnostics. Successful metadata+count queries establish behavior.
        receipt['action']['response_shape']=sorted(response.keys()) if isinstance(response,dict) else type(response).__name__
        receipt['action']['state']='returned';save()
        after=reconcile(client,plan,**mappings)
        receipt['after']=after
        if execute_one not in after['existing_tables']:
            raise DataError('Created table is still missing from discovery; do not retry automatically.')
        receipt['action']['state']='verified'
        receipt['state']='table_verified';save();return receipt
    except Exception as error:
        receipt['state']='needs_reconciliation' if receipt.get('action') else 'failed_read_only'
        receipt['failure']={'category':type(error).__name__,
            'detail':str(error) if isinstance(error,DataError) else 'Resume validation failed; no retry was attempted.'}
        save();raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'data/staging/zetaris/registration-plan.json')
    parser.add_argument('--execute-one',choices=tuple(COLUMNS))
    parser.add_argument('--name-column',default='name')
    parser.add_argument('--path-column',default='path')
    parser.add_argument('--format-column',default='format')
    args=parser.parse_args()
    plan=json.loads(args.plan.read_text())
    output=ROOT/'artifacts/private'/('zetaris-resume-'+uuid.uuid4().hex[:12])
    try:
        receipt=run(plan,output,args.execute_one,name_column=args.name_column,path_column=args.path_column,format_column=args.format_column)
        print(json.dumps({'state':receipt['state'],'remaining_tables':receipt.get('after',receipt.get('before',{})).get('missing_tables',[]),'receipt':str(output/'receipt.json')}))
    except Exception:
        raise SystemExit('Zetaris resume stopped; inspect the private receipt before any further registration: '+str(output/'receipt.json')) from None


if __name__=='__main__':main()
