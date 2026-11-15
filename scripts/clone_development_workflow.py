"""Read-only archive-demo SQLite snapshot and fixity-verified PostgreSQL staging clone.

This is a local qualification copy, not production cutover or archive acceptance.
"""
import argparse,json,os,sqlite3,sys,uuid
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from sqlalchemy.engine import make_url
from app.postgres import PostgresStore
from app.persistence_transfer import import_sqlite,blob_bindings
from app.service import put
from app.packages import sha,PackageError

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--target-data',type=Path,required=True)
parser.add_argument('--actor',required=True)
args=parser.parse_args()
url=os.environ.get('MIGRATION_DATABASE_URL')
if not url or make_url(url).host not in ('localhost','127.0.0.1','postgres'):
    parser.error('A local development MIGRATION_DATABASE_URL is required')
if not args.actor.strip():parser.error('An administrative actor is required')
target=args.target_data.resolve();target.mkdir(parents=True,exist_ok=True)
snapshot=target/('migration-snapshot-'+str(uuid.uuid4())+'.sqlite')
source=args.source.resolve()
with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(snapshot)) as copied:
    original.backup(copied)
with closing(sqlite3.connect(snapshot)) as db:
    bodies=[json.loads(r[0]) for r in db.execute('SELECT body FROM aggregates')]
    if any(b['tenant']!='archive-demo' for b in bodies):raise SystemExit('This qualification clone accepts only the archive-demo dataset')
digests={d for b in bodies for d in blob_bindings(b)}
for digest in digests:
    if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise PackageError('Invalid development blob binding')
    data=(source.parent/'blobs'/digest).read_bytes()
    if sha(data)!=digest:raise PackageError('Source development blob fixity failed')
    put(target,data)
store=PostgresStore(url)
try:report=import_sqlite(store,snapshot,quiesced=True)
finally:store.close()
report.update(mode='LOCAL_DEVELOPMENT_QUALIFICATION_CLONE',administrative_actor=args.actor,
    checked_at=datetime.now(timezone.utc).isoformat(),source_opened_read_only=True,blobs_copied=len(digests),runtime_cutover=False)
evidence=snapshot.with_suffix('.json');evidence.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
