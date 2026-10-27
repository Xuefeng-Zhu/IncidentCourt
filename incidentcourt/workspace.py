"""Private, source-scoped case history. Never exposed to investigation SQL/tools."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .config import ROOT

MAX_REPORT_BYTES = 5 * 1024 * 1024
ACTIVE = ('queued', 'running')
BOOTSTRAP_LAYERS = {'configuration', 'zetaris_connection', 'application_tables',
                    'expected_sample_counts', 'history_investigation', 'target_investigation',
                    'remote_query_provenance', 'citations', 'nightshift_execution',
                    'memory_consumption', 'holdout_isolation'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class WorkspaceError(ValueError):
    def __init__(self, message, status=400, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def source_context():
    mode = 'sample' if os.environ.get('SAMPLE_MODE', '').lower() in ('1', 'true') else os.environ.get('INCIDENTCOURT_DATA_MODE', 'local')
    identity = {'mode': mode}
    if mode == 'zetaris':
        identity.update({k: os.environ.get(k, '') for k in ('ZETARIS_BASE_URL', 'ZETARIS_ORG_ID', 'ZETARIS_TABLE_PREFIX')})
    else:
        manifest = ROOT / ('data/public/sample-manifest.json' if mode == 'sample' else 'data/public/manifest.json')
        identity['manifest'] = hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.exists() else 'not-imported'
    return {'id': hashlib.sha256(canonical(identity).encode()).hexdigest()[:24], 'mode': mode,
            'label': {'zetaris': 'Zetaris archive', 'sample': 'Offline sample', 'local': 'Local archive'}.get(mode, mode)}


def validate_report(report):
    """Bound untrusted imports and verify evidence lineage; not narrative accuracy."""
    def require(condition):
        if not condition:
            raise WorkspaceError('Choose a complete IncidentCourt report with valid evidence and query references.')
    def text(value, maximum=12000):
        return isinstance(value, str) and 0 < len(value) <= maximum
    require(isinstance(report, dict))
    require(bool(re.fullmatch(r'IC-\d{3}', str(report.get('incident_id', '')))))
    require(bool(re.fullmatch(r'[a-f0-9]{12}', str(report.get('run_id', '')))))
    require(bool(re.fullmatch(r'(sample|local|zetaris) / (local|nightshift)', str(report.get('mode', '')))))
    data_mode, model_mode = report['mode'].split(' / ')
    require(text(report.get('summary')) and isinstance(report.get('model'), dict) and report['model'].get('mode') == model_mode)
    require(isinstance(report.get('memory'), dict) and text(report['memory'].get('mode'), 120) and text(report.get('created_at'), 80))
    matches = report['memory'].get('matches', [])
    require(isinstance(matches, list) and len(matches) <= 50)
    for match in matches:
        require(isinstance(match, dict))
        record = match.get('memory', match)
        require(isinstance(record, dict) and text(record.get('id'), 120))
    try:
        require(datetime.fromisoformat(report['created_at'].replace('Z', '+00:00')).tzinfo is not None)
        require(len(canonical(report).encode()) <= MAX_REPORT_BYTES)
    except (ValueError, TypeError, RecursionError):
        raise WorkspaceError('Report contains invalid dates, values, or exceeds the import limit.') from None
    for name, maximum in [('queries', 100), ('evidence', 500), ('steps', 100), ('timeline', 100), ('alternatives', 50), ('limitations', 100)]:
        require(isinstance(report.get(name), list) and len(report[name]) <= maximum)
    require(bool(report['queries']) and bool(report['evidence']) and bool(report['steps']))
    qids = set()
    for q in report['queries']:
        require(isinstance(q, dict) and text(q.get('id'), 120) and q['id'] not in qids)
        require(q.get('backend') == data_mode and text(q.get('purpose'), 2000) and text(q.get('sql'), 20000))
        require(q['sql'].lstrip().upper().startswith('SELECT ') and type(q.get('row_count')) is int and 0 <= q['row_count'] <= 1000)
        qids.add(q['id'])
    if 'analysis_window' in report:
        window = report['analysis_window']
        require(isinstance(window, dict))
        bounds = [window.get(key) for key in ('start_ts', 'baseline_end_ts', 'comparison_start_ts', 'end_ts')]
        require(all(type(value) in (int, float) and math.isfinite(value) for value in bounds))
        require(bounds[0] < bounds[1] <= bounds[2] < bounds[3])
        require(isinstance(window.get('query_id'), str) and window['query_id'] in qids)
    eids = set()
    for e in report['evidence']:
        require(isinstance(e, dict) and text(e.get('id'), 120) and e['id'] not in eids and e.get('query_id') in qids)
        require(e.get('kind') in ('metrics', 'logs', 'traces', 'counterevidence') and text(e.get('service'), 200) and text(e.get('summary')))
        require(isinstance(e.get('rows'), list) and len(e['rows']) <= 1000)
        for row in e['rows']:
            require(isinstance(row, dict) and len(row) <= 100)
            for key, value in row.items():
                require(isinstance(key, str) and len(key) <= 120)
                require(value is None or isinstance(value, bool) or isinstance(value, (int, float)) and math.isfinite(value) or isinstance(value, str) and len(value) <= 30000)
        eids.add(e['id'])
    def refs(value):
        return isinstance(value, list) and len(value) <= 500 and all(isinstance(v, str) and v in eids for v in value)
    for step in report['steps']:
        require(isinstance(step, dict) and text(step.get('role'), 100) and text(step.get('action'), 2000) and text(step.get('detail')) and refs(step.get('evidence_ids')))
        require(step.get('query_id') is None or step.get('query_id') in qids)
    for point in report['timeline']:
        require(isinstance(point, dict) and text(point.get('title'), 2000) and text(point.get('detail')) and refs(point.get('evidence_ids')))
        require(type(point.get('ts')) in (int, float) and math.isfinite(point['ts']))
    for item in report['alternatives']:
        require(isinstance(item, dict) and text(item.get('service'), 200) and text(item.get('reason')) and refs(item.get('evidence_ids')))
    require(all(text(v, 4000) for v in report['limitations']))
    for key in ('supporting_evidence_ids', 'counterevidence_ids'):
        require(key not in report or refs(report[key]))
    require(isinstance(report.get('counterevidence', []), list) and len(report.get('counterevidence', [])) <= 100)
    for item in report.get('counterevidence', []):
        require(isinstance(item, dict) and text(item.get('summary')) and refs(item.get('evidence_ids')))
    if model_mode == 'nightshift':
        explanation = report.get('provider_explanation', {})
        require(isinstance(explanation, dict) and text(explanation.get('summary')) and bool(explanation.get('evidence_ids')) and refs(explanation.get('evidence_ids')))
    require(report.get('likely_service') is None or text(report.get('likely_service'), 200))
    confidence = report.get('confidence')
    require(text(confidence, 100) or type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1)
    return report


class Workspace:
    def __init__(self, path=None, context=None):
        self.path = Path(path or ROOT / 'data/workspace.sqlite3')
        self.context = context or source_context()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.db() as db:
            db.executescript('''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,mode TEXT,label TEXT,cached_at TEXT,catalog_error TEXT);
CREATE TABLE IF NOT EXISTS cases(context_id TEXT,incident_id TEXT,data TEXT,PRIMARY KEY(context_id,incident_id));
CREATE TABLE IF NOT EXISTS investigations(
 id TEXT PRIMARY KEY,context_id TEXT NOT NULL,incident_id TEXT NOT NULL,linked INTEGER NOT NULL,
 state TEXT NOT NULL,stage TEXT NOT NULL,query_count INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,origin TEXT NOT NULL,request_key TEXT,
 run_id TEXT,report_hash TEXT,report TEXT,error TEXT,
 note TEXT NOT NULL DEFAULT '',reviewed_at TEXT,review_version INTEGER NOT NULL DEFAULT 0,
 UNIQUE(context_id,request_key));
CREATE INDEX IF NOT EXISTS investigations_context ON investigations(context_id,created_at);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
''')
            db.execute('INSERT OR IGNORE INTO contexts(id,mode,label) VALUES(?,?,?)', (self.context['id'], self.context['mode'], self.context['label']))
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cache_cases(self, records):
        if not isinstance(records, list) or len(records) > 100:
            raise WorkspaceError('Telemetry returned an invalid case list.')
        seen = set()
        for item in records:
            if not isinstance(item, dict) or not re.fullmatch(r'IC-\d{3}', str(item.get('id', ''))) or item['id'] in seen:
                raise WorkspaceError('Telemetry returned an invalid case list.')
            seen.add(item['id'])
            if len(canonical(item)) > 16000:
                raise WorkspaceError('Case metadata exceeds the workspace limit.')
        with self.db() as db:
            db.execute('DELETE FROM cases WHERE context_id=?', (self.context['id'],))
            db.executemany('INSERT INTO cases VALUES(?,?,?)', [(self.context['id'], x['id'], canonical(x)) for x in records])
            db.execute('UPDATE contexts SET cached_at=?,catalog_error=NULL WHERE id=?', (now(), self.context['id']))

    def catalog_failed(self):
        with self.db() as db:
            db.execute('UPDATE contexts SET catalog_error=? WHERE id=?', ('Telemetry could not be refreshed. Saved investigations remain available.', self.context['id']))

    def summary(self, row):
        value = {k: row[k] for k in ('id', 'incident_id', 'state', 'stage', 'query_count', 'created_at', 'updated_at', 'origin', 'run_id', 'error', 'reviewed_at', 'review_version')}
        value['linked'] = bool(row['linked'])
        value['has_note'] = bool(row['note'])
        value['review_state'] = 'Reviewed' if row['reviewed_at'] else 'Needs review'
        if row['report']:
            report = json.loads(row['report'])
            value.update(candidate=report.get('likely_service'), confidence=report.get('confidence'), evidence_count=len(report['evidence']), mode=report['mode'])
        return value

    def investigations(self):
        with self.db() as db:
            return [self.summary(row) for row in db.execute('SELECT * FROM investigations WHERE context_id=? ORDER BY created_at DESC,id DESC', (self.context['id'],))]

    def investigation(self, identity):
        if not re.fullmatch(r'[a-f0-9]{20}', str(identity)):
            raise WorkspaceError('Investigation not found.', 404)
        with self.db() as db:
            row = db.execute('SELECT * FROM investigations WHERE id=? AND context_id=?', (identity, self.context['id'])).fetchone()
            if not row:
                raise WorkspaceError('Investigation not found.', 404)
            return {**self.summary(row), 'report': json.loads(row['report']) if row['report'] else None,
                    'review': {'note': row['note'], 'reviewed': bool(row['reviewed_at']), 'version': row['review_version']}}

    def cases(self):
        runs = [r for r in self.investigations() if r['linked']]
        with self.db() as db:
            records = [json.loads(r['data']) for r in db.execute('SELECT data FROM cases WHERE context_id=? ORDER BY incident_id', (self.context['id'],))]
            context = dict(db.execute('SELECT * FROM contexts WHERE id=?', (self.context['id'],)).fetchone())
        # A verified historical case remains navigable if the source is offline.
        known = {r['id'] for r in records}
        for run in runs:
            if run['incident_id'] not in known:
                records.append({'id': run['incident_id'], 'title': run['incident_id'], 'source': self.context['label'], 'metadata_unavailable': True})
                known.add(run['incident_id'])
        for record in records:
            history = [r for r in runs if r['incident_id'] == record['id']]
            latest = history[0] if history else None
            completed = next((r for r in history if r['state'] == 'completed'), None)
            active = next((r for r in history if r['state'] in ACTIVE), None)
            state = 'Running' if active else 'Failed' if latest and latest['state'] in ('failed', 'interrupted') else ('Reviewed' if completed['reviewed_at'] else 'Needs review') if completed else 'Needs investigation'
            record.update(status=state, latest=latest, completed=completed, investigation_count=len(history))
        return {'cases': sorted(records, key=lambda r: r['id']), 'context': {k: context[k] for k in ('mode', 'label', 'cached_at', 'catalog_error')}, 'active': next((r for r in runs if r['state'] in ACTIVE), None)}

    def case(self, incident_id):
        if not re.fullmatch(r'IC-\d{3}', str(incident_id)):
            raise WorkspaceError('Incident not found.', 404)
        case = next((r for r in self.cases()['cases'] if r['id'] == incident_id), None)
        if not case:
            raise WorkspaceError('Incident not found.', 404)
        return {'case': case, 'investigations': [r for r in self.investigations() if r['linked'] and r['incident_id'] == incident_id]}

    def save_review(self, identity, note, reviewed, version):
        if not isinstance(note, str) or len(note) > 8000 or type(reviewed) is not bool or type(version) is not int:
            raise WorkspaceError('Review requires a note of at most 8,000 characters, a reviewed flag, and its current version.')
        current = self.investigation(identity)
        if current['state'] != 'completed':
            raise WorkspaceError('Only completed investigations can be reviewed.', 409)
        with self.db() as db:
            updated = db.execute('UPDATE investigations SET note=?,reviewed_at=?,review_version=review_version+1 WHERE id=? AND context_id=? AND review_version=?',
                                 (note, now() if reviewed else None, identity, self.context['id'], version))
            if updated.rowcount != 1:
                raise WorkspaceError('This review changed in another tab. Reload it before saving.', 409)
        return self.investigation(identity)['review']

    def save_report(self, report, origin='imported', linked=False, identity=None):
        validate_report(report)
        if linked and report['mode'].split(' / ')[0] != self.context['mode']:
            raise WorkspaceError('Reports from another telemetry source cannot be linked to this queue.')
        serialized = canonical(report)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        with self.db() as db:
            if identity is None:
                existing = db.execute('SELECT id FROM investigations WHERE context_id=? AND origin=? AND report_hash=?', (self.context['id'], origin, digest)).fetchone()
                if existing:
                    return existing['id']
                identity = uuid.uuid4().hex[:20]
                db.execute('INSERT INTO investigations(id,context_id,incident_id,linked,state,stage,created_at,updated_at,origin) VALUES(?,?,?,?,?,?,?,?,?)',
                           (identity, self.context['id'], report['incident_id'], int(linked), 'completed', 'Completed', report['created_at'], now(), origin))
            else:
                row = db.execute('SELECT state,incident_id FROM investigations WHERE id=? AND context_id=?', (identity, self.context['id'])).fetchone()
                if not row or row['state'] not in ACTIVE or row['incident_id'] != report['incident_id'] or report['mode'].split(' / ')[0] != self.context['mode']:
                    raise WorkspaceError('The completed report does not belong to this active investigation.', 409)
            db.execute('UPDATE investigations SET state=?,stage=?,query_count=?,updated_at=?,run_id=?,report_hash=?,report=? WHERE id=?',
                       ('completed', 'Completed', len(report['queries']), now(), report['run_id'], digest, serialized, identity))
        return identity

    def bootstrap(self, receipt_path, model):
        """Once per database: only explicitly configured, hash-verified live reports."""
        if self.context['mode'] != 'zetaris' or not receipt_path:
            return
        with self.db() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='verified_bootstrap'").fetchone():
                return
        try:
            path = Path(receipt_path)
            if not path.is_absolute(): path = ROOT / path
            path = path.resolve(strict=True)
            if not path.is_relative_to((ROOT / 'artifacts/private').resolve()) or path.stat().st_size > 1024 * 1024:
                return
            receipt = json.loads(path.read_text())
            if receipt.get('status') != 'passed' or receipt.get('policy', {}).get('approved_route') != model or set(receipt.get('layers', {})) != BOOTSTRAP_LAYERS or any(v.get('state') != 'passed' for v in receipt['layers'].values()):
                return
            reports = []
            for phase in ('history', 'target'):
                entry = receipt['reports'][phase]
                if entry['file'] != phase + '-report.json': return
                source = (path.parent / entry['file']).resolve(strict=True)
                if source.parent != path.parent or source.stat().st_size > MAX_REPORT_BYTES: return
                report = validate_report(json.loads(source.read_text()))
                if report['run_id'] != entry['run_id'] or hashlib.sha256(canonical(report).encode()).hexdigest() != entry['sha256'] or report['mode'] != 'zetaris / nightshift': return
                reports.append(report)
            for report in reports:
                self.save_report(report, origin='verification', linked=True)
            with self.db() as db:
                db.execute("INSERT OR IGNORE INTO metadata VALUES('verified_bootstrap',?)", (now(),))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return

    def previous_request(self, key):
        with self.db() as db:
            row = db.execute('SELECT id FROM investigations WHERE context_id=? AND request_key=?', (self.context['id'], key)).fetchone()
            return row['id'] if row else None

    def reserve(self, incident_id, key):
        self.case(incident_id)
        identity = uuid.uuid4().hex[:20]
        with self.db() as db:
            db.execute('INSERT INTO investigations(id,context_id,incident_id,linked,state,stage,created_at,updated_at,origin,request_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (identity, self.context['id'], incident_id, 1, 'queued', 'Starting', now(), now(), 'generated', key))
        return identity

    def progress(self, identity, stage, count):
        with self.db() as db:
            db.execute("UPDATE investigations SET state='running',stage=?,query_count=?,updated_at=? WHERE id=? AND state IN ('queued','running')", (str(stage)[:180], max(0, int(count)), now(), identity))

    def failed(self, identity):
        with self.db() as db:
            db.execute("UPDATE investigations SET state='failed',stage='Failed',updated_at=?,error=? WHERE id=? AND state IN ('queued','running')", (now(), 'Investigation failed. Existing receipts were retained. Check connections before starting a new run.', identity))

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE investigations SET state='interrupted',stage='Interrupted',updated_at=?,error=? WHERE state IN ('queued','running')", (now(), 'The server stopped before this run finished. It has not been restarted.',))


class JobManager:
    def __init__(self, workspace, run_lock, runner=None):
        self.workspace, self.run_lock = workspace, run_lock
        if runner is None:
            from .investigate import investigate
            runner = investigate
        self.runner = runner
        self.lock = threading.Lock()
        self.active_id = None
        self.threads = {}

    def start(self, incident_id, key, model):
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', key):
            raise WorkspaceError('A valid idempotency key is required.')
        with self.lock:
            existing = self.workspace.previous_request(key)
            if existing:
                value = self.workspace.investigation(existing)
                if value['incident_id'] != incident_id:
                    raise WorkspaceError('This request key belongs to another incident.', 409)
                return existing
            if not self.run_lock.acquire(blocking=False):
                raise WorkspaceError('An investigation is already running.', 409, investigation_id=self.active_id)
            try:
                identity = self.workspace.reserve(incident_id, key)
                self.active_id = identity
                thread = threading.Thread(target=self._run, args=(identity, incident_id, model), daemon=True)
                self.threads[identity] = thread
                thread.start()
                return identity
            except Exception:
                if self.active_id: self.workspace.failed(self.active_id)
                self.active_id = None
                self.run_lock.release()
                raise

    def _run(self, identity, incident_id, model):
        try:
            self.workspace.progress(identity, 'Reading incident', 0)
            report = self.runner(incident_id, model=model, progress=lambda stage, count: self.workspace.progress(identity, stage, count))
            self.workspace.save_report(report, identity=identity)
        except Exception:
            self.workspace.failed(identity)
        finally:
            with self.lock:
                self.active_id = None
                self.run_lock.release()
                self.threads.pop(identity, None)
