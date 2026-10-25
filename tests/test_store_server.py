"""Boundary regressions using temporary synthetic rows and fixture credentials only."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from incidentcourt import server
from incidentcourt.store import DataError, Store, Zetaris


class StoreBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'fixture.sqlite3'
        with sqlite3.connect(self.path) as con:
            con.executescript('''
CREATE TABLE incidents(id TEXT,title TEXT,source TEXT,start_ts REAL,end_ts REAL,split TEXT);
CREATE TABLE metrics(id TEXT,incident_id TEXT,ts REAL,service TEXT,name TEXT,value REAL);
CREATE TABLE logs(id TEXT,incident_id TEXT,ts REAL,service TEXT,severity TEXT,message TEXT);
CREATE TABLE traces(id TEXT,incident_id TEXT,ts REAL,service TEXT,span_id TEXT,parent_id TEXT,trace_id TEXT,duration_ms REAL,status INTEGER);
CREATE TABLE answer_key(secret TEXT);
INSERT INTO answer_key VALUES ('fixture-private-answer');
INSERT INTO incidents VALUES ('IC-001','Fixture incident','synthetic-test-only',0,90,'holdout');
INSERT INTO metrics VALUES ('m1','IC-001',1,'service-a','cpu',NULL);
''')
        self.store = Store(path=self.path, mode='local')
    def tearDown(self):
        self.temp.cleanup()

    def test_read_only_store_denies_private_tables_even_in_subquery(self):
        rows, receipt = self.store.query('fixture', 'SELECT id,value FROM metrics')
        self.assertEqual(rows, [{'id':'m1','value':None}])
        self.assertEqual(receipt, 'Q01')
        for sql in (
            'SELECT secret FROM answer_key',
            'SELECT (SELECT secret FROM answer_key) AS secret FROM metrics',
            'SELECT name FROM sqlite_master',
            'SELECT id FROM metrics UNION SELECT secret FROM answer_key',
            'DELETE FROM metrics',
            "ATTACH DATABASE 'file:private.sqlite' AS labels",
            'PRAGMA writable_schema=1',
            'SELECT id FROM metrics; DELETE FROM metrics',
        ):
            with self.subTest(sql=sql), self.assertRaises(DataError):
                self.store.query('rejected', sql)
        self.assertEqual(len(self.store.queries), 1)
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM metrics').fetchone()[0], 1)
        with self.assertRaises(DataError): self.store.table('answer_key')

    def test_excess_rows_raise_instead_of_silent_truncation(self):
        with sqlite3.connect(self.path) as con:
            con.executemany('INSERT INTO metrics VALUES (?,?,?,?,?,?)',
                [(f'm{i+2}','IC-001',i,'service-a','cpu',i) for i in range(1000)])
        with self.assertRaisesRegex(DataError, '1000'):
            self.store.query('too many', 'SELECT id FROM metrics')
        self.assertEqual(self.store.queries, [])

    def test_unknown_mode_and_failed_remote_never_fall_back_to_local(self):
        with self.assertRaises(DataError): Store(path=self.path, mode='invalid')
        with patch.dict(os.environ, {'ZETARIS_BASE_URL':'https://fixture.invalid','ZETARIS_ORG_ID':'1','ZETARIS_API_KEY':'fixture-token'}, clear=False):
            store = Store(path=self.path, mode='zetaris')
            with patch.object(store.remote, 'query', side_effect=DataError('fixture unavailable')):
                with self.assertRaisesRegex(DataError, 'fixture unavailable'):
                    store.incidents()
            self.assertEqual(store.queries, [])

    def test_invalid_incident_ids_are_validated_before_sql(self):
        for value in ("IC-001' OR 1=1", 'IC-001;DELETE', '../../data/private', 'IC-9999', None, [], {}):
            with self.subTest(value=value), self.assertRaises(DataError):
                self.store.incident(value)


@contextmanager
def running(handler):
    instance = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown(); instance.server_close(); thread.join()


class HTTPBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.instance = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.instance.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.instance.shutdown(); cls.instance.server_close(); cls.thread.join()

    def call(self, method, path, body=b'', headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.instance.server_port, timeout=3)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def test_no_sql_or_private_file_route(self):
        with patch.object(server, 'investigate') as run:
            for method, path in [('GET','/api/query?sql=SELECT%20*%20FROM%20answer_key'),
                                 ('POST','/api/query'),('GET','/data/private/answer-key.json'),
                                 ('GET','/../data/private/answer-key.json')]:
                with self.subTest(path=path):
                    status, body = self.call(method, path)
                    self.assertEqual(status, 404)
                    self.assertNotIn('fixture-private-answer', json.dumps(body))
            run.assert_not_called()

    def test_host_and_cross_origin_denied_before_investigation(self):
        with patch.object(server, 'investigate') as run:
            for headers in ({'Host':'attacker.invalid'},
                            {'Origin':'https://attacker.invalid','Content-Type':'application/json'}):
                status, _ = self.call('POST','/api/investigate',b'{}',headers)
                self.assertEqual(status, 403)
            status, _ = self.call('GET','/api/incidents',headers={'Host':'attacker.invalid'})
            self.assertEqual(status, 403)
            run.assert_not_called()

    def test_content_type_size_and_json_errors_are_explicit(self):
        with patch.object(server, 'investigate') as run:
            self.assertEqual(self.call('POST','/api/investigate',b'{}')[0], 415)
            for content in (b'', b'x'*4096):
                self.assertEqual(self.call('POST','/api/investigate',content,{'Content-Type':'application/json'})[0], 413)
            for content in (b'{', b'[]', b'null'):
                self.assertEqual(self.call('POST','/api/investigate',content,{'Content-Type':'application/json'})[0], 400)
            run.assert_not_called()

    def test_unknown_model_fails_closed_without_report_write(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'ROOT', Path(folder)):
            status, body = self.call('POST','/api/investigate',
                json.dumps({'incident_id':'IC-001','model':'automatic-fallback'}).encode(),
                {'Content-Type':'application/json'})
            self.assertEqual(status, 422)
            self.assertIn('local or nightshift', body['error'])
            self.assertFalse((Path(folder)/'artifacts/runs').exists())


class RedirectBoundaryTests(unittest.TestCase):
    def test_zetaris_redirect_cannot_forward_bearer_to_another_origin(self):
        captured = []
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_GET(self):
                captured.append(dict(self.headers))
                self.send_response(200); self.end_headers(); self.wfile.write(b'[]')
        with running(Receiver) as recipient:
            class Redirect(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def do_POST(self):
                    self.rfile.read(int(self.headers.get('Content-Length','0')))
                    self.send_response(302)
                    self.send_header('Location',f'http://127.0.0.1:{recipient.server_port}/receive')
                    self.end_headers()
            with running(Redirect) as endpoint, patch.dict(os.environ, {
                'ZETARIS_BASE_URL':f'http://127.0.0.1:{endpoint.server_port}',
                'ZETARIS_ORG_ID':'1','ZETARIS_API_KEY':'fixture-token-only'}, clear=False):
                with self.assertRaises(DataError):
                    Zetaris().query('SELECT 1')
        self.assertEqual(captured, [], 'Credentials must never reach the redirected origin')


if __name__ == '__main__':
    unittest.main()
