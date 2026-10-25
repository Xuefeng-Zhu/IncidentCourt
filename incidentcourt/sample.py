"""Restore the committed sampled RCAEval fixture offline using the stdlib only."""
from __future__ import annotations
from contextlib import closing
import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = {
    'incidents':('id','title','source','start_ts','end_ts','split'),
    'metrics':('id','incident_id','ts','service','name','value'),
    'logs':('id','incident_id','ts','service','severity','message'),
    'traces':('id','incident_id','ts','service','span_id','parent_id','trace_id','duration_ms','status'),
}
SCHEMA = '''
CREATE TABLE incidents(id TEXT PRIMARY KEY,title TEXT NOT NULL,source TEXT NOT NULL,start_ts REAL NOT NULL,end_ts REAL NOT NULL,split TEXT NOT NULL);
CREATE TABLE metrics(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT NOT NULL,name TEXT NOT NULL,value REAL);
CREATE TABLE logs(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT,severity TEXT,message TEXT);
CREATE TABLE traces(id TEXT PRIMARY KEY,incident_id TEXT NOT NULL REFERENCES incidents(id),ts REAL NOT NULL,service TEXT,span_id TEXT,parent_id TEXT,trace_id TEXT,duration_ms REAL,status INTEGER);
CREATE INDEX metric_window ON metrics(incident_id,service,name,ts);
CREATE INDEX log_window ON logs(incident_id,service,ts);
CREATE INDEX trace_window ON traces(incident_id,service,ts);
CREATE INDEX trace_link ON traces(incident_id,trace_id,span_id);
CREATE INDEX trace_parent ON traces(incident_id,trace_id,parent_id);
CREATE TABLE sample_metadata(fixture_sha256 TEXT NOT NULL);
'''
MAX_COMPRESSED = 1_500_000
MAX_EXPANDED = 20_000_000


class SampleError(ValueError):
    pass


def ensure_sample_db(path: str | Path) -> Path:
    """Return a matching sample DB, atomically creating it on a fresh checkout.

    No provider, HTTP, pyarrow, full archive, labels or runtime installation is used.
    The fixture's SHA-256 is checked against its committed public manifest.
    """
    path=Path(path).resolve()
    try:
        manifest=json.loads((ROOT/'data/public/sample-manifest.json').read_text())
        expected=manifest['sha256']
        if not isinstance(expected,str) or len(expected)!=64:
            raise SampleError('Sample fixture manifest is invalid.')
        if path.exists():
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
                    marker=con.execute('SELECT fixture_sha256 FROM sample_metadata LIMIT 1').fetchone()
                if marker and marker[0]==expected:
                    return path
            except sqlite3.Error:
                raise SampleError('Sample target already contains another database; choose a fresh sample path.') from None
        compressed=(ROOT/'data/sample.json.gz').read_bytes()
        if len(compressed)>MAX_COMPRESSED or len(compressed)!=manifest['compressed_bytes'] or hashlib.sha256(compressed).hexdigest()!=expected:
            raise SampleError('Sample fixture failed integrity verification.')
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as source:
            raw=source.read(MAX_EXPANDED+1)
        if len(raw)>MAX_EXPANDED or len(raw)!=manifest['expanded_bytes']:
            raise SampleError('Sample fixture expanded size is invalid.')
        fixture=json.loads(raw)
        if fixture.get('format')!=1 or set(fixture.get('tables',{}))!=set(COLUMNS):
            raise SampleError('Sample fixture schema is invalid.')
        path.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='incidentcourt-sample-',suffix='.sqlite3',dir=path.parent,delete=False) as temporary:
            temporary_path=Path(temporary.name)
        try:
            with closing(sqlite3.connect(temporary_path)) as con:
                con.execute('PRAGMA foreign_keys=ON')
                con.executescript(SCHEMA)
                for table,columns in COLUMNS.items():
                    rows=fixture['tables'][table]
                    if not isinstance(rows,list) or len(rows)>100000 or any(not isinstance(row,dict) or set(row)!=set(columns) for row in rows):
                        raise SampleError('Sample fixture rows are invalid.')
                    names=','.join(columns);placeholders=','.join('?' for _ in columns)
                    con.executemany(f'INSERT INTO {table} ({names}) VALUES ({placeholders})',
                                    [tuple(row[c] for c in columns) for row in rows])
                con.execute('INSERT INTO sample_metadata VALUES (?)',(expected,))
                con.commit()
                if con.execute('PRAGMA foreign_key_check').fetchall():
                    raise SampleError('Sample fixture references are invalid.')
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return path
    except SampleError:
        raise
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError) as exc:
        raise SampleError('Offline sample unavailable; restore the committed sample fixture and manifest.') from None
