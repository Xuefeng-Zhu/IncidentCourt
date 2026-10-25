"""Prepare anonymous Parquet files locally for an existing Zetaris data source.

This script does not upload, register, provision or modify external services.
"""
import argparse
from pathlib import Path
import sqlite3
from incidentcourt.config import ROOT


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sample',action='store_true')
    args=parser.parse_args()
    import pyarrow as pa
    import pyarrow.parquet as pq
    path=ROOT/('data/sample.sqlite3' if args.sample else 'data/telemetry.sqlite3')
    if args.sample:
        from incidentcourt.sample import ensure_sample_db
        ensure_sample_db(path)
    if not path.exists(): raise SystemExit('Import the bounded telemetry subset first.')
    output=ROOT/'data/staging'
    output.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        for name in ('incidents','metrics','logs','traces'):
            rows=connection.execute(f'SELECT * FROM {name} ORDER BY id').fetchall()
            pq.write_table(pa.Table.from_pylist([dict(row) for row in rows]),output/(name+'.parquet'),compression='zstd')
            print(f'{name}: {len(rows)} anonymous rows exported locally')


if __name__=='__main__': main()
