"""Offline application snapshot/restore; never changes runtime routing or sends external writes."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend' if (ROOT/'backend').is_dir() else ROOT))
from app.recovery import backup,restore,ReadOnlySQLiteSource
from app.service import Service
from app.postgres import PostgresStore
from app.packages import PackageError
from app.store import Conflict

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('action',choices=['backup','restore'])
parser.add_argument('snapshot',type=Path)
parser.add_argument('--data-dir',type=Path,required=True)
parser.add_argument('--schemas-dir',type=Path,default=ROOT/'schemas')
parser.add_argument('--actor',required=True)
parser.add_argument('--quiesced',action='store_true',help='Application and all workers are stopped and destination is offline')
parser.add_argument('--expected-sha256',help='Restore requires the independently retained backup manifest digest')
args=parser.parse_args()
if not args.quiesced:parser.error('Stop the application and all workers, then explicitly pass --quiesced')
if args.action=='restore' and not args.expected_sha256:parser.error('Restore requires --expected-sha256 from the retained backup report')
url=os.environ.get('RECOVERY_DATABASE_URL');store=None
try:
    if args.action=='backup' and (not args.data_dir.is_dir() or (not url and not (args.data_dir/'workflow.sqlite').is_file())):
        parser.error('An existing source application data directory/database is required')
    if url:store=PostgresStore(url)
    if args.action=='backup':
        source=store if store is not None else ReadOnlySQLiteSource(args.data_dir/'workflow.sqlite')
        result=backup(Service(args.data_dir,args.schemas_dir,store=source),args.snapshot,actor=args.actor,quiesced=True)
    else:
        result=restore(args.snapshot,args.data_dir,args.schemas_dir,expected_digest=args.expected_sha256,actor=args.actor,quiesced=True,store=store)
    print(json.dumps(result,indent=2))
except (PackageError,Conflict,ValueError) as exc:
    sys.exit(str(exc))
except Exception:
    sys.exit('Recovery did not complete. Keep the snapshot and destination data for controlled inspection; no runtime cutover was performed.')
finally:
    if store is not None:store.close()
