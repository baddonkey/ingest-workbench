"""Explicit workflow migration tooling. Never changes API configuration."""
import argparse,json,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.postgres import PostgresStore
from app.persistence_transfer import import_sqlite,export_sqlite

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['import','export'])
parser.add_argument('snapshot',type=Path)
parser.add_argument('--source-quiesced',action='store_true')
args=parser.parse_args()
url=os.environ.get('MIGRATION_DATABASE_URL')
if not url:parser.error('MIGRATION_DATABASE_URL is required; do not pass credentials on the command line')
store=None
try:
    store=PostgresStore(url)
    report=import_sqlite(store,args.snapshot,quiesced=args.source_quiesced) if args.action=='import' else export_sqlite(store,args.snapshot)
    print(json.dumps(report,indent=2))
except Exception:
    # Retain details in controlled diagnostics during qualification, not URLs/secrets in CLI logs.
    sys.exit('Migration did not complete. No runtime cutover occurred; inspect the source/destination under administrator control.')
finally:
    if store is not None:store.close()
