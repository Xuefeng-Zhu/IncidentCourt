"""Scoped reconciliation/resume tests. Transport is a fixture, never live SQL."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from incidentcourt.store import DataError
from scripts.prepare_zetaris_import import COLUMNS,make_plan
from scripts import resume_zetaris_registration as resume


class Client:
    def __init__(self,plan):
        self.plan=plan;self.calls=[];self.existing={};self.bad_listing=False
        self.error_response=False;self.timeout=False;self.publish=True
        self.mapping=('name','path','format')
    def query(self,sql):
        self.calls.append(('query',sql))
        if sql=='SELECT 1 AS connected':return [{'connected':1}]
        if sql=='SHOW LIGHTNING TABLES '+self.plan['namespace']:
            if self.bad_listing:return [{'unknown_column':'incidents'}]
            name_key,path_key,format_key=self.mapping
            return [{name_key:name,path_key:state['path'],format_key:state.get('format','PARQUET'),
                     'ignored_auth_field':'fixture-secret-must-not-be-saved'} for name,state in self.existing.items()]
        for check in self.plan['verification_queries']:
            if sql==check['sql']:
                state=self.existing[check['table']]
                return [{'observations':state.get('count',check['expected_observations'])}]
        raise AssertionError('Fixture received unexpected SQL')
    def request(self,path,body):
        self.calls.append(('request',body))
        if path!='/api/proxy/sql-editor/sqls/run-query':raise AssertionError('Unexpected API route')
        item=next((x for x in self.plan['statements'] if x['sql']==body['sql'] and x.get('table')),None)
        if not item:raise AssertionError('Only one canonical FILESTORE CREATE is permitted')
        if self.publish:self.add(item['table'])
        if self.timeout:raise DataError('Fixture transport timed out after sending DDL.')
        if self.error_response:return {'status':'error','msg':'fixture-secret-must-not-be-saved'}
        return {'headers':['Result'],'data':[],'total':0}
    def add(self,name):
        self.existing[name]={'path':self.plan['engine_path']+'/'+name+'.parquet'}


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        counts={'incidents':3,'metrics':10,'logs':9,'traces':12}
        files={name:{'file':name+'.parquet','rows':counts[name],'bytes':10,'sha256':'0'*64}
               for name in COLUMNS}
        self.plan=make_plan('incidentcourt_live_fixture','s3a://existing-bucket/incidentcourt/fixture',files,counts,'us-west-2')
        self.plan.update({'source_mode':'sample','total_rows':34,'total_bytes':40})
        self.client=Client(self.plan)
    def tearDown(self):self.temp.cleanup()
    def run_resume(self,table=None,name='run',**mapping):
        with patch.object(resume,'Zetaris',return_value=self.client),patch.dict(os.environ,{'ZETARIS_ENGINE_ID':'fixture-engine'}):
            return resume.run(self.plan,self.base/name,table,**mapping)
    def mutations(self):return [body for kind,body in self.client.calls if kind=='request']

    def test_default_reconciles_empty_namespace_read_only(self):
        result=self.run_resume()
        self.assertEqual(result['state'],'reconciled')
        self.assertEqual(result['before']['missing_tables'],list(COLUMNS))
        self.assertEqual(self.mutations(),[])
        self.assertEqual(len(self.client.calls),2)

    def test_exactly_one_missing_table_created_after_fresh_reconcile_and_verified(self):
        self.client.add('metrics')
        result=self.run_resume('incidents')
        self.assertEqual(result['state'],'table_verified')
        self.assertEqual(result['action']['state'],'verified')
        self.assertEqual(result['after']['missing_tables'],['logs','traces'])
        requests=self.mutations();self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['engineId'],'fixture-engine')
        self.assertTrue(requests[0]['sql'].startswith('CREATE LIGHTNING FILESTORE TABLE incidents '))
        self.assertFalse(any('CREATE LIGHTNING DATABASE' in str(call) or 'DROP ' in str(call) for call in self.client.calls))
        text=(self.base/'run/receipt.json').read_text()
        self.assertNotIn('fixture-secret-must-not-be-saved',text)

    def test_existing_table_is_never_recreated_and_unknown_listing_is_not_absence(self):
        self.client.add('incidents')
        with self.assertRaises(DataError):self.run_resume('incidents',name='existing')
        self.assertEqual(self.mutations(),[])
        self.client.existing={};self.client.bad_listing=True
        with self.assertRaises(DataError):self.run_resume('incidents',name='unknown')
        self.assertEqual(self.mutations(),[])

    def test_partial_wrong_path_or_count_blocks_all_mutations(self):
        self.client.add('metrics');self.client.existing['metrics']['path']='s3a://other-bucket/private/metrics.parquet'
        with self.assertRaises(DataError):self.run_resume('incidents',name='path')
        self.assertEqual(self.mutations(),[])
        self.client.add('metrics');self.client.existing['metrics']['count']=9
        with self.assertRaises(DataError):self.run_resume('incidents',name='count')
        self.assertEqual(self.mutations(),[])

    def test_explicit_mapping_from_actual_instance_headers_is_supported(self):
        self.client.add('metrics');self.client.mapping=('Table Name','File Path','File Format')
        result=self.run_resume(None,name_column='Table Name',path_column='File Path',format_column='File Format')
        self.assertEqual(result['before']['existing_tables'],['metrics'])
        self.assertEqual(self.mutations(),[])

    def test_uncertain_timeout_has_no_retry_and_can_be_reconciled_read_only(self):
        self.client.timeout=True
        with self.assertRaises(DataError):self.run_resume('incidents',name='timeout')
        self.assertEqual(len(self.mutations()),1)
        receipt=json.loads((self.base/'timeout/receipt.json').read_text())
        self.assertEqual(receipt['state'],'needs_reconciliation')
        self.assertEqual(receipt['action']['state'],'started')
        self.client.timeout=False
        result=self.run_resume(name='reconcile')
        self.assertEqual(result['before']['existing_tables'],['incidents'])
        self.assertEqual(len(self.mutations()),1)

    def test_error_or_missing_postcreate_discovery_never_becomes_verified(self):
        self.client.error_response=True
        with self.assertRaises(DataError):self.run_resume('incidents',name='error')
        self.assertEqual(len(self.mutations()),1)
        receipt=(self.base/'error/receipt.json').read_text()
        self.assertNotIn('fixture-secret-must-not-be-saved',receipt)
        self.client=Client(self.plan);self.client.publish=False
        with self.assertRaises(DataError):self.run_resume('incidents',name='unpublished')
        self.assertEqual(len(self.mutations()),1)
        self.assertEqual(json.loads((self.base/'unpublished/receipt.json').read_text())['state'],'needs_reconciliation')

    def test_malformed_or_out_of_scope_plan_fails_before_client(self):
        self.plan['verification_queries']=[]
        with patch.object(resume,'Zetaris') as client,self.assertRaises(ValueError):
            resume.run(self.plan,self.base/'invalid','incidents')
        client.assert_not_called()
        self.assertFalse((self.base/'invalid').exists())


if __name__=='__main__':unittest.main()
