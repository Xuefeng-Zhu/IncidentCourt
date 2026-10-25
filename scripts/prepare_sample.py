#!/usr/bin/env python3
"""Build a small offline fixture from already imported telemetry; no network."""
from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
TABLES = ('incidents','metrics','logs','traces')
MAX_LOGS = 500
MAX_SPANS = 5000


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def spread(rows, maximum):
    """Select observations across a sorted interval, including its boundaries."""
    if len(rows) <= maximum:
        return rows
    if maximum < 2:
        return rows[:maximum]
    indices = {round(i*(len(rows)-1)/(maximum-1)) for i in range(maximum)}
    return [rows[i] for i in sorted(indices)]


def select_logs(con, iid):
    all_logs = [dict(r) for r in con.execute('SELECT * FROM logs WHERE incident_id=? ORDER BY ts,id',(iid,))]
    matches = [r for r in all_logs if any(t in (r['message'] or '').lower() for t in ('error','timeout','fail','unavailable'))]
    selected = {r['id']:r for r in spread(matches, MAX_LOGS//2)}
    for row in spread(all_logs, MAX_LOGS - len(selected)):
        selected[row['id']] = row
    # Sampling overlap can reduce the count; do not pad with invented observations.
    return sorted(selected.values(), key=lambda r:(r['ts'],r['id'])), len(all_logs), len(matches)


def select_traces(con, iid):
    groups = [dict(r) for r in con.execute('SELECT trace_id,MIN(ts) AS first_ts,MAX(ts) AS last_ts,COUNT(*) AS n FROM traces WHERE incident_id=? GROUP BY trace_id',(iid,))]
    if not groups:
        return [], 0
    start,end = min(r['first_ts'] for r in groups),max(r['last_ts'] for r in groups)
    selected, used = {}, 0
    # Keep the complete recorded trace groups containing earliest and latest spans.
    anchors = [min(groups,key=lambda r:(r['first_ts'],r['trace_id'])),max(groups,key=lambda r:(r['last_ts'],r['trace_id']))]
    for group in anchors:
        if group['trace_id'] not in selected:
            if used+group['n'] > MAX_SPANS:
                raise ValueError('Boundary trace groups exceed the sample span cap')
            selected[group['trace_id']]=group; used+=group['n']
    buckets=[[] for _ in range(12)]
    for group in groups:
        slot=min(11,int(12*(group['first_ts']-start)/max(end-start,.001)))
        buckets[slot].append(group)
    for bucket in buckets:
        bucket.sort(key=lambda r:digest(iid+':'+r['trace_id']))
    # Round-robin deterministic hash order gives every temporal stratum a chance.
    while any(buckets):
        for bucket in buckets:
            if not bucket: continue
            group=bucket.pop()
            if group['trace_id'] in selected or used+group['n']>MAX_SPANS: continue
            selected[group['trace_id']]=group; used+=group['n']
        if used >= MAX_SPANS: break
    ids=sorted(selected)
    spans=[]
    for trace_id in ids:
        spans.extend(dict(r) for r in con.execute('SELECT * FROM traces WHERE incident_id=? AND trace_id=?',(iid,trace_id)))
    return sorted(spans,key=lambda r:(r['ts'],r['id'])), len(groups)


def prepare(root=ROOT):
    con=sqlite3.connect((root/'data/telemetry.sqlite3').as_uri()+'?mode=ro',uri=True)
    con.row_factory=sqlite3.Row
    source=json.loads((root/'data/public/manifest.json').read_text())
    fixture={'format':1,'tables':{name:[] for name in TABLES}}
    manifest={'format':1,'source':'RCAEval RE2-OB','source_revision':source['revision'],
              'source_database_sha256':source['database_sha256'],'synthetic':False,
              'sampling':{'metrics':'Every 10th distinct observed timestamp plus first/last, all source fields at those times',
                          'logs':'Up to250 error/timeout/fail/unavailable text matches spread across time, then up to250-500 overall observations spread across time; include signal boundaries',
                          'traces':'Complete observed trace groups, first/last-span groups plus deterministic hash order round-robin across12 time strata; at most5000 spans per incident'},
              'limitations':['This is a sampled experimental telemetry archive, not a full RCAEval benchmark.',
                             'Sampling changes distributions and may omit rare failures; rankings may differ from the full imported subset.',
                             'Complete observed trace groups retain captured spans only; original capture may lack parents.',
                             'Metric gaps remain NULL and observed signal coverage may end before the incident metric window.'],
              'incidents':[]}
    for incident in con.execute('SELECT * FROM incidents ORDER BY id'):
        incident=dict(incident);iid=incident['id'];fixture['tables']['incidents'].append(incident)
        times=[r[0] for r in con.execute('SELECT DISTINCT ts FROM metrics WHERE incident_id=? ORDER BY ts',(iid,))]
        kept=set(times[::10]+times[-1:])
        metrics=[dict(r) for r in con.execute('SELECT * FROM metrics WHERE incident_id=? ORDER BY ts,id',(iid,)) if r['ts'] in kept]
        logs,logs_total,error_matches=select_logs(con,iid)
        traces,trace_groups_total=select_traces(con,iid)
        entry={'id':iid,'split':incident['split'],'rows':{},'coverage':{},'source_rows':{},
               'source_trace_groups':trace_groups_total,'selected_trace_groups':len({r['trace_id'] for r in traces}),
               'source_log_text_matches':error_matches}
        for name,rows in [('metrics',metrics),('logs',logs),('traces',traces)]:
            fixture['tables'][name].extend(rows)
            entry['rows'][name]=len(rows)
            entry['source_rows'][name]=con.execute(f'SELECT COUNT(*) FROM {name} WHERE incident_id=?',(iid,)).fetchone()[0]
            entry['coverage'][name]={'first_ts':min((r['ts'] for r in rows),default=None),'last_ts':max((r['ts'] for r in rows),default=None)}
        manifest['incidents'].append(entry)
    con.close()
    raw=json.dumps(fixture,separators=(',',':'),allow_nan=False).encode()
    compressed=gzip.compress(raw,compresslevel=9,mtime=0)
    if len(compressed)>1_500_000:
        raise ValueError('Offline fixture exceeded its1.5MB compressed budget')
    manifest.update({'compressed_bytes':len(compressed),'expanded_bytes':len(raw),
                     'sha256':hashlib.sha256(compressed).hexdigest(),
                     'license_notice':'LICENSES/RCAEval-MIT.txt'})
    (root/'data/sample.json.gz').write_bytes(compressed)
    (root/'data/public/sample-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'bytes':len(compressed),'rows':{k:len(v) for k,v in fixture['tables'].items()}},indent=2))


if __name__=='__main__':
    prepare()
