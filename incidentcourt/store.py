"""The agent can execute bounded telemetry templates, never arbitrary SQL."""
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from .config import ROOT
from .zetaris_contract import decode_rows, normalize_row, ZetarisContractError

TABLES = frozenset({'incidents', 'metrics', 'logs', 'traces'})


class DataError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


class Zetaris:
    """UI proxy contract from zetaris/getting-started/scripts/zetaris_api.py.

    Remote row envelope support is deliberately strict until checked on an instance.
    Never include response bodies or credentials in public exception messages.
    """
    def __init__(self):
        self.base = os.environ.get('ZETARIS_BASE_URL', '').rstrip('/')
        self.org = os.environ.get('ZETARIS_ORG_ID', '')
        self.token = os.environ.get('ZETARIS_API_KEY', '')
        self.opener = build_opener(NoRedirect)
        parsed = urlparse(self.base)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.path or parsed.query or parsed.fragment or parsed.username):
            raise DataError('Set ZETARIS_BASE_URL to the existing SQL UI origin.')
        if parsed.scheme != 'https' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise DataError('Remote Zetaris connections require HTTPS.')
        if not self.org.isdigit():
            raise DataError('Set the numeric ZETARIS_ORG_ID in .env.local.')

    def request(self, path, body):
        headers = {'Content-Type': 'application/json', 'X-Request-ID': str(uuid.uuid4())}
        if self.token:
            headers.update({'Authorization': f'Bearer {self.token}', 'X-Org-ID': self.org})
        req = Request(self.base + path, json.dumps(body).encode(), headers)
        try:
            with self.opener.open(req, timeout=30) as response:
                raw = response.read(8_000_001)
                if len(raw) > 8_000_000:
                    raise DataError('Zetaris response exceeded the bounded query budget.')
                return json.loads(raw)
        except HTTPError as error:
            diagnostic_id=None
            if path=='/api/proxy/sql-editor/sqls/run-query':
                # Retain enough SQL diagnostics to fix a real connector failure.
                # Never persist auth/login payloads, headers or known secrets.
                try:
                    raw=json.loads(error.read(16_000))
                    known=[s for s in (self.token,os.environ.get('ZETARIS_PASSWORD'),os.environ.get('ZETARIS_API_KEY')) if isinstance(s,str) and s]
                    def clean(value,depth=0):
                        if depth>6:
                            return '[bounded diagnostic]'
                        if isinstance(value,dict):
                            return {str(k)[:120]:('[redacted]' if re.search(r'(?i)password|secret|token|authorization|api.?key|access.?key|credential|cookie|headers',str(k)) else clean(v,depth+1))
                                    for k,v in list(value.items())[:30]}
                        if isinstance(value,list):
                            return [clean(v,depth+1) for v in value[:10]]
                        if isinstance(value,str):
                            for secret in known:
                                value=value.replace(secret,'[redacted]')
                            value=re.sub(r'(?i)Bearer\s+\S+','Bearer [redacted]',value)
                            value=re.sub(r'\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b','[redacted token]',value)
                            value=re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b','[redacted AWS key]',value)
                            return value[:2000]
                        return value if value is None or isinstance(value,(int,float,bool)) else None
                    diagnostic_id=uuid.uuid4().hex[:12]
                    directory=ROOT/'artifacts/private/zetaris-errors'
                    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
                    fd=os.open(directory/(diagnostic_id+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as target:
                        json.dump({'http_status':error.code,'safe_error':clean(raw)},target,indent=2)
                except (OSError,ValueError,TypeError):
                    diagnostic_id=None
            error.close()
            detail=f'; sanitized diagnostic {diagnostic_id}' if diagnostic_id else '; inspect instance access'
            raise DataError(f'Zetaris request failed (HTTP {error.code}){detail}.') from None
        except (OSError, URLError, ValueError):
            raise DataError('Zetaris connection or JSON response failed; verify the SQL UI origin.') from None

    def query(self, sql):
        if not self.token:
            username, password = os.environ.get('ZETARIS_USERNAME'), os.environ.get('ZETARIS_PASSWORD')
            if not username or not password:
                raise DataError('Set ZETARIS_API_KEY or ZETARIS_USERNAME and ZETARIS_PASSWORD locally.')
            login = self.request('/api/auth/login', {'username': username, 'password': password})
            if isinstance(login, dict):
                self.token = login.get('idToken') or login.get('token') or login.get('accessToken')
            if not self.token:
                raise DataError('Zetaris login returned no recognized access token.')
        body = {'sql': sql, 'limit': 1000}
        if os.environ.get('ZETARIS_ENGINE_ID'):
            body['engineId'] = os.environ['ZETARIS_ENGINE_ID']
        result = self.request('/api/proxy/sql-editor/sqls/run-query', body)
        # Do not guess, replay cached local data, or discard errors as empty results.
        try:
            if isinstance(result,dict) and ('headers' in result or 'data' in result):
                return decode_rows(result)
            if isinstance(result,dict) and (result.get('error') or result.get('errors') or result.get('success') is False):
                raise ZetarisContractError('Zetaris reported a SQL execution error.')
            if isinstance(result, list) and all(isinstance(r, dict) for r in result):
                return [normalize_row(r) for r in result]
            if isinstance(result, dict) and isinstance(result.get('rows'), list):
                rows = result['rows']
                if all(isinstance(r, dict) for r in rows):
                    return [normalize_row(r) for r in rows]
        except ZetarisContractError as exc:
            raise DataError(str(exc)) from None
        raise DataError('Zetaris responded, but its row envelope still needs instance verification.')


class Store:
    def __init__(self, path=None, mode=None):
        self.mode = mode or ('sample' if os.environ.get('SAMPLE_MODE','').lower() in ('1','true') else os.environ.get('INCIDENTCOURT_DATA_MODE', 'local'))
        if self.mode not in ('sample', 'local', 'zetaris'):
            raise DataError('INCIDENTCOURT_DATA_MODE must be sample, local or zetaris.')
        self.path = path or ROOT / ('data/sample.sqlite3' if self.mode=='sample' else 'data/telemetry.sqlite3')
        if self.mode=='sample' and path is None:
            from .sample import ensure_sample_db
            ensure_sample_db(self.path)
        self.queries = []
        self.remote = Zetaris() if self.mode == 'zetaris' else None
        namespace = os.environ.get('ZETARIS_TABLE_PREFIX', '') if self.remote else ''
        if namespace and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\.', namespace):
            raise DataError('Invalid ZETARIS_TABLE_PREFIX; use identifier components and a trailing dot.')
        self.prefix = namespace

    def table(self, name):
        if name not in TABLES:
            raise DataError('Only anonymous telemetry tables are accessible.')
        return self.prefix + name

    def query(self, purpose, sql):
        # Only trusted application templates call this method. SQLite additionally
        # enforces authorizer and read-only file permissions, including subqueries.
        if not sql.lstrip().upper().startswith('SELECT ') or ';' in sql or '--' in sql or '/*' in sql:
            raise DataError('A single read-only telemetry SELECT is required.')
        if self.remote:
            rows = self.remote.query(sql)
        else:
            if not self.path.exists():
                raise DataError('Telemetry is not imported. Run scripts/import_rcaeval.py first.')
            with closing(sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                def authorize(action, table, column, database, trigger):
                    if action == sqlite3.SQLITE_READ and table not in TABLES:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY
                conn.set_authorizer(authorize)
                try:
                    rows = [dict(r) for r in conn.execute(sql).fetchmany(1001)]
                except sqlite3.Error:
                    raise DataError('Telemetry query failed or attempted access outside approved tables.') from None
        if len(rows) > 1000:
            raise DataError('Query exceeded 1000 returned rows; narrow the template.')
        self.queries.append({'id': f'Q{len(self.queries)+1:02}', 'purpose': purpose, 'sql': sql,
                             'row_count': len(rows), 'backend': self.mode})
        return rows, self.queries[-1]['id']

    def incidents(self):
        return self.query('List anonymous incidents', f'SELECT id,title,source,start_ts,end_ts,split FROM {self.table("incidents")} ORDER BY id LIMIT 100')[0]

    def incident(self, incident_id):
        if not isinstance(incident_id,str) or not re.fullmatch(r'IC-\d{3}', incident_id):
            raise DataError('Choose an anonymous incident ID from the list.')
        rows, _ = self.query('Read incident window', f'SELECT id,title,source,start_ts,end_ts,split FROM {self.table("incidents")} WHERE id={literal(incident_id)} LIMIT 1')
        if not rows:
            raise DataError('Incident not found.')
        return rows[0]
