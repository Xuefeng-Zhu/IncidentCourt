"""Workspace behavior with temporary databases and labeled fixture providers only."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from incidentcourt.workspace import Workspace, JobManager, WorkspaceError, validate_report, BOOTSTRAP_LAYERS
from incidentcourt import server
from tests.test_store_server import running
import http.client

ROOT = Path(__file__).resolve().parents[1]

def fixture_report():
    # Existing public sample output is a report-format fixture, not hosted proof.
    return json.loads((ROOT/'output_examples/example_1.json').read_text())

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'workspace.sqlite3'
        self.context = {'id':'sample-fixture', 'mode':'sample', 'label':'Fixture sample'}
        self.w = Workspace(self.path, self.context)
        self.w.cache_cases([{'id':'IC-001','title':'Fixture case','start_ts':0,'end_ts':90}, {'id':'IC-002','title':'Second fixture case'}])
    def tearDown(self): self.temp.cleanup()

    def test_review_persists_without_changing_original_or_other_source(self):
        report = fixture_report()
        identity = self.w.save_report(report, origin='verification', linked=True)
        before = self.w.investigation(identity)['report']
        self.assertEqual(self.w.case('IC-001')['case']['status'], 'Needs review')
        self.w.save_review(identity, 'Human observation, never a model prompt.', True, 0)
        restored = Workspace(self.path, self.context)
        self.assertEqual(restored.investigation(identity)['report'], before)
        self.assertEqual(restored.investigation(identity)['review']['note'], 'Human observation, never a model prompt.')
        self.assertEqual(restored.case('IC-001')['case']['status'], 'Reviewed')
        with self.assertRaises(WorkspaceError) as error:
            self.w.save_review(identity, 'stale tab', False, 0)
        self.assertEqual(error.exception.status, 409)
        alternate = Workspace(self.path, {'id':'another-source','mode':'sample','label':'Other fixture'})
        self.assertEqual(alternate.investigations(), [])
        with self.assertRaises(WorkspaceError): alternate.investigation(identity)

    def test_import_is_deduplicated_unlinked_and_cannot_be_rewritten(self):
        report = fixture_report()
        identity = self.w.save_report(report)
        self.assertEqual(identity, self.w.save_report(report))
        self.assertFalse(self.w.investigation(identity)['linked'])
        self.assertEqual(self.w.case('IC-001')['case']['status'], 'Needs investigation')
        with self.assertRaises(WorkspaceError): self.w.save_report(report, identity=identity)
        self.w.catalog_failed()
        self.assertEqual(len(self.w.cases()['cases']), 2)
        self.assertTrue(self.w.cases()['context']['catalog_error'])

    def test_malformed_imports_and_identifiers_are_rejected(self):
        for mutate in [lambda r:r['evidence'][0].update(query_id='Q-missing'),
                       lambda r:r.update(mode='zetaris / nightshift'),
                       lambda r:r['provider_explanation'].update(evidence_ids=['missing']) if 'provider_explanation' in r else r.update(counterevidence=None),
                       lambda r:r.update(created_at='not-a-date')]:
            value = fixture_report(); mutate(value)
            with self.assertRaises(WorkspaceError): validate_report(value)
        for identity in ['../../.env.local','receipt.json','f'*19]:
            with self.assertRaises(WorkspaceError): self.w.investigation(identity)
        with self.assertRaises(WorkspaceError): self.w.cache_cases([{'id':"IC-001' OR 1=1"}])
        with self.assertRaises(WorkspaceError): self.w.save_review('a'*20, 'x'*8001, False, 0)

    def test_active_run_is_idempotent_busy_and_resumable_from_db(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def provider(incident_id, model, progress):
            calls.append((incident_id,model)); progress('Fixture query completed', 2)
            entered.set(); release.wait(5)
            return fixture_report()
        manager = JobManager(self.w, threading.Lock(), runner=provider)
        key = 'fixture-request-key-001'
        identity = manager.start('IC-001', key, 'local')
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.w.investigation(identity)['query_count'], 2)
            self.assertEqual(manager.start('IC-001',key,'local'), identity)
            with self.assertRaises(WorkspaceError) as error:
                manager.start('IC-002','fixture-request-key-002','local')
            self.assertEqual(error.exception.status,409)
            self.assertEqual(len(calls),1)
            # A separate reader can resume status without repeating execution.
            self.assertEqual(Workspace(self.path,self.context).investigation(identity)['state'],'running')
            thread = manager.threads[identity]
        finally: release.set()
        thread.join(3)
        self.assertEqual(self.w.investigation(identity)['state'],'completed')
        self.assertEqual(manager.start('IC-001',key,'local'),identity)
        self.w.save_review(identity,'reviewed',True,0)
        rerun = manager.start('IC-001','fixture-request-key-003','local')
        thread = manager.threads.get(rerun)
        if thread:thread.join(3)
        self.assertFalse(self.w.investigation(rerun)['review']['reviewed'])
        self.assertEqual(len(calls),2)

    def test_failure_and_restart_never_dispatch_again(self):
        def failed(*args,**kwargs): raise RuntimeError('fixture secret must not be exposed')
        manager = JobManager(self.w,threading.Lock(),runner=failed)
        identity = manager.start('IC-001','fixture-failed-key-001','local')
        thread = manager.threads.get(identity)
        if thread:thread.join(3)
        self.assertEqual(self.w.investigation(identity)['state'],'failed')
        self.assertNotIn('fixture secret',json.dumps(self.w.investigation(identity)))
        interrupted = self.w.reserve('IC-002','fixture-interrupted-key')
        self.w.recover()
        self.assertEqual(self.w.investigation(interrupted)['state'],'interrupted')
        self.assertIsNone(self.w.investigation(interrupted)['report'])

    def test_bootstrap_only_explicit_hash_verified_reports_once(self):
        root=Path(self.temp.name); directory=root/'artifacts/private/verified';directory.mkdir(parents=True)
        import hashlib
        from incidentcourt.workspace import canonical
        reports={}
        for phase in ['history','target']:
            report=fixture_report();report['mode']='zetaris / nightshift';report['model']['mode']='nightshift'
            report['provider_explanation']={'summary':'Fixture only, not live inference.','evidence_ids':[report['evidence'][0]['id']]}
            for q in report['queries']:q['backend']='zetaris'
            if phase=='target':report['run_id']='123456abcdef';report['incident_id']='IC-002'
            name=phase+'-report.json';(directory/name).write_text(json.dumps(report))
            reports[phase]={'file':name,'run_id':report['run_id'],'sha256':hashlib.sha256(canonical(report).encode()).hexdigest()}
        receipt={'status':'passed','policy':{'approved_route':'nvidia/fixture'},'layers':{name:{'state':'passed'} for name in BOOTSTRAP_LAYERS},'reports':reports}
        path=directory/'receipt.json';path.write_text(json.dumps(receipt))
        w=Workspace(root/'bootstrap.sqlite3',{'id':'zetaris-fixture','mode':'zetaris','label':'Fixture'})
        with patch('incidentcourt.workspace.ROOT',root):
            receipt['reports']['target']['sha256']='0'*64;path.write_text(json.dumps(receipt))
            w.bootstrap(path,'nvidia/fixture');self.assertEqual(w.investigations(),[])
            receipt['reports']['target']=reports['target']={**reports['target'],'sha256':hashlib.sha256(canonical(json.loads((directory/'target-report.json').read_text())).encode()).hexdigest()}
            path.write_text(json.dumps(receipt));w.bootstrap(path,'nvidia/fixture');w.bootstrap(path,'nvidia/fixture')
        self.assertEqual(len(w.investigations()),2)
        self.assertTrue(all(not r['reviewed_at'] for r in w.investigations()))

class WorkspaceHTTPTests(unittest.TestCase):
    setUp = WorkspaceTests.setUp
    tearDown = WorkspaceTests.tearDown
    def test_workspace_http_contract_and_authorization(self):
        manager=JobManager(self.w,threading.Lock(),runner=lambda *a,**kw:fixture_report())
        def request(instance,method,path,value=None,origin=None):
            body=json.dumps(value or {}).encode();headers={'Host':f'127.0.0.1:{instance.server_port}','Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            connection=http.client.HTTPConnection('127.0.0.1',instance.server_port,timeout=5)
            connection.request(method,path,body if method=='POST' else None,headers)
            response=connection.getresponse();result=json.loads(response.read());connection.close()
            return response.status,result
        with patch.object(server,'workspace_service',return_value=(self.w,manager)),running(server.Handler) as instance:
            code,data=request(instance,'GET','/api/cases');self.assertEqual(code,200);self.assertEqual(len(data['cases']),2)
            code,data=request(instance,'POST','/api/investigations',{'incident_id':'IC-001','idempotency_key':'fixture-http-key-001'})
            self.assertEqual(code,202);identity=data['id']
            thread=manager.threads.get(identity)
            if thread:thread.join(2)
            code,data=request(instance,'GET','/api/investigations/'+identity);self.assertEqual(code,200);self.assertIsNotNone(data['report'])
            code,review=request(instance,'POST','/api/investigations/'+identity+'/review',{'note':'Fixture review','reviewed':True,'version':0});self.assertEqual(code,200)
            self.assertTrue(review['reviewed'])
            code,_=request(instance,'POST','/api/investigations/import',fixture_report(),origin='https://untrusted.invalid');self.assertEqual(code,403)
            code,data=request(instance,'POST','/api/investigations/import',fixture_report());self.assertEqual(code,201);self.assertFalse(data['linked'])
            code,_=request(instance,'GET','/api/investigations/../../.env.local');self.assertEqual(code,404)
        # The same product API must not dispatch an unauthorized hosted run.
        self.w.context['mode']='zetaris'
        with patch.object(server,'workspace_service',return_value=(self.w,manager)),patch.object(server.NightshiftClient,'from_env') as factory,running(server.Handler) as instance:
            factory.return_value.configured=True;factory.return_value.allow_inference=False;factory.return_value.hosted_only=True
            code,_=request(instance,'POST','/api/investigations',{'incident_id':'IC-002','idempotency_key':'fixture-paused-key-001'})
            self.assertEqual(code,403)

    def test_legacy_and_background_routes_share_the_execution_lock(self):
        entered, release = threading.Event(), threading.Event()
        def runner(*args, **kwargs):
            entered.set();release.wait(4);return fixture_report()
        manager = JobManager(self.w, server.RUN_LOCK, runner=runner)
        with patch.object(server,'workspace_service',return_value=(self.w,manager)),patch.object(server,'investigate') as legacy,running(server.Handler) as instance:
            identity=manager.start('IC-001','fixture-shared-lock-001','local')
            thread=manager.threads[identity]
            try:
                self.assertTrue(entered.wait(1))
                connection=http.client.HTTPConnection('127.0.0.1',instance.server_port,timeout=3)
                connection.request('POST','/run',json.dumps({'incident_id':'IC-002','model':'local'}),{'Content-Type':'application/json'})
                response=connection.getresponse();response.read();connection.close()
                self.assertEqual(response.status,409);legacy.assert_not_called()
            finally:release.set();thread.join(3)
            self.assertTrue(server.RUN_LOCK.acquire(blocking=False))
            try:
                with self.assertRaises(WorkspaceError):manager.start('IC-002','fixture-shared-lock-002','local')
            finally:server.RUN_LOCK.release()
