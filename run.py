"""Start UI/API, or run one JSON input. No installs or downloads in request paths."""
import argparse
import json
import os
import sys


def main():
    parser=argparse.ArgumentParser(description='IncidentCourt investigation workbench')
    parser.add_argument('--sample',action='store_true',help='Use bundled real telemetry subset; no network calls')
    parser.add_argument('--input',help='JSON file containing incident_id and optional model')
    parser.add_argument('--port',type=int,default=8317)
    parser.add_argument('--host',default='127.0.0.1',choices=('127.0.0.1','0.0.0.0'))
    args=parser.parse_args()
    if args.sample: os.environ['SAMPLE_MODE']='true'
    from incidentcourt.config import ROOT
    from incidentcourt.store import Store,DataError
    from incidentcourt.integrations import IntegrationError
    from incidentcourt.investigate import investigate
    try:
        if os.environ.get('SAMPLE_MODE','').lower() in ('true','1'):
            from incidentcourt.sample import ensure_sample_db
            ensure_sample_db(ROOT/'data/sample.sqlite3')
        if args.input:
            from contextlib import redirect_stdout
            from pathlib import Path
            with Path(args.input).open() as source: payload=json.load(source)
            with redirect_stdout(sys.stderr):
                report=investigate(payload['incident_id'],payload.get('model','local'))
            print(json.dumps(report,indent=2,allow_nan=False))
        else:
            from incidentcourt.server import main as serve
            sys.argv=[sys.argv[0],'--port',str(args.port),'--host',args.host]
            serve()
    except (DataError,IntegrationError,ValueError,KeyError,OSError) as exc:
        # Integration exceptions are sanitized; do not print raw transport errors.
        message=str(exc) if isinstance(exc,(DataError,IntegrationError)) else 'Invalid input or local file configuration.'
        print(json.dumps({'status':'error','error':message}),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
