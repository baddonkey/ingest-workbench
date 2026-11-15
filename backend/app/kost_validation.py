"""Durable, advisory validation of immutable SIP artifacts."""
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit
from sqlalchemy import Table, Column, Text, Integer, Float, Index, text
from .postgres import metadata
from .json_codec import canonical
from .packages import PackageError, sha
from .store import Conflict

HEAD = '0005_kost_service'
runs = Table('workflow_kost_runs', metadata,
    Column('id', Text, primary_key=True), Column('tenant', Text, nullable=False),
    Column('transfer_id', Text, nullable=False), Column('sip_id', Text, nullable=False),
    Column('target_id', Text, nullable=False), Column('active', Integer, nullable=False),
    Column('state', Text, nullable=False), Column('lease_until', Float, nullable=False),
    Column('token', Text), Column('next_at', Float, nullable=False),
    Column('created', Float, nullable=False), Column('body', Text, nullable=False),
    Column('body_sha256', Text, nullable=False))
Index('kost_one_active_target', runs.c.tenant, runs.c.transfer_id, runs.c.sip_id, runs.c.target_id,
    unique=True, sqlite_where=runs.c.active == 1, postgresql_where=runs.c.active == 1)
events = Table('workflow_kost_events', metadata,
    Column('id', Text, primary_key=True), Column('run_id', Text, nullable=False),
    Column('body', Text, nullable=False), Column('body_sha256', Text, nullable=False))
Index('kost_due', runs.c.active, runs.c.next_at, runs.c.lease_until)


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    url: str = 'http://localhost:8001'
    api_key: str = ''
    language: str = 'de'
    poll: float = 5
    deadline: float = 3600

    def __post_init__(self):
        parsed = urlsplit(self.url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('KOSTVAL_URL must be an HTTP service URL without credentials or query')
        if self.language not in ('de', 'fr', 'it', 'en') or self.poll < 1 or self.deadline < 10:
            raise ValueError('Invalid KOST language, polling interval or deadline')

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('KOSTVAL_ENABLED', 'false').lower() == 'true',
            url=os.getenv('KOSTVAL_URL', 'http://localhost:8001'),
            api_key=os.getenv('KOSTVAL_API_KEY', ''), language=os.getenv('KOSTVAL_LANGUAGE', 'de'),
            poll=float(os.getenv('KOSTVAL_POLL_SECONDS', '5')),
            deadline=float(os.getenv('KOSTVAL_DEADLINE_SECONDS', '3600')))

    def fingerprint(self):
        return sha(canonical({'url': self.url.rstrip('/'), 'language': self.language, 'log_type': 'xml'}))


def init_sqlite(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS workflow_kost_runs (
      id TEXT PRIMARY KEY, tenant TEXT NOT NULL, transfer_id TEXT NOT NULL, sip_id TEXT NOT NULL,
      target_id TEXT NOT NULL, active INTEGER NOT NULL, state TEXT NOT NULL, lease_until REAL NOT NULL,
      token TEXT, next_at REAL NOT NULL, created REAL NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL);
    CREATE UNIQUE INDEX IF NOT EXISTS kost_one_active_target ON workflow_kost_runs(tenant,transfer_id,sip_id,target_id) WHERE active=1;
    CREATE INDEX IF NOT EXISTS kost_due ON workflow_kost_runs(active,next_at,lease_until);
    CREATE TABLE IF NOT EXISTS workflow_kost_events (id TEXT PRIMARY KEY,run_id TEXT NOT NULL,body TEXT NOT NULL,body_sha256 TEXT NOT NULL);
    ''')


def execute(tx, sql, values=None):
    return tx.db.execute(sql if isinstance(tx.db, sqlite3.Connection) else text(sql), values or {})


def row(cursor):
    value = cursor.fetchone()
    return dict(value if isinstance(value, sqlite3.Row) else value._mapping) if value is not None else None


def clock(tx):
    query = "SELECT (julianday('now')-2440587.5)*86400.0 AS timestamp" if isinstance(tx.db, sqlite3.Connection) else 'SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS timestamp'
    return float(row(execute(tx, query))['timestamp'])


def decode(record):
    if sha(record['body'].encode()) != record['body_sha256']:
        raise PackageError('KOST journal checksum mismatch')
    body = json.loads(record['body'])
    for field in ('id', 'tenant', 'transfer_id', 'sip_id', 'target_id', 'state'):
        if body[field] != record[field]:
            raise PackageError('KOST journal binding mismatch')
    return body


def target(body, sip_id, prepared_id=None):
    sip = next((item for item in body['sips'] if item['id'] == sip_id), None)
    if sip is None:
        raise KeyError(sip_id)
    artifact = sip
    if prepared_id:
        from .prepared_inputs import find
        artifact = find(body, sip_id, prepared_id)
    return {'target_id': prepared_id or 'received', 'prepared_id': prepared_id,
        'input_sha256': artifact['sha256'], 'profile': sip['profile'],
        'filename': sip.get('original_filename'),
        'label': 'Frozen '+prepared_id[:8] if prepared_id else 'Received SIP'}


def retain(tx, body, kind, timestamp):
    raw = canonical(body).decode()
    execute(tx, '''UPDATE workflow_kost_runs SET body=:body,body_sha256=:digest,state=:state,
        active=:active WHERE id=:id''', dict(body=raw, digest=sha(raw.encode()), state=body['state'],
            active=int(body['state'] != 'COMPLETE'), id=body['id']))
    detail = canonical({'run_id': body['id'], 'time': timestamp, 'kind': kind,
        'state': body['state'], 'result': body.get('result'), 'actor': body['actor']}).decode()
    execute(tx, 'INSERT INTO workflow_kost_events(id,run_id,body,body_sha256) VALUES (:id,:run,:body,:digest)',
        dict(id=str(uuid.uuid4()), run=body['id'], body=detail, digest=sha(detail.encode())))


def enqueue(tx, body, sip_id, actor, request_id, config, *, prepared_id=None,
            expected_sha256=None, acknowledge_uncertain=False, trigger='manual'):
    selected = target(body, sip_id, prepared_id)
    if expected_sha256 is not None and selected['input_sha256'] != expected_sha256:
        raise Conflict('Validation input changed; select the retained artifact again')
    request_id = str(uuid.UUID(request_id))
    binding = dict(tenant=body['tenant'], transfer_id=body['id'], sip_id=sip_id,
        target_id=selected['target_id'])
    digest = sha(canonical({**binding, 'actor': actor, 'input_sha256': selected['input_sha256'],
        'configuration': config.fingerprint(), 'acknowledge_uncertain': acknowledge_uncertain}))
    existing = row(execute(tx, 'SELECT * FROM workflow_kost_runs WHERE id=:id', {'id': request_id}))
    if existing:
        if existing['tenant'] != body['tenant'] or decode(existing)['request_sha256'] != digest:
            raise Conflict('Validation request identity has different content')
        return decode(existing)
    if not config.enabled:
        raise Conflict('KOST validation is not configured')
    active = row(execute(tx, '''SELECT * FROM workflow_kost_runs WHERE tenant=:tenant AND
        transfer_id=:transfer_id AND sip_id=:sip_id AND target_id=:target_id AND active=1''', binding))
    timestamp = clock(tx)
    if active:
        current = decode(active)
        if current['state'] != 'SUBMISSION_UNKNOWN' or not acknowledge_uncertain:
            return current
        current.update(state='COMPLETE', finished=timestamp, message='Uncertain submission superseded by operator; a remote job may still exist.')
        retain(tx, current, 'uncertain-submission-superseded', timestamp)
    result = dict(id=request_id, **binding, **{key: value for key, value in selected.items() if key != 'target_id'},
        state='QUEUED', result=None, actor=actor, trigger=trigger, created=timestamp, started=None,
        finished=None, remote_id=None, errors=[], message=None, last_error=None,
        report_sha256=None, response_sha256=None, report_status='pending',
        configuration_sha256=config.fingerprint(), language=config.language, tool_version=None,
        request_sha256=digest, attempts=0)
    raw = canonical(result).decode()
    execute(tx, '''INSERT INTO workflow_kost_runs
        (id,tenant,transfer_id,sip_id,target_id,active,state,lease_until,token,next_at,created,body,body_sha256)
        VALUES (:id,:tenant,:transfer_id,:sip_id,:target_id,1,'QUEUED',0,NULL,0,:created,:body,:digest)''',
        {**binding, 'id': request_id, 'created': timestamp, 'body': raw, 'digest': sha(raw.encode())})
    retain(tx, result, 'queued', timestamp)
    return result


def auto_enqueue(tx, body, sip_id, config, prepared_id=None):
    if config.enabled:
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, 'kost:'+body['tenant']+':'+body['id']+':'+sip_id+':'+(prepared_id or 'received')))
        enqueue(tx, body, sip_id, 'automatic', identity, config, prepared_id=prepared_id, trigger='freeze' if prepared_id else 'upload')


def public(body):
    return {key: value for key, value in body.items() if key not in ('request_sha256',)}


def inventory(service, ident, tenant, sip_id):
    body = service.get(ident, tenant)
    targets = [target(body, sip_id)] + [target(body, sip_id, item['id']) for item in body.get('prepared_inputs', []) if item['sip_id'] == sip_id]
    with service.store.transaction() as tx:
        for selected in targets:
            binding = dict(tenant=tenant, transfer_id=ident, sip_id=sip_id, target_id=selected['target_id'])
            latest = row(execute(tx, '''SELECT * FROM workflow_kost_runs WHERE tenant=:tenant AND transfer_id=:transfer_id
                AND sip_id=:sip_id AND target_id=:target_id ORDER BY created DESC,id DESC LIMIT 1''', binding))
            selected['latest'] = public(decode(latest)) if latest else None
    return {'enabled': service.kost_config.enabled, 'advisory': True, 'targets': targets}


def history(service, ident, tenant, sip_id, prepared_id=None, before=None):
    selected = target(service.get(ident, tenant), sip_id, prepared_id)
    values = dict(tenant=tenant, transfer_id=ident, sip_id=sip_id, target_id=selected['target_id'], before=before)
    cursor = ''
    with service.store.transaction() as tx:
        if before:
            previous = scoped(tx, ident, tenant, sip_id, before)
            if previous['target_id'] != selected['target_id']:
                raise KeyError(before)
            values.update(created=previous['created'])
            cursor = ' AND (created<:created OR (created=:created AND id<:before))'
        result = execute(tx, '''SELECT * FROM workflow_kost_runs WHERE tenant=:tenant AND transfer_id=:transfer_id
            AND sip_id=:sip_id AND target_id=:target_id'''+cursor+' ORDER BY created DESC,id DESC LIMIT 21', values)
        records = []
        while (item := row(result)) is not None:
            records.append(public(decode(item)))
    return {'runs': records[:20], 'next': records[19]['id'] if len(records) > 20 else None}


def scoped(tx, ident, tenant, sip_id, run_id):
    record = row(execute(tx, '''SELECT * FROM workflow_kost_runs WHERE id=:id AND tenant=:tenant
        AND transfer_id=:transfer_id AND sip_id=:sip_id''', dict(id=run_id, tenant=tenant, transfer_id=ident, sip_id=sip_id)))
    if record is None:
        raise KeyError(run_id)
    return decode(record)


def start(service, ident, tenant, actor, sip_id, request):
    with service.store.transaction() as tx:
        body = tx.transfer(ident, tenant)
        return public(enqueue(tx, body, sip_id, actor, request['request_id'], service.kost_config,
            prepared_id=request.get('prepared_id'), expected_sha256=request['input_sha256'],
            acknowledge_uncertain=request.get('acknowledge_uncertain', False)))


def claim(service):
    with service.store.transaction() as tx:
        timestamp = clock(tx)
        suffix = '' if isinstance(tx.db, sqlite3.Connection) else ' FOR UPDATE SKIP LOCKED'
        record = row(execute(tx, '''SELECT * FROM workflow_kost_runs WHERE active=1
            AND state<>'SUBMISSION_UNKNOWN' AND next_at<=:now AND lease_until<=:now
            ORDER BY next_at,created LIMIT 1'''+suffix, {'now': timestamp}))
        if record is None:
            return None
        body = decode(record)
        if body['state'] == 'SUBMITTING':
            body.update(state='SUBMISSION_UNKNOWN', message='Submission acknowledgement was lost; a remote job may exist.')
            retain(tx, body, 'submission-uncertain', timestamp)
            return None
        token = str(uuid.uuid4())
        execute(tx, 'UPDATE workflow_kost_runs SET token=:token,lease_until=:expiry WHERE id=:id',
            dict(token=token, expiry=timestamp+300, id=body['id']))
        if body['state'] == 'QUEUED':
            body.update(state='SUBMITTING', started=body['started'] or timestamp)
        retain(tx, body, 'claimed', timestamp)
        return body, token, timestamp


def complete_step(service, body, token, delay=0):
    with service.store.transaction() as tx:
        timestamp = clock(tx)
        changed = execute(tx, '''UPDATE workflow_kost_runs SET token=NULL,lease_until=0,next_at=:next
            WHERE id=:id AND token=:token AND lease_until>:now''',
            dict(next=timestamp+delay, id=body['id'], token=token, now=timestamp))
        if changed.rowcount != 1:
            raise Conflict('KOST worker lease expired or was replaced')
        retain(tx, body, 'worker-update', timestamp)


def step(service, client):
    from .service import put
    import httpx
    claimed = claim(service)
    if claimed is None:
        return False
    body, token, timestamp = claimed
    config = service.kost_config
    delay = config.poll
    submission_attempted = False
    try:
        if body['configuration_sha256'] != config.fingerprint():
            raise ValueError('KOST service configuration changed; request a new validation')
        if timestamp-body['created'] > config.deadline:
            raise ValueError('KOST validation deadline exceeded; remote work may still be running')
        if body['state'] == 'REPORT':
            body['report_sha256'] = put(service.data, client.report(body['remote_id']))
            body.update(state='COMPLETE', report_status='retained', finished=timestamp, last_error=None)
        else:
            submitting = body['state'] == 'SUBMITTING'
            if submitting:
                selected = target(service.get(body['transfer_id'], body['tenant']), body['sip_id'], body['prepared_id'])
                body['filename'] = selected['filename']
                if not body['filename']:
                    body.update(state='COMPLETE', result='error', report_status='unavailable', finished=timestamp,
                        message='Original upload filename was not retained. Upload the SIP into a new transfer to validate it without renaming.')
                    complete_step(service, body, token)
                    return True
                data = service.blob(body['input_sha256'])
                submission_attempted = True
                job, raw = client.submit(data, body['filename'], body['language'])
            else:
                job, raw = client.status(body['remote_id'])
                if job['id'] != body['remote_id']:
                    raise ValueError('KOST returned a different job identity')
            body.update(remote_id=job['id'], response_sha256=put(service.data, raw),
                remote_status=job['status'], errors=job['errors'], message=job['message'],
                exit_code=job['exit_code'], last_error=None, attempts=0,
                remote_started=job['started_at'], remote_finished=job['finished_at'])
            if job['status'] in ('valid', 'invalid', 'error'):
                body.update(result=job['status'], state='REPORT' if job['report_url'] else 'COMPLETE',
                    report_status='pending' if job['report_url'] else 'unavailable', finished=timestamp)
            else:
                body['state'] = 'TRACKING'
    except Exception as exc:
        if not isinstance(exc, (httpx.HTTPError, ValueError, OSError, PackageError)):
            raise
        body['attempts'] += 1
        body['last_error'] = 'KOST service request failed ('+type(exc).__name__+').'
        delay = min(60, config.poll*2**min(body['attempts'], 4))
        rejected = isinstance(exc, httpx.HTTPStatusError) and 400 <= exc.response.status_code < 500
        if submission_attempted and not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)) and not rejected:
            body.update(state='SUBMISSION_UNKNOWN', message='Submission could not be confirmed; a remote job may exist.')
        elif body['state'] == 'REPORT':
            if body['attempts'] >= 5 or timestamp-body['created'] > config.deadline:
                body.update(state='COMPLETE', report_status='unavailable', finished=timestamp)
        elif rejected or isinstance(exc, (ValueError, PackageError)) or timestamp-body['created'] > config.deadline:
            body.update(state='COMPLETE', result='error', message='Validation could not complete; check service configuration or retry.', finished=timestamp, report_status='unavailable')
        elif body['state'] == 'SUBMITTING':
            body['state'] = 'QUEUED'
    complete_step(service, body, token, delay)
    return True