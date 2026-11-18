"""Explicit, read-only SQLite snapshot import; originals/blobs remain at source."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from sqlalchemy import select,insert
from .packages import sha,PackageError
from .postgres import aggregates,validate_body,PostgresStore
from .store import Conflict


def require_supported(body):
    if body.get('delivery_operations') or body.get('crosswalk_exports'):
        raise Conflict('Retired integration records are not supported; retain the source unchanged')
    if any(output.get('profile') not in ('urn:ch-archive:aip-profile:3.0.0','urn:ch-archive:aip-profile:4.0.0')
           for output in body.get('outputs',[])):
        raise Conflict('Retired candidate profile is not supported; retain the source unchanged')


def blob_bindings(body):
    for plan in body.get('prepared_plans', []):yield plan['descriptor_sha256']
    for review in body.get('prepared_reviews', []):
        yield review['evidence_sha256']
        for resource in review['resource_snapshots']:yield resource['sha256']
        for resources in review['target_resources'].values():
            for digest in resources.values():yield digest
    for prepared in body.get('prepared_inputs', []):
        for field in ('sha256', 'metadata_sha256', 'preparation_sha256', 'evidence_sha256'):
            yield prepared[field]
    for operation in body.get('metadata_operations',[]):
        for field in ('before_metadata_sha256','after_metadata_sha256','changes_sha256','evidence_sha256'):
            yield operation[field]
        if operation['kind']=='content-edit':
            for key in ('before_archive_sha256','after_archive_sha256'):
                yield operation['changes']['content'][key]
    for export in body.get('sip_exports', []):
        yield export['sha256']
        yield export['evidence_sha256']
    for draft in body.get('metadata_drafts', []):
        yield draft['metadata_sha256']
        yield draft['changes_sha256']
    for sip in body.get('sips',[]):
        yield sip['sha256']
    for output in body.get('outputs',[]):
        yield output['sha256']
        if output.get('validation_report_sha256'):yield output['validation_report_sha256']
    for resource in body.get('representation_resources',[]):yield resource['sha256']


def import_sqlite(store,source,*,quiesced=False,schemas=None):
    if not quiesced:raise ValueError('A quiesced, backed-up source snapshot must be explicitly confirmed')
    source=Path(source).resolve()
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='workflow_kost_runs'").fetchone() and db.execute('SELECT 1 FROM workflow_kost_runs LIMIT 1').fetchone():
            raise Conflict('KOST history requires a complete application recovery snapshot; aggregate-only import is not supported')
        rows=[dict(r) for r in db.execute('SELECT id,tenant,revision,body FROM aggregates ORDER BY rowid')]
    checked_blobs=set()
    bodies={}
    def blob(digest):
        data=(source.parent/'blobs'/digest).read_bytes()
        if sha(data)!=digest:raise PackageError('Source artifact digest mismatch')
        return data
    for row in rows:
        body=json.loads(row['body']);validate_body(body)
        require_supported(body)
        bodies[body['id']]=body
        if (body['id'],body['tenant'],body['revision'])!=(row['id'],row['tenant'],row['revision']):
            raise PackageError('Source snapshot binding mismatch')
        for digest in blob_bindings(body):
            if digest in checked_blobs:continue
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise PackageError('Invalid blob identity')
            try:data=(source.parent/'blobs'/digest).read_bytes()
            except OSError as exc:raise PackageError('Referenced source blob is unavailable') from exc
            if sha(data)!=digest:raise PackageError('Referenced source blob digest mismatch')
            checked_blobs.add(digest)
    from .metadata_drafts import validate_retained as validate_drafts
    from .sip_exports import validate_retained as validate_exports
    for body in bodies.values():
        validate_drafts(body,blob)
        validate_exports(body,blob)
        from .working_metadata import validate_retained as validate_working
        validate_working(body,blob)
        from .prepared_inputs import validate_retained as validate_prepared
        validate_prepared(body,blob)
    for body in bodies.values():
        from .prepared_review import validate_retained as validate_reviews
        validate_reviews(body,blob)
        from .prepared_aip import validate_retained as validate_candidates
        validate_candidates(body,blob,Path(schemas or os.getenv('SCHEMA_DIR',str(Path(__file__).resolve().parents[2]/'schemas'))))
    imported=reused=0
    with store.engine.begin() as db:
        # Serialize imports with ordinary writers. Imports are administrative,
        # deliberately offline operations, not a concurrently running replicator.
        db.exec_driver_sql('LOCK TABLE workflow_aggregates IN SHARE ROW EXCLUSIVE MODE')
        for row in rows:
            existing=db.execute(select(aggregates).where(aggregates.c.id==row['id'])).mappings().first()
            if existing is not None:
                if any(existing[k]!=row[k] for k in ('id','tenant','revision','body')):
                    raise Conflict('Destination contains a different aggregate; no overwrite is allowed')
                PostgresStore.decode(existing);reused+=1
            else:
                db.execute(insert(aggregates).values(**row,body_sha256=sha(row['body'].encode())))
                imported+=1
    return {'mode':'PERSISTENCE_FOUNDATION_ONLY','imported':imported,'reused':reused,
            'source_aggregates':len(rows),'verified_source_blobs':len(checked_blobs),
            'source_snapshot_sha256':sha(json.dumps({'aggregates':rows},sort_keys=True,ensure_ascii=False).encode()),
            'blobs_copied':False,'historical_events_dispatched':False,'runtime_cutover':False,'production_acceptance':False}


def export_sqlite(store,destination):
    """Export aggregates to a new file; blobs and outbox are separate."""
    destination=Path(destination)
    descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:
        with store.engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
            from .kost_validation import runs
            if db.execute(select(runs.c.id).limit(1)).first():
                raise Conflict('KOST history requires a complete application recovery snapshot; aggregate-only export is not supported')
            rows=list(db.execute(select(aggregates).order_by(aggregates.c.sequence)).mappings())
            for row in rows:require_supported(store.decode(row))
        with closing(sqlite3.connect(destination)) as db,db:
            db.execute('CREATE TABLE aggregates (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL)')
            db.executemany('INSERT INTO aggregates VALUES (?,?,?,?)',[(r['id'],r['tenant'],r['revision'],r['body']) for r in rows])
        return {'aggregates':len(rows),'blobs_included':False,'outbox_included':False}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
