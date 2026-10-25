"""Local-only HTTP application, serving static assets and bounded workflows."""
import argparse
import json
import os
import re
from datetime import datetime
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import ROOT
from .store import Store, DataError
from .integrations import HMemStore, NightshiftClient, IntegrationError
from .investigate import investigate

RUN_LOCK = threading.Lock()


VERIFICATION_LAYERS = (
    'configuration', 'zetaris_connection', 'application_tables',
    'history_investigation', 'target_investigation', 'remote_query_provenance',
    'citations', 'nightshift_execution', 'memory_consumption', 'holdout_isolation',
)


def last_verification(requested_model):
    """Allowlisted historical proof only; never a current connectivity check.

    The path is explicitly selected in project configuration. No HTTP parameter
    can select a private file, and no query, prompt, credential or path is returned.
    """
    configured = os.environ.get('INCIDENTCOURT_VERIFICATION_RECEIPT', '')
    if not configured:
        return None
    try:
        path = Path(configured)
        if not path.is_absolute():
            path = ROOT / path
        private = (ROOT / 'artifacts/private').resolve()
        path = path.resolve(strict=True)
        if not path.is_relative_to(private) or not path.is_file() or path.stat().st_size > 1024 * 1024:
            return None
        with path.open('rb') as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return None
        value = json.loads(raw)
        if value.get('status') != 'passed' or value.get('policy', {}).get('approved_route') != requested_model:
            return None
        layers = value['layers']
        if any(layers.get(name, {}).get('state') != 'passed' for name in VERIFICATION_LAYERS):
            return None
        if layers.get('expected_sample_counts', {}).get('state') not in ('passed', 'not_requested'):
            return None
        execution = layers['nightshift_execution']
        if execution.get('actual_gateway_calls') != 6 or execution.get('uncached') is not True:
            return None
        phases = execution['calls_by_phase']
        if any(not isinstance(phases.get(phase), list) or len(phases[phase]) != 3 for phase in ('history', 'target')):
            return None
        calls = phases['history'] + phases['target']
        if any(call.get('served_model') != requested_model
               or call.get('provider') != requested_model.split('/', 1)[0]
               or call.get('from_cache') is not False
               or isinstance(call.get('actual_usd'), bool) or call.get('actual_usd') != 0 for call in calls):
            return None
        if len({call['receipt_id'] for call in calls}) != 6 or len({call['decision_id'] for call in calls}) != 6:
            return None
        reports = value['reports']
        if any(not re.fullmatch(r'[a-f0-9]{12}', reports[phase].get('run_id', '')) for phase in ('history', 'target')):
            return None
        verified_at = value.get('updated_at') or value['created_at']
        if datetime.fromisoformat(verified_at.replace('Z', '+00:00')).tzinfo is None:
            return None
        queries = layers['remote_query_provenance'].get('actual_queries')
        if not isinstance(queries, int) or isinstance(queries, bool) or queries < 1:
            return None
        return {'historical': True, 'verified_at': verified_at,
                'physical_model': requested_model, 'gateway_calls': 6, 'remote_selects': queries,
                'history_run_id': reports['history']['run_id'], 'target_run_id': reports['target']['run_id'],
                'detail': 'Earlier completed verification; current connectivity is not rechecked.'}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def status():
    sample = os.environ.get('SAMPLE_MODE', '').lower() in ('1', 'true')
    mode = 'sample' if sample else os.environ.get('INCIDENTCOURT_DATA_MODE', 'local')
    db = ROOT / ('data/sample.sqlite3' if sample else 'data/telemetry.sqlite3')
    model = NightshiftClient.from_env().status()
    memory = HMemStore(ROOT / ('data/memory-sample' if sample else 'data/memory')).status()
    configured = model.get('state') == 'configured_unverified'
    enabled = bool(not sample and configured and model.get('inference_enabled'))
    hosted = model.get('hosted_only') is True
    if configured:
        model_detail = ('Hosted Nightshift model configured' if hosted else 'Nightshift configured')
        model_detail += '; new inference authorization is off. Open a saved record to review earlier execution.' if not enabled else '; authorized inference is enabled. Current provider connectivity is not checked by this status view.'
    else:
        model_detail = 'Nightshift runtime authentication is not configured; generation is unavailable.'
    return {'data': {'mode': mode if mode in ('zetaris', 'sample') or db.exists() else 'not_imported',
                     'detail': ('Curated real RCAEval subset; offline sample execution.' if sample else 'Local RCAEval telemetry; Zetaris path not verified.') if mode != 'zetaris' else 'Zetaris selected. Current connectivity is not checked by this status view.'},
            'model': {'mode': 'configured' if configured else 'not_configured', 'detail': model_detail,
                      'inference_enabled': enabled, 'hosted_only': hosted,
                      'requested_model': model.get('requested_model')},
            'memory': {'mode': 'official-hmem-reference' if memory.get('state') == 'working' else 'unavailable',
                       'detail': 'Official Meterless H-MEM reference; historical context rechecked against current telemetry.',
                       'reference': memory},
            'last_verified': last_verification(model.get('requested_model')),
            'integration_ready': False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Avoid logging request bodies, query strings, provider errors or credentials.
        pass

    def reply(self, value, code=200):
        body=json.dumps(value,allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        host=self.headers.get('Host','')
        return host in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.allowed_host():
            return self.reply({'error':'Local access only.'},403)
        path=urlparse(self.path).path
        try:
            if path=='/api/status': return self.reply(status())
            if path=='/api/incidents': return self.reply(Store().incidents())
            files={'/':('index.html','text/html'),'/static/styles.css':('styles.css','text/css'),'/static/app.js':('app.js','text/javascript')}
            if path not in files: return self.reply({'error':'Not found.'},404)
            name,mime=files[path]
            body=(ROOT/'incidentcourt/static'/name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',mime+'; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers(); self.wfile.write(body)
        except (DataError,IntegrationError,ValueError) as exc:
            self.reply({'error':str(exc)},503)
        except Exception:
            self.reply({'error':'Local application error; inspect configuration and data import.'},500)

    def do_POST(self):
        if not self.allowed_host(): return self.reply({'error':'Local access only.'},403)
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://localhost:{self.server.server_port}',f'http://127.0.0.1:{self.server.server_port}'):
            return self.reply({'error':'Cross-origin requests are disabled.'},403)
        if self.path not in ('/api/investigate','/run'): return self.reply({'error':'Not found.'},404)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.reply({'error':'JSON is required.'},415)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<4096: return self.reply({'error':'Invalid request size.'},413)
            payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict): raise ValueError()
        except (ValueError,TypeError): return self.reply({'error':'Invalid JSON request.'},400)
        if not RUN_LOCK.acquire(blocking=False): return self.reply({'error':'An investigation is already running.'},409)
        try:
            report=investigate(payload.get('incident_id',''),payload.get('model','local'))
            directory=ROOT/'artifacts/runs'
            directory.mkdir(parents=True,exist_ok=True)
            (directory/(report['run_id']+'.json')).write_text(json.dumps(report,indent=2,allow_nan=False))
            self.reply(report)
        except (DataError,IntegrationError,ValueError) as exc:
            self.reply({'error':str(exc)},422)
        except Exception:
            self.reply({'error':'Investigation failed; no completed report was saved.'},500)
        finally:
            RUN_LOCK.release()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8317)
    parser.add_argument('--host',default='127.0.0.1',choices=('127.0.0.1','0.0.0.0'))
    args=parser.parse_args()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'IncidentCourt: http://127.0.0.1:{args.port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
