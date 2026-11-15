"""Single-node reference repository: atomic aggregate updates with optimistic concurrency."""
import json, sqlite3
from contextlib import contextmanager
from pathlib import Path
from .packages import PackageError, sha

class Conflict(ValueError): pass

def validate_event_chain(events):
    if not isinstance(events,list):raise PackageError('Invalid workflow event chain')
    previous='0'*64
    seen=set()
    for entry in events:
        if not isinstance(entry,dict):raise PackageError('Invalid retained workflow event')
        ident=entry.get('id')
        digest=sha(json.dumps({k:v for k,v in entry.items() if k!='hash'},sort_keys=True,
                              separators=(',',':'),ensure_ascii=False).encode())
        if (not isinstance(ident,str) or not ident or ident in seen
                or entry.get('previous')!=previous or digest!=entry.get('hash')):
            raise PackageError('Invalid retained workflow event chain')
        seen.add(ident)
        previous=entry['hash']

class Store:
    backend='sqlite'
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS aggregates (id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL)')
            db.execute('DROP TABLE IF EXISTS validation_jobs')
            from .kost_validation import init_sqlite
            init_sqlite(db)
    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def create(self, body):
        validate_event_chain(body.get('events'))
        with self.connection() as db:
            db.execute('INSERT INTO aggregates VALUES (?,?,?,?)', (body['id'], body['tenant'], 0, json.dumps(body)))
        return body

    def get(self,ident,tenant):
        with self.connection() as db:
            row=db.execute('SELECT body FROM aggregates WHERE id=? AND tenant=?',(ident,tenant)).fetchone()
        if not row:raise KeyError(ident)
        body=json.loads(row['body'])
        validate_event_chain(body.get('events'))
        return body

    def list(self,tenant):
        with self.connection() as db:
            bodies=[json.loads(r['body']) for r in db.execute('SELECT body FROM aggregates WHERE tenant=? ORDER BY rowid DESC LIMIT 100',(tenant,))]
        for body in bodies:validate_event_chain(body.get('events'))
        return bodies

    def list_page(self,tenant,query,sort,direction,offset,limit):
        order={'created':'rowid','title':"lower_unicode(json_extract(body,'$.title'))",
               'state':"lower_unicode(json_extract(body,'$.state'))",
               'sips':"json_array_length(body,'$.sips')",'revision':'revision'}[sort]
        descending=direction=='descending'
        where="tenant=? AND (instr(lower_unicode(json_extract(body,'$.title')),?)>0 OR instr(lower_unicode(json_extract(body,'$.state')),?)>0)"
        with self.connection() as db:
            db.create_function('lower_unicode',1,str.lower,deterministic=True)
            total=db.execute('SELECT count(*) FROM aggregates WHERE tenant=?',(tenant,)).fetchone()[0]
            args=(tenant,query.lower(),query.lower())
            matched=db.execute('SELECT count(*) FROM aggregates WHERE '+where,args).fetchone()[0]
            rows=db.execute('SELECT body FROM aggregates WHERE '+where+' ORDER BY '+order+
                (' DESC' if descending else ' ASC')+', rowid DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        bodies=[json.loads(row['body']) for row in rows]
        for body in bodies:validate_event_chain(body.get('events'))
        return {'items':bodies,'total':total,'matched':matched,'next_offset':offset+len(bodies) if offset+len(bodies)<matched else None}

    def save(self,body,expected):
        with self.transaction() as transaction:transaction.save_transfer(body,expected)
        return body

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            yield SQLiteTransaction(db)

    def ready(self):
        try:
            db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=.25)
            try:db.execute('SELECT id, tenant, revision, body FROM aggregates LIMIT 0')
            finally:db.close()
            return True
        except (sqlite3.Error,OSError):return False


class SQLiteTransaction:
    def __init__(self,db):self.db=db

    def transfer(self,ident,tenant):
        row=self.db.execute('SELECT body FROM aggregates WHERE id=? AND tenant=?',(ident,tenant)).fetchone()
        if not row:raise KeyError(ident)
        return json.loads(row['body'])

    def save_transfer(self,body,expected):
        row=self.db.execute('SELECT revision,body FROM aggregates WHERE id=? AND tenant=?',
                            (body['id'],body['tenant'])).fetchone()
        if row is None or row['revision']!=expected:raise Conflict('Stale transfer revision')
        previous=json.loads(row['body'])
        old_events=previous.get('events')
        validate_event_chain(old_events)
        validate_event_chain(body.get('events'))
        if body['events'][:len(old_events)]!=old_events:
            raise PackageError('Retained workflow events cannot be replaced')
        body['revision']=expected+1
        result=self.db.execute('UPDATE aggregates SET revision=?,body=? WHERE id=? AND tenant=? AND revision=?',
            (body['revision'],json.dumps(body),body['id'],body['tenant'],expected))
        if result.rowcount!=1:raise Conflict('Stale transfer revision')

