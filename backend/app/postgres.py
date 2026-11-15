"""Optional PostgreSQL persistence foundation, not yet the application's runtime store.

The outbox delivers immutable workflow notifications at least once. It must not
directly retry external repository/AIS writes after an uncertain outcome.
"""
import copy
import json
import secrets
from contextlib import contextmanager
from datetime import timedelta
from sqlalchemy import (MetaData,Table,Column,Text,String,Integer,BigInteger,Identity,
                        DateTime,Float,ForeignKey,CheckConstraint,Index,UniqueConstraint,create_engine,select,insert,update,func,and_,or_,exists,text)
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import JSONB
from .packages import sha,PackageError
from .store import Conflict, validate_event_chain

metadata=MetaData()
aggregates=Table('workflow_aggregates',metadata,
    Column('id',Text,primary_key=True),Column('tenant',Text,nullable=False),
    Column('revision',Integer,nullable=False),Column('body',Text,nullable=False),
    Column('body_sha256',String(64),nullable=False),Column('sequence',BigInteger,Identity(),nullable=False),
    CheckConstraint('revision >= 0',name='workflow_revision_nonnegative'))
Index('workflow_tenant_sequence',aggregates.c.tenant,aggregates.c.sequence)
outbox=Table('workflow_outbox',metadata,
    Column('id',Text,primary_key=True),Column('aggregate_id',Text,ForeignKey('workflow_aggregates.id'),nullable=False),
    Column('tenant',Text,nullable=False),Column('revision',Integer,nullable=False),
    Column('position',Integer,nullable=False),
    Column('payload',Text,nullable=False),Column('payload_sha256',String(64),nullable=False),
    Column('state',Text,nullable=False,server_default='READY'),Column('attempt',Integer,nullable=False,server_default='0'),
    Column('available_at',DateTime(timezone=True),nullable=False,server_default=func.now()),
    Column('lease_until',DateTime(timezone=True)),Column('owner',Text),Column('token_sha256',String(64)),
    CheckConstraint("state IN ('READY','LEASED','DELIVERED')",name='outbox_state_allowed'),
    CheckConstraint('attempt >= 0',name='outbox_attempt_nonnegative'),
    CheckConstraint('position >= 0',name='outbox_position_nonnegative'),
    UniqueConstraint('aggregate_id','position',name='outbox_aggregate_position_unique'))
Index('outbox_tenant_delivery',outbox.c.tenant,outbox.c.state,outbox.c.available_at)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)


def validate_body(body):
    if (not isinstance(body,dict) or not isinstance(body.get('id'),str) or not body['id']
            or not isinstance(body.get('tenant'),str) or not body['tenant']
            or type(body.get('revision')) is not int or body['revision']<0
            or not isinstance(body.get('events'),list)):
        raise PackageError('Invalid workflow aggregate')
    validate_event_chain(body['events'])


class PostgresStore:
    backend='postgresql'
    def __init__(self,url):
        if make_url(url).drivername!='postgresql+psycopg':
            raise ValueError('PostgreSQL psycopg URL required')
        self.engine=create_engine(url,pool_pre_ping=True,connect_args={'connect_timeout':5})

    def close(self):self.engine.dispose()

    def ready(self):
        try:
            with self.engine.connect() as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                db.execute(text("SET LOCAL statement_timeout = '500ms'"))
                revision=db.scalar(text('SELECT version_num FROM alembic_version'))
                db.execute(select(aggregates.c.id).limit(0))
                db.execute(text('SELECT id FROM workflow_kost_runs LIMIT 0'))
                return revision=='0005_kost_service'
        except Exception:return False

    @contextmanager
    def transaction(self):
        with self.engine.begin() as db:yield PostgresTransaction(self,db)

    @staticmethod
    def decode(row):
        if sha(row['body'].encode())!=row['body_sha256']:raise PackageError('Workflow snapshot digest mismatch')
        body=json.loads(row['body']);validate_body(body)
        if (body['id'],body['tenant'],body['revision'])!=(row['id'],row['tenant'],row['revision']):
            raise PackageError('Workflow snapshot binding mismatch')
        return body

    def get(self,ident,tenant):
        with self.engine.connect() as db:
            row=db.execute(select(aggregates).where(aggregates.c.id==ident,aggregates.c.tenant==tenant)).mappings().first()
            if row is None:raise KeyError(ident)
            return self.decode(row)

    def list(self,tenant):
        with self.engine.connect() as db:
            return [self.decode(r) for r in db.execute(select(aggregates).where(aggregates.c.tenant==tenant)
                .order_by(aggregates.c.sequence.desc()).limit(100)).mappings()]

    def list_page(self,tenant,query,sort,direction,offset,limit):
        body=aggregates.c.body.cast(JSONB)
        title=func.lower(body['title'].astext)
        state=func.lower(body['state'].astext)
        order={'created':aggregates.c.sequence,'title':title,'state':state,
               'sips':func.jsonb_array_length(body['sips']),'revision':aggregates.c.revision}[sort]
        tenant_filter=aggregates.c.tenant==tenant
        matching=and_(tenant_filter,or_(func.strpos(title,query.lower())>0,func.strpos(state,query.lower())>0))
        with self.engine.connect() as db:
            total=db.scalar(select(func.count()).select_from(aggregates).where(tenant_filter))
            matched=db.scalar(select(func.count()).select_from(aggregates).where(matching))
            rows=db.execute(select(aggregates).where(matching)
                .order_by(order.desc() if direction=='descending' else order.asc(),aggregates.c.sequence.desc())
                .limit(limit).offset(offset)).mappings()
            bodies=[self.decode(row) for row in rows]
        return {'items':bodies,'total':total,'matched':matched,'next_offset':offset+len(bodies) if offset+len(bodies)<matched else None}

    @staticmethod
    def notify(db,body,events):
        offset=len(body['events'])-len(events)
        for position,event in enumerate(events,offset):
            payload=canonical(event)
            db.execute(insert(outbox).values(id=event['id'],aggregate_id=body['id'],tenant=body['tenant'],
                revision=body['revision'],position=position,payload=payload,payload_sha256=sha(payload.encode())))

    def create(self,body):
        body=copy.deepcopy(body);validate_body(body)
        if body['revision']!=0:raise PackageError('New aggregate revision must be zero')
        raw=canonical(body)
        with self.engine.begin() as db:
            db.execute(insert(aggregates).values(id=body['id'],tenant=body['tenant'],revision=0,
                body=raw,body_sha256=sha(raw.encode())))
            self.notify(db,body,body['events'])
        return body

    def save(self,body,expected):
        body=copy.deepcopy(body);body['revision']=expected+1;validate_body(body)
        with self.engine.begin() as db:
            self.save_in_transaction(db,body,expected)
        return body

    def save_in_transaction(self,db,body,expected):
        body['revision']=expected+1;validate_body(body)
        row=db.execute(select(aggregates).where(aggregates.c.id==body['id'],aggregates.c.tenant==body['tenant'])
            .with_for_update()).mappings().first()
        if row is None or row['revision']!=expected:raise Conflict('Stale revision; reload and review')
        previous=self.decode(row)
        count=len(previous['events'])
        if body['events'][:count]!=previous['events']:raise PackageError('Retained workflow events cannot be replaced')
        raw=canonical(body)
        changed=db.execute(update(aggregates).where(aggregates.c.id==body['id'],aggregates.c.tenant==body['tenant'],
            aggregates.c.revision==expected).values(revision=body['revision'],body=raw,body_sha256=sha(raw.encode())))
        if changed.rowcount!=1:raise Conflict('Stale revision; reload and review')
        self.notify(db,body,body['events'][count:])

    def claim(self,tenant,owner,lease_seconds=120):
        if not tenant or not owner or not 1<=lease_seconds<=600:raise ValueError('Tenant, worker and bounded lease required')
        with self.engine.begin() as db:
            now=db.scalar(select(func.clock_timestamp()))
            earlier=outbox.alias('earlier')
            row=db.execute(select(outbox).where(outbox.c.tenant==tenant,or_(
                and_(outbox.c.state=='READY',outbox.c.available_at<=now),
                and_(outbox.c.state=='LEASED',outbox.c.lease_until<=now)),
                ~exists(select(1).where(earlier.c.aggregate_id==outbox.c.aggregate_id,
                    earlier.c.position<outbox.c.position,earlier.c.state!='DELIVERED')))
                .order_by(outbox.c.available_at,outbox.c.id).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:return None
            if sha(row['payload'].encode())!=row['payload_sha256']:raise PackageError('Outbox payload digest mismatch')
            token=secrets.token_urlsafe(32);until=now+timedelta(seconds=lease_seconds)
            db.execute(update(outbox).where(outbox.c.id==row['id']).values(state='LEASED',owner=owner,
                token_sha256=sha(token.encode()),lease_until=until,attempt=row['attempt']+1))
            return {'id':row['id'],'aggregate_id':row['aggregate_id'],'tenant':tenant,'revision':row['revision'],
                'payload':json.loads(row['payload']),'payload_sha256':row['payload_sha256'],
                'attempt':row['attempt']+1,'lease_until':until.isoformat(),'lease_token':token}

    def heartbeat(self,ident,tenant,owner,token,lease_seconds=120):
        if not 1<=lease_seconds<=600:raise ValueError('Bounded lease required')
        with self.engine.begin() as db:
            row=db.execute(select(outbox).where(outbox.c.id==ident,outbox.c.tenant==tenant).with_for_update()).mappings().first()
            if row is None:raise KeyError(ident)
            now=db.scalar(select(func.clock_timestamp()))
            if (row['state']!='LEASED' or row['lease_until']<=now or row['owner']!=owner
                    or not secrets.compare_digest(row['token_sha256'] or '',sha(token.encode()))):
                raise Conflict('Outbox lease expired, superseded or owned by another worker')
            until=now+timedelta(seconds=lease_seconds)
            db.execute(update(outbox).where(outbox.c.id==ident).values(lease_until=until))
            return until.isoformat()

    def finish(self,ident,tenant,owner,token,*,retry=False):
        with self.engine.begin() as db:
            row=db.execute(select(outbox).where(outbox.c.id==ident,outbox.c.tenant==tenant).with_for_update()).mappings().first()
            if row is None:raise KeyError(ident)
            if row['owner']!=owner or not secrets.compare_digest(row['token_sha256'] or '',sha(token.encode())):
                raise Conflict('Outbox lease belongs to another attempt or worker')
            if row['state']=='DELIVERED' and not retry:return
            now=db.scalar(select(func.clock_timestamp()))
            if row['state']!='LEASED' or row['lease_until']<=now:raise Conflict('Outbox lease expired or superseded')
            values={'state':'DELIVERED','lease_until':None}
            if retry:
                values.update(state='READY',available_at=now+timedelta(seconds=min(300,2**min(row['attempt'],9))),
                    owner=None,token_sha256=None)
            db.execute(update(outbox).where(outbox.c.id==ident).values(**values))


class PostgresTransaction:
    def __init__(self,store,db):self.store,self.db=store,db

    def transfer(self,ident,tenant):
        row=self.db.execute(select(aggregates).where(aggregates.c.id==ident,aggregates.c.tenant==tenant)
            .with_for_update()).mappings().first()
        if row is None:raise KeyError(ident)
        return self.store.decode(row)

    def save_transfer(self,body,expected):self.store.save_in_transaction(self.db,body,expected)

