"""Prepare bounded typed Parquet and an official-syntax Zetaris registration plan.

Local preparation only: no upload, query, registration, resources or credentials.
Run as python -m scripts.prepare_zetaris_import --engine-path /actual/mounted/path.
The engine path must be an existing mount, native dffs folder, or private S3 prefix readable by the
driver/executors. Naming a laptop path or S3 object does not upload anything.

Syntax references:
https://github.com/zetaris/getting-started/blob/main/open_data/parquet_csv/HOWTO.md
https://kbase.zetaris.com/knowledge/sql-manual
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import sqlite3
from urllib.parse import urlsplit

from incidentcourt.config import ROOT
from incidentcourt.sample import ensure_sample_db

COLUMNS={
    'incidents':(('id','string'),('title','string'),('source','string'),('start_ts','float64'),('end_ts','float64'),('split','string')),
    'metrics':(('id','string'),('incident_id','string'),('ts','float64'),('service','string'),('name','string'),('value','float64')),
    'logs':(('id','string'),('incident_id','string'),('ts','float64'),('service','string'),('severity','string'),('message','string')),
    'traces':(('id','string'),('incident_id','string'),('ts','float64'),('service','string'),('span_id','string'),('parent_id','string'),('trace_id','string'),('duration_ms','float64'),('status','int64')),
}
MAX_ROWS=1_500_000
MAX_BYTES=50*1024*1024


def engine_path(value):
    if isinstance(value,str) and value.startswith('dffs://'):
        parsed=urlsplit(value)
        # Observed native NDP File Store contract: dffs://folder/file.parquet.
        # The folder is the URI authority; do not rewrite it as dffs:///folder.
        valid_path=(not parsed.path or parsed.path=='/' or
                    (re.fullmatch(r'/[A-Za-z0-9_./-]+',parsed.path)
                     and not any(p in ('.','..','') for p in parsed.path.strip('/').split('/'))))
        if (parsed.scheme!='dffs' or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}',parsed.netloc)
                or parsed.query or parsed.fragment or not valid_path):
            raise ValueError('NDP engine path needs the observed dffs://folder URI, without credentials, query or traversal.')
        return value.rstrip('/')
    if isinstance(value,str) and value.startswith('s3a://'):
        parsed=urlsplit(value)
        bucket=parsed.hostname or ''
        # Dotted bucket names are valid in S3 but have a reproduced Zetaris
        # connector failure; restrict this narrow preparation path to plain names.
        if (parsed.scheme!='s3a' or parsed.netloc!=bucket or parsed.query or parsed.fragment
                or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]',bucket)
                or not re.fullmatch(r'/[A-Za-z0-9_./-]+',parsed.path)
                or any(p in ('.','..','') for p in parsed.path.strip('/').split('/'))):
            raise ValueError('S3 engine path needs a plain existing bucket and bounded prefix, without credentials, query or traversal.')
        return value.rstrip('/')
    if (not isinstance(value,str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+',value)
            or any(p in ('.','..') for p in value.split('/'))):
        raise ValueError('Engine path must be an absolute mount, validated s3a bucket/prefix, or observed dffs://folder URI.')
    return value.rstrip('/')


def identifier(value):
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}',value):
        raise ValueError('Zetaris database name must be a simple identifier.')
    return value


def make_plan(namespace,visible_path,files,counts,s3_region=None):
    namespace=identifier(namespace);visible_path=engine_path(visible_path)
    is_s3=visible_path.startswith('s3a://')
    is_ndp=visible_path.startswith('dffs://')
    if s3_region is not None and (not is_s3 or not re.fullmatch(r'[a-z]{2}(?:-gov)?-[a-z]+-[0-9]',s3_region)):
        raise ValueError('S3 region must be the verified bucket region and applies only to S3 paths.')
    storage_options=''
    if is_s3:
        storage_options=', isS3BucketPublic "false", useS3PathStyleAccess "true"'
        if s3_region:
            suffix='amazonaws.com.cn' if s3_region.startswith('cn-') else 'amazonaws.com'
            storage_options+=f', s3Endpoint "s3.{s3_region}.{suffix}"'
    statements=[{'kind':'create_database','sql':f'CREATE LIGHTNING DATABASE {namespace} DESCRIBE BY "IncidentCourt anonymous RCAEval telemetry"'}]
    checks=[]
    for table in COLUMNS:
        statements.append({'kind':'register_file','table':table,
            'sql':f'CREATE LIGHTNING FILESTORE TABLE {table} FROM {namespace} FORMAT PARQUET OPTIONS (PATH "{visible_path}/{table}.parquet", inferSchema "true"{storage_options})'})
        checks.append({'table':table,'sql':f'SELECT COUNT(*) AS observations FROM {namespace}.{table}',
                       'expected_observations':counts[table]})
    return {'format':1,'state':'prepared_only','live_registration_verified':False,
        'engine_file_visibility_verified':False,'namespace':namespace,'table_prefix':namespace+'.',
        'engine_path':visible_path,'storage_kind':'s3' if is_s3 else 'ndp-file-system' if is_ndp else 'mounted-filesystem',
        'storage_authentication':'existing-engine-default-unverified' if is_s3 else 'existing-ndp-file-system-access' if is_ndp else 'existing-engine-filesystem-access',
        'storage_region':s3_region,'files':files,'statements':statements,'verification_queries':checks,
        'required_next_steps':['Confirm these exact files exist at engine_path and are readable by the existing engine role or filesystem identity.',
            'Use an existing authenticated SQL instance; run SELECT1 before any registration.',
            'Review and run each complete registration statement separately; existing objects require definition checks.',
            'Compare every verification count and then execute an investigation whose query receipts all say zetaris.'],
        'syntax_sources':['https://github.com/zetaris/getting-started/blob/main/open_data/parquet_csv/HOWTO.md',
                          'https://kbase.zetaris.com/knowledge/sql-manual',
                          'https://kbase.zetaris.com/knowledge/amazon-s3-storage',
                          'https://kbase.zetaris.com/knowledge/file-system-overview',
                          'https://hadoop.apache.org/docs/current/hadoop-aws/tools/hadoop-aws/authentication.html'],
        'limitations':['The generic official file-table syntax has not yet been tested against this IncidentCourt mount and Zetaris version.',
                       'This script makes no files public and transfers no data to an external service.',
                       'S3 plans omit access keys and tokens. Whether this Zetaris connector uses the existing engine role must be proved by real SELECT results; no Zetaris-specific IAM option is assumed.']}


def prepare(source,directory,visible_path,namespace='incidentcourt',s3_region=None):
    # Validate before creating any files or importing optional export dependencies.
    make_plan(namespace,visible_path,{},dict.fromkeys(COLUMNS,0),s3_region)
    if source not in ('sample','full'):
        raise ValueError('Source must be sample or full.')
    database=ROOT/('data/sample.sqlite3' if source=='sample' else 'data/telemetry.sqlite3')
    if source=='sample':ensure_sample_db(database)
    if not database.exists():raise ValueError('Import telemetry before preparing a full export.')
    import pyarrow as pa
    import pyarrow.parquet as pq
    types={'string':pa.string(),'float64':pa.float64(),'int64':pa.int64()}
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    files,counts,used_rows,used_bytes={}, {},0,0
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as con:
        con.row_factory=sqlite3.Row
        for table,fields in COLUMNS.items():
            count=con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            used_rows+=count
            if used_rows>MAX_ROWS:raise ValueError('Export exceeds the bounded1.5million-row project budget.')
            names=','.join(name for name,_ in fields)
            schema=pa.schema([pa.field(name,types[kind],nullable=True) for name,kind in fields])
            path=directory/(table+'.parquet')
            partial=path.with_suffix('.parquet.part')
            try:
                with pq.ParquetWriter(partial,schema,compression='zstd') as writer:
                    cursor=con.execute(f'SELECT {names} FROM {table} ORDER BY id')
                    while rows:=cursor.fetchmany(10000):
                        writer.write_table(pa.Table.from_pylist([dict(r) for r in rows],schema=schema))
                        if partial.stat().st_size+used_bytes>MAX_BYTES:
                            raise ValueError('Export exceeds the bounded50MiB file budget.')
                size=partial.stat().st_size
                if size+used_bytes>MAX_BYTES:raise ValueError('Export exceeds the bounded50MiB file budget.')
                partial.replace(path)
            finally:partial.unlink(missing_ok=True)
            used_bytes+=size;counts[table]=count
            files[table]={'file':path.name,'rows':count,'bytes':size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                          'schema':[{'name':name,'type':kind} for name,kind in fields]}
    plan=make_plan(namespace,visible_path,files,counts,s3_region)
    plan.update({'source_mode':source,'total_rows':used_rows,'total_bytes':used_bytes,
                 'data_source':'RCAEval RE2-OB, actual telemetry; sampled subset' if source=='sample' else 'RCAEval RE2-OB, bounded full three-case subset'})
    (directory/'registration-plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    sql='-- PREPARED ONLY. Engine visibility and live registration are not verified.\n-- Run one complete statement per call after checking the intended existing instance.\n\n'
    sql+=';\n\n'.join(s['sql'] for s in plan['statements'])+';\n\n'
    sql+=';\n'.join(c['sql'] for c in plan['verification_queries'])+';\n'
    (directory/'registration.sql').write_text(sql)
    return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=('sample','full'),default='sample')
    parser.add_argument('--output',type=Path,default=ROOT/'data/staging/zetaris')
    parser.add_argument('--engine-path',required=True,help='Existing engine mount, s3a://bucket/project-prefix or observed dffs://folder; no upload is performed')
    parser.add_argument('--s3-region',help='Verified existing bucket region; emits documented regional s3Endpoint')
    parser.add_argument('--database',default='incidentcourt')
    args=parser.parse_args()
    try:plan=prepare(args.source,args.output,args.engine_path,args.database,args.s3_region)
    except (OSError,ValueError,ImportError,sqlite3.Error):
        raise SystemExit('Zetaris preparation failed; check the typed source, optional Arrow dependency, path and bounded budgets.') from None
    print(json.dumps({'state':plan['state'],'source_mode':plan['source_mode'],'total_rows':plan['total_rows'],
                      'total_bytes':plan['total_bytes'],'live_registration_verified':False},indent=2))


if __name__=='__main__':main()
