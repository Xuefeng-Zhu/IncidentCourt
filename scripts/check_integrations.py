"""Read-only preflight. Never creates infrastructure, tokens, tables or model calls."""
import json
from incidentcourt.config import ROOT
from incidentcourt.store import Store,DataError
from incidentcourt.integrations import HMemStore,NightshiftClient


def main():
    result={'zetaris':{'state':'unverified'},'nightshift':NightshiftClient.from_env().status(probe=True),
            'hmem':HMemStore(ROOT/'data/memory').status()}
    try:
        store=Store(mode='zetaris')
        rows,_=store.query('Read-only connectivity check','SELECT 1 AS connected')
        result['zetaris']={'state':'sql_verified','select_one':rows}
        # Separate connectivity from actual application table readiness.
        result['zetaris']['incidents']=len(store.incidents())
        for table in ('metrics','logs','traces'):
            rows,_=store.query(f'Check application {table} table',f'SELECT COUNT(*) AS observations FROM {store.table(table)}')
            result['zetaris'][table]=rows
        result['zetaris']['application_tables_verified']=True
    except DataError as exc:
        result['zetaris']['detail']=str(exc)
        result['zetaris']['application_tables_verified']=False
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
