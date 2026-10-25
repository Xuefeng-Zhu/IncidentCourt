"""No live calls: registration regressions use fixture plans and transport only."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
import io

from incidentcourt.store import DataError,Zetaris
from scripts import register_zetaris
from scripts.prepare_zetaris_import import COLUMNS,make_plan


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.counts={'incidents':3,'metrics':10,'logs':9,'traces':12}
        self.files={}
        for table,fields in COLUMNS.items():
            payload=('synthetic transport test bytes: '+table).encode()
            (self.base/(table+'.parquet')).write_bytes(payload)
            self.files[table]={'file':table+'.parquet','rows':self.counts[table],
                'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
                'schema':[{'name':name,'type':kind} for name,kind in fields]}
        self.plan=make_plan('incidentcourt_live_fixture','s3a://existing-bucket/incidentcourt/fixture',
                            self.files,self.counts,'us-west-2')
        self.plan.update({'source_mode':'sample','total_rows':sum(self.counts.values()),
                          'total_bytes':sum(f['bytes'] for f in self.files.values())})
        self.environment=patch.dict(os.environ,{
            'ZETARIS_BASE_URL':'https://fixture.invalid','ZETARIS_ORG_ID':'1',
            'ZETARIS_API_KEY':'','ZETARIS_USERNAME':'fixture-user',
            'ZETARIS_PASSWORD':'fixture-password','ZETARIS_ENGINE_ID':'fixture-engine'},clear=False)
        self.environment.start()
        self.client=Zetaris()
        self.calls=[]
        self.ddl_failure_at=None
        self.ddl_response={'ok':True}
        self.connected=[['1']]
        self.mismatch_table=None

    def tearDown(self):
        self.environment.stop();self.temp.cleanup()

    def transport(self,path,body):
        self.calls.append({'path':path,'body':body,'authenticated':self.client.token=='fixture-access-token'})
        if path=='/api/auth/login':return {'idToken':'fixture-access-token'}
        sql=body['sql']
        if sql=='SELECT 1 AS connected':return {'headers':['connected'],'data':self.connected}
        if sql.startswith('CREATE '):
            ddl_count=sum(c['body'].get('sql','').startswith('CREATE ') for c in self.calls)
            if ddl_count==self.ddl_failure_at:raise DataError('Fixture DDL failed.')
            return self.ddl_response
        for table,count in self.counts.items():
            if sql==f'SELECT COUNT(*) AS observations FROM incidentcourt_live_fixture.{table}':
                return {'headers':['observations'],'data':[[str(count-1 if table==self.mismatch_table else count)]]}
        raise AssertionError('Unexpected SQL was sent to the fixture transport')

    def execute(self,plan=None,output=None):
        with patch.object(register_zetaris,'Zetaris',return_value=self.client),patch.object(self.client,'request',side_effect=self.transport):
            return register_zetaris.execute(plan or self.plan,output or self.base/'receipt')

    def test_success_reuses_login_and_exactly_five_owned_creates_with_count_checks(self):
        result=self.execute()
        self.assertEqual(result['state'],'passed')
        self.assertEqual(len(result['actions']),5)
        self.assertEqual(len(result['checks']),4)
        self.assertTrue(all(r['passed'] for r in result['checks']))
        self.assertEqual(sum(c['path']=='/api/auth/login' for c in self.calls),1)
        self.assertTrue(all(c['authenticated'] for c in self.calls if c['path']!='/api/auth/login'))
        self.assertTrue(all(c['body'].get('engineId')=='fixture-engine' for c in self.calls if c['path']!='/api/auth/login'))
        texts=[c['body'].get('sql','') for c in self.calls]
        self.assertFalse(any('DROP ' in sql or 'REPLACE ' in sql or ';' in sql for sql in texts))
        path=self.base/'receipt/receipt.json'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertNotIn('fixture-access-token',path.read_text())
        self.assertNotIn('fixture-password',path.read_text())

    def test_invalid_plan_scope_duplicates_or_missing_checks_fail_before_network(self):
        variants=[]
        p=deepcopy(self.plan);p['verification_queries']=[];variants.append(p)
        p=deepcopy(self.plan);p['verification_queries'][1]=p['verification_queries'][0];variants.append(p)
        p=deepcopy(self.plan);p['statements'][1]=p['statements'][2];variants.append(p)
        p=deepcopy(self.plan);p['statements'][0]['sql']='CREATE LIGHTNING DATABASE unrelated';variants.append(p)
        p=deepcopy(self.plan);p['statements'][1]['sql']=p['statements'][1]['sql'].replace('existing-bucket/incidentcourt/fixture','other-bucket/private');variants.append(p)
        p=deepcopy(self.plan);p['verification_queries'][0]['expected_observations']=0;variants.append(p)
        p=deepcopy(self.plan);p['files']['metrics']['rows']=True;variants.append(p)
        p=deepcopy(self.plan);p['files']['metrics']['rows']=1_500_001;variants.append(p)
        p=deepcopy(self.plan);p['files']['metrics']['bytes']=50*1024*1024+1;variants.append(p)
        p=deepcopy(self.plan);p['files']['metrics']['sha256']='bad-digest';variants.append(p)
        p=deepcopy(self.plan);p['files']['logs']['file']='../private.parquet';variants.append(p)
        p=deepcopy(self.plan);p['total_rows']+=1;variants.append(p)
        for index,plan in enumerate(variants):
            self.calls=[]
            with self.subTest(index=index),self.assertRaises((ValueError,DataError)):
                self.execute(plan,self.base/f'invalid-{index}')
            self.assertEqual(self.calls,[],'Reject a malformed or out-of-scope plan before authenticating or creating anything')

    def test_select_one_must_be_actual_one_before_any_ddl(self):
        for index,connected in enumerate(([],[['0']])):
            self.connected=connected;self.calls=[];self.client.token=''
            with self.subTest(connected=connected),self.assertRaises(DataError):
                self.execute(output=self.base/f'connect-{index}')
            self.assertFalse(any(c['body'].get('sql','').startswith('CREATE ') for c in self.calls))

    def test_failed_ddl_is_retained_and_never_retried_repaired_or_dropped(self):
        self.ddl_failure_at=2
        with self.assertRaises(DataError):self.execute()
        sqls=[c['body'].get('sql','') for c in self.calls]
        self.assertEqual(sum(sql.startswith('CREATE ') for sql in sqls),2)
        self.assertFalse(any(sql.startswith('DROP ') for sql in sqls))
        self.assertFalse(any('COUNT(*)' in sql for sql in sqls))
        receipt=json.loads((self.base/'receipt/receipt.json').read_text())
        self.assertEqual(receipt['state'],'failed')
        self.assertEqual(receipt['actions'][-1]['state'],'started')

    def test_explicit_error_status_is_not_treated_as_ddl_success(self):
        self.ddl_response={'status':'error','message':'fixture-private-response'}
        with self.assertRaises(DataError):self.execute()
        self.assertEqual(sum(c['body'].get('sql','').startswith('CREATE ') for c in self.calls),1)
        receipt=json.loads((self.base/'receipt/receipt.json').read_text())
        self.assertNotIn('fixture-private-response',receipt.get('failure',{}).get('detail',''))

    def test_count_mismatch_fails_and_retains_observed_count_without_retry(self):
        self.mismatch_table='metrics'
        with self.assertRaises(DataError):self.execute()
        receipt=json.loads((self.base/'receipt/receipt.json').read_text())
        self.assertEqual(receipt['state'],'failed')
        self.assertEqual(len(receipt['checks']),2)
        self.assertEqual(receipt['checks'][-1]['actual_observations'],9)
        self.assertFalse(receipt['checks'][-1]['passed'])
        counts=[c['body'].get('sql','') for c in self.calls if 'COUNT(*)' in c['body'].get('sql','')]
        self.assertEqual(len(counts),len(set(counts)))

    def test_default_cli_is_dry_run_without_client_or_mutation(self):
        path=self.base/'plan.json';path.write_text(json.dumps(self.plan))
        stdout=io.StringIO()
        with patch('sys.argv',['register_zetaris','--plan',str(path)]),patch.object(register_zetaris,'Zetaris') as client,redirect_stdout(stdout):
            register_zetaris.main()
        client.assert_not_called()
        summary=json.loads(stdout.getvalue())
        self.assertEqual(summary['state'],'prepared_only')
        self.assertEqual(summary['statements'],5)
        self.assertNotIn('s3a://',stdout.getvalue())
        self.assertNotIn('fixture-password',stdout.getvalue())

    def test_same_receipt_directory_cannot_be_reused_to_repeat_mutations(self):
        output=self.base/'existing';output.mkdir()
        with self.assertRaises(FileExistsError):self.execute(output=output)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
