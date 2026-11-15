"""Offline application recovery snapshots. No runtime cutover or external effects.

Quiescence is an administrator prerequisite, not something a database read can
prove. A snapshot includes application tables and the content vault;
infrastructure, identity, secrets and external repositories are separate.
"""
import copy
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, insert, func, text, inspect as sql_inspect
from .service import canonical, event, now, put
from .packages import sha, PackageError
from .store import Conflict
from .postgres import aggregates, outbox, validate_body
from .kost_validation import runs as kost_runs, events as kost_events, decode as decode_kost, target as kost_target
from .persistence_transfer import blob_bindings, require_supported

FORMAT='urn:ch-archive:application-recovery:2'
HEAD='0005_kost_service'
TABLES=[aggregates,outbox,kost_runs,kost_events]
IDENTITIES=[(aggregates,'sequence')]
DIGEST=re.compile('[0-9a-f]{64}')
SQLITE_COLUMNS={
    'aggregates':{'id','tenant','revision','body'}}
SQLITE_COLUMNS.update({table.name:set(table.c.keys()) for table in (kost_runs,kost_events)})


class ReadOnlySQLiteSource:
    """Snapshot port; avoids Store startup DDL against the source volume."""
    backend='sqlite'
    def __init__(self,path):
        self.path=Path(path)
        if not self.path.is_file():raise ValueError('An existing workflow database is required')
    def ready(self):
        from .store import Store
        return Store.ready(self)


def encoded(value):
    if isinstance(value,datetime):return value.isoformat()
    raise TypeError('Unsupported snapshot value')


def strict_json(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise PackageError('Duplicate snapshot JSON key')
            result[key]=value
        return result
    def constant(value):raise PackageError('Nonfinite snapshot number')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def write_new(path,data):
    with os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def rows_sqlite(path,names,*,optional=()):
    if path.is_symlink():raise PackageError('Symlinked application database refused')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row;db.execute('BEGIN')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise PackageError('SQLite integrity check failed')
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={}
        for name in names:
            if name not in tables and name in optional:result[name]=[];continue
            if {r[1] for r in db.execute('PRAGMA table_info('+name+')')}!=SQLITE_COLUMNS[name]:
                raise Conflict('Unsupported SQLite recovery schema')
            result[name]=[dict(r) for r in db.execute('SELECT * FROM '+name+' ORDER BY rowid')]
        return result


def reject_retired_tables(db):
    existing=set(sql_inspect(db).get_table_names())
    for name in ('workflow_ais_jobs','workflow_ais_records','workflow_ais_events'):
        if name in existing and db.execute(text('SELECT 1 FROM '+name+' LIMIT 1')).first():
            raise Conflict('Retired integration records are not supported by recovery; retain the source unchanged')


def capture(service):
    if service.store.backend=='postgresql':
        with service.store.engine.connect().execution_options(isolation_level='REPEATABLE READ') as db:
            db.exec_driver_sql('SET TRANSACTION READ ONLY')
            revision=db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one()
            if revision!=HEAD:raise Conflict('Recovery requires the supported migrated schema')
            snapshots={}
            inspector=sql_inspect(db)
            reject_retired_tables(db)
            for table in TABLES:
                if {c['name'] for c in inspector.get_columns(table.name)}!=set(table.c.keys()):raise Conflict('Recovery table schema differs')
                order=table.c.sequence if 'sequence' in table.c else list(table.primary_key.columns)
                if not isinstance(order,list):order=[order]
                snapshots[table.name]=[dict(row) for row in db.execute(select(table).order_by(*order)).mappings()]
            highwaters={}
            for table,column in IDENTITIES:
                sequence=db.scalar(select(func.pg_get_serial_sequence(table.name,column)))
                value=db.exec_driver_sql('SELECT last_value FROM pg_sequences WHERE schemaname=current_schema() AND sequencename=%s',
                    (sequence.rsplit('.',1)[-1].strip('"'),)).scalar_one_or_none()
                highwaters[table.name+'.'+column]=value or 0
        # JSON preserves original stored JSON text; datetime columns use ISO values.
        return strict_json(json.dumps({'backend':'postgresql','schema':revision,'tables':snapshots,'highwaters':highwaters},default=encoded))
    tables=rows_sqlite(service.store.path,['aggregates',kost_runs.name,kost_events.name],optional=(kost_runs.name,kost_events.name))
    folder=service.data/'delivery-journals'
    if folder.is_symlink():raise PackageError('Symlinked journal vault refused')
    if folder.exists():
        if any(folder.iterdir()):raise Conflict('Retired integration journals are not supported by recovery; retain the source unchanged')
    return {'backend':'sqlite','schema':'workflow:2','tables':tables}


def normalized(state):
    if state['backend']=='postgresql':
        result=copy.deepcopy(state['tables'])
        for table in (kost_runs,kost_events):result.setdefault(table.name,[])
        return result
    tables=state['tables']
    result={table.name:[] for table in TABLES}
    for table in (kost_runs,kost_events):result[table.name]=tables.get(table.name,[])
    for i,row in enumerate(tables['aggregates']):result[aggregates.name].append({**row,'body_sha256':sha(row['body'].encode()),'sequence':i+1})
    return result


def validate(state,blob,schemas):
    backend=state.get('backend')
    if backend not in ('sqlite','postgresql') or state.get('schema') not in ({'sqlite':('workflow:2',),'postgresql':(HEAD,)}[backend]):
        raise Conflict('Unsupported application recovery format/schema')
    expected_fields={'backend','schema','tables'}|({'highwaters'} if backend=='postgresql' else set())
    if set(state)!=expected_fields:raise PackageError('Unsupported application recovery state fields')
    if backend=='postgresql':
        expected=TABLES
        if set(state['tables'])!={t.name for t in expected}:raise PackageError('Incomplete application table inventory')
        for table in expected:
            if any(set(r)!=set(table.c.keys()) for r in state['tables'][table.name]):raise PackageError('Snapshot row columns differ')
    else:
        if set(state['tables']) not in ({'aggregates'},{'aggregates',kost_runs.name,kost_events.name}):raise PackageError('Unsupported workflow table inventory')
        for name,rows in state['tables'].items():
            if any(set(r)!=SQLITE_COLUMNS[name] for r in rows):raise PackageError('SQLite row columns differ')
    tables=normalized(state)
    for table in TABLES:
        keys=[tuple(r[c.name] for c in table.primary_key.columns) for r in tables[table.name]]
        if len(keys)!=len(set(keys)):raise PackageError('Duplicate recovery row identity')
    bodies={}
    for row in tables[aggregates.name]:
        body=strict_json(row['body']);validate_body(body)
        require_supported(body)
        if (body['id'],body['tenant'],body['revision'])!=(row['id'],row['tenant'],row['revision']) or sha(row['body'].encode())!=row['body_sha256']:
            raise PackageError('Workflow snapshot binding mismatch')
        bodies[body['id']]=body
        for digest in blob_bindings(body):blob(digest)
        from .metadata_drafts import validate_retained
        validate_retained(body,blob)
        from .sip_exports import validate_retained as validate_exports
        validate_exports(body,blob)
        from .working_metadata import validate_retained as validate_working
        validate_working(body,blob,schemas)
        from .prepared_inputs import validate_retained as validate_prepared
        validate_prepared(body,blob,schemas)
    for body in bodies.values():
        from .prepared_review import validate_retained as validate_reviews
        validate_reviews(body,blob,schemas)
        from .prepared_aip import validate_retained as validate_candidates
        validate_candidates(body,blob,schemas)
    retained_kost={}
    for record in tables[kost_runs.name]:
        run=decode_kost(record)
        parent=bodies.get(run['transfer_id'])
        if parent is None or parent['tenant']!=run['tenant']:raise PackageError('Orphan KOST validation')
        selected=kost_target(parent,run['sip_id'],run['prepared_id'])
        if any(run[key]!=selected[key] for key in ('target_id','input_sha256','profile')):
            raise PackageError('KOST validated input binding differs')
        if run['state'] not in ('QUEUED','SUBMITTING','TRACKING','REPORT','COMPLETE','SUBMISSION_UNKNOWN') or record['active']!=int(run['state']!='COMPLETE'):
            raise PackageError('Invalid KOST recovery state')
        if run['result'] not in (None,'valid','invalid','error'):raise PackageError('Invalid KOST outcome')
        blob(run['input_sha256'])
        for key in ('response_sha256','report_sha256'):
            if run.get(key):blob(run[key])
        retained_kost[run['id']]=run
    active_targets=[(r['tenant'],r['transfer_id'],r['sip_id'],r['target_id']) for r in retained_kost.values() if r['state']!='COMPLETE']
    if len(active_targets)!=len(set(active_targets)):raise PackageError('Duplicate active KOST validations')
    for entry in tables[kost_events.name]:
        detail=strict_json(entry['body'])
        if entry['run_id'] not in retained_kost or detail['run_id']!=entry['run_id'] or sha(entry['body'].encode())!=entry['body_sha256']:
            raise PackageError('KOST event identity or fixity differs')
    positions=set()
    for row in tables[outbox.name]:
        parent=bodies.get(row['aggregate_id']);key=(row['aggregate_id'],row['position'])
        if (parent is None or row['tenant']!=parent['tenant'] or row['state'] not in ('READY','LEASED','DELIVERED')
                or row['position']<0 or row['position']>=len(parent['events']) or key in positions
                or row['revision']>parent['revision'] or sha(row['payload'].encode())!=row['payload_sha256']
                or strict_json(row['payload'])!=parent['events'][row['position']] or row['id']!=parent['events'][row['position']]['id']):
            raise PackageError('Outbox snapshot event/order binding mismatch')
        positions.add(key)
    return tables


def backup(service,destination,*,actor,quiesced=False):
    if not quiesced or not actor.strip():raise ValueError('Administrator actor and stopped application/workers required')
    destination=Path(destination).resolve()
    if destination.is_relative_to(service.data.resolve()):raise ValueError('Snapshot must be outside the source application data directory')
    from .readiness import check
    if check(service)['status']!='ready':raise Conflict('Source trusted-schema/database readiness failed')
    state=capture(service)
    vault=service.data/'blobs';inventory={}
    if vault.is_symlink():raise PackageError('Symlinked content vault refused')
    if vault.exists():
        for path in sorted(vault.iterdir()):
            if path.is_symlink() or not path.is_file() or DIGEST.fullmatch(path.name) is None:raise PackageError('Unsupported content vault entry')
            inventory[path.name]={'sha256':path.name,'size':path.stat().st_size}
    def source_blob(digest):
        if digest not in inventory:raise PackageError('Required recovery blob is absent')
        data=(vault/digest).read_bytes()
        if sha(data)!=digest or len(data)!=inventory[digest]['size']:raise PackageError('Source vault fixity mismatch')
        return data
    validate(state,source_blob,service.schemas)
    destination.mkdir(mode=0o700,parents=True,exist_ok=False);(destination/'blobs').mkdir(mode=0o700)
    for digest in inventory:write_new(destination/'blobs'/digest,source_blob(digest))
    raw=canonical(state);write_new(destination/'state.json',raw)
    schemas={p.relative_to(service.schemas).as_posix():sha(p.read_bytes()) for p in sorted(service.schemas.rglob('*.xsd'))}
    profiles=service.schemas.parent/'profiles'
    profile_files={p.relative_to(profiles).as_posix():sha(p.read_bytes()) for p in sorted(profiles.rglob('*')) if p.is_file()}
    manifest={'format':FORMAT,'backend':state['backend'],'schema':state['schema'],'actor':actor,'created':now(),
              'source_quiesced':True,'state_sha256':sha(raw),'state_size':len(raw),'blobs':inventory,'schemas':schemas,'application_profiles':profile_files,
              'production_acceptance':False,'external_repositories_included':False,'identity_and_secrets_included':False}
    manifest_raw=canonical(manifest);write_new(destination/'manifest.json',manifest_raw)
    return {'snapshot_sha256':sha(manifest_raw),'backend':state['backend'],'blobs':len(inventory),
            'aggregates':len(normalized(state)[aggregates.name]),'production_acceptance':False,'runtime_cutover':False}


def load(snapshot,expected_digest,schemas):
    root=Path(snapshot).resolve(strict=True)
    if DIGEST.fullmatch(expected_digest) is None:raise ValueError('Externally retained snapshot manifest digest required')
    if any(p.is_symlink() for p in root.iterdir()) or {p.name for p in root.iterdir()}!={'state.json','manifest.json','blobs'}:
        raise PackageError('Unsupported or incomplete recovery capsule')
    raw=(root/'manifest.json').read_bytes()
    if sha(raw)!=expected_digest:raise PackageError('Recovery manifest does not match its retained digest')
    manifest=strict_json(raw)
    if manifest.get('format')!=FORMAT or manifest.get('source_quiesced') is not True:raise Conflict('Unsupported or nonquiesced recovery capsule')
    state_raw=(root/'state.json').read_bytes()
    if sha(state_raw)!=manifest['state_sha256'] or len(state_raw)!=manifest['state_size']:raise PackageError('Recovery state fixity mismatch')
    state=strict_json(state_raw)
    if (state['backend'],state['schema'])!=(manifest['backend'],manifest['schema']):raise PackageError('Recovery manifest/state binding mismatch')
    actual={p.relative_to(schemas).as_posix():sha(p.read_bytes()) for p in sorted(schemas.rglob('*.xsd'))}
    if manifest['schemas']!=actual:raise Conflict('Restore trusted schemas differ from the snapshot')
    profiles=schemas.parent/'profiles'
    actual_profiles={p.relative_to(profiles).as_posix():sha(p.read_bytes()) for p in sorted(profiles.rglob('*')) if p.is_file()}
    if manifest['application_profiles']!=actual_profiles:raise Conflict('Restore application profiles differ from the snapshot')
    inventory=manifest['blobs'];vault=root/'blobs'
    if not vault.is_dir() or {p.name for p in vault.iterdir()}!=set(inventory):raise PackageError('Recovery blob inventory differs')
    def blob(digest):
        if not isinstance(digest,str) or DIGEST.fullmatch(digest) is None or digest not in inventory:raise PackageError('Required recovery blob is absent or unsafe')
        path=vault/digest
        if path.is_symlink() or not path.is_file():raise PackageError('Unsafe recovery blob')
        data=path.read_bytes()
        if sha(data)!=digest or inventory[digest]!=dict(sha256=digest,size=len(data)):raise PackageError('Recovery blob fixity mismatch')
        return data
    for digest in inventory:blob(digest)
    validate(state,blob,schemas)
    return state,manifest,blob


def fence(tables,*,actor,digest):
    tables=copy.deepcopy(tables)
    for record in tables[kost_runs.name]:
        run=decode_kost(record)
        if run['state']=='SUBMITTING':
            run.update(state='SUBMISSION_UNKNOWN',message='Restored interrupted submission; a remote job may exist.')
        record.update(token=None,lease_until=0,state=run['state'],body=canonical(run).decode())
        record['body_sha256']=sha(record['body'].encode())
    for row in tables[outbox.name]:
        if row['state']=='LEASED':row['state']='READY'
        row.update(owner=None,token_sha256=None,lease_until=None)
    recovery_events=[]
    for row in tables[aggregates.name]:
        body=strict_json(row['body'])
        event(body,'application-recovery',actor,{'snapshot_sha256':digest,'restored_capabilities_expired':True,'external_effects_performed':False})
        body['revision']+=1
        row.update(revision=body['revision'],body=canonical(body).decode());row['body_sha256']=sha(row['body'].encode())
        recovery_events.append(body)
    return tables,recovery_events


def restore(snapshot,data,schemas,*,expected_digest,actor,quiesced=False,store=None):
    if not quiesced or not actor.strip():raise ValueError('Administrator actor and stopped destination application/workers required')
    if Path(data).is_symlink():raise Conflict('A new destination data directory is required, not a symlink')
    data=Path(data).resolve();schemas=Path(schemas).resolve()
    if data.exists() or data.is_relative_to(Path(snapshot).resolve()):raise Conflict('A new destination data directory is required')
    state,manifest,blob=load(snapshot,expected_digest,schemas)
    backend=store.backend if store is not None else 'sqlite'
    if state['backend']!=backend:raise Conflict('Recovery requires the same backend type; cross-store cutover is separate')
    tables,bodies=fence(normalized(state),actor=actor,digest=expected_digest)
    if store is not None and not store.ready():raise Conflict('Destination schema/readiness must match before restore')
    if store is not None:
        with store.engine.connect() as db:
            reject_retired_tables(db)
            if any(db.scalar(select(func.count()).select_from(t)) for t in TABLES):raise Conflict('Destination application tables must be empty')
    # Reserve an entirely new vault. Published blobs remain available even if
    # a PostgreSQL commit acknowledgement is lost; never delete possible references.
    data.mkdir(mode=0o700,parents=True,exist_ok=False)
    for digest in manifest['blobs']:put(data,blob(digest))
    write_new(data/'recovery-source-state.json',canonical(state))
    if backend=='postgresql':
        with store.engine.begin() as db:
            db.exec_driver_sql('LOCK TABLE '+', '.join(t.name for t in TABLES)+' IN SHARE ROW EXCLUSIVE MODE')
            if any(db.scalar(select(func.count()).select_from(t)) for t in TABLES):raise Conflict('Destination application tables must be empty')
            for table in TABLES:
                for row in tables[table.name]:
                    values=dict(row)
                    for column in table.c:
                        if column.name in ('available_at','lease_until') and isinstance(values[column.name],str):values[column.name]=datetime.fromisoformat(values[column.name])
                    db.execute(insert(table).values(**values))
            for body in bodies:store.notify(db,body,[body['events'][-1]])
            for table,column in IDENTITIES:
                high=max([state['highwaters'][table.name+'.'+column],*(r[column] for r in tables[table.name])])+1
                db.execute(select(func.setval(func.pg_get_serial_sequence(table.name,column),high,False)))
    else:
        from .store import Store
        target=Store(data/'workflow.sqlite')
        with target.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for name,table in [('aggregates',aggregates),(kost_runs.name,kost_runs),(kost_events.name,kost_events)]:
                columns=sorted(SQLITE_COLUMNS[name])
                for row in tables[table.name]:db.execute('INSERT INTO '+name+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[row[c] for c in columns])
    report={'format':FORMAT,'snapshot_sha256':expected_digest,'actor':actor,'time':now(),'backend':backend,
            'aggregates':len(bodies),'blobs':len(manifest['blobs']),'restored_capabilities_expired':True,
            'external_effects_performed':False,'production_acceptance':False,'runtime_cutover':False}
    write_new(data/'recovery-receipt.json',canonical(report))
    return report
