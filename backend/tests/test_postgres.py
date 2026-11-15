"""Opt-in tests against a real local PostgreSQL instance, not an SQLite emulation."""
import copy
import os
import sqlite3
import tempfile
import unittest
import uuid
import importlib.util
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
if not os.environ.get('TEST_POSTGRES_URL') and any(importlib.util.find_spec(name) is None for name in ('sqlalchemy','alembic','psycopg')):
    raise unittest.SkipTest('Optional PostgreSQL dependencies not installed')
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,select,update,func,delete
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema,DropSchema
from app.postgres import PostgresStore,aggregates,outbox,metadata,canonical
from app.persistence_transfer import import_sqlite,export_sqlite
from app.service import Service,event
from app.store import Conflict
from app.packages import PackageError,sha

ROOT=Path(__file__).resolve().parents[2]
URL=os.environ.get('TEST_POSTGRES_URL')


@unittest.skipUnless(URL,'TEST_POSTGRES_URL required for native PostgreSQL qualification')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema='qualification_'+uuid.uuid4().hex
        cls.admin=create_engine(URL)
        with cls.admin.begin() as db:db.execute(CreateSchema(cls.schema))
        scoped=make_url(URL).update_query_dict({'options':'-c search_path='+cls.schema})
        cls.store=PostgresStore(scoped)
        cls.config=Config(str(ROOT/'backend/alembic.ini'))
        with cls.store.engine.begin() as db:
            cls.config.attributes['connection']=db
            command.upgrade(cls.config,'head')

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        with cls.admin.begin() as db:db.execute(DropSchema(cls.schema,cascade=True))
        cls.admin.dispose()

    def setUp(self):
        with self.store.engine.begin() as db:
            db.execute(delete(outbox));db.execute(delete(aggregates))
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(Path(self.temp.name),ROOT/'schemas')
        self.body=self.service.create('Persistence qualification','tenant-a','operator')

    def tearDown(self):self.temp.cleanup()

    def test_migration_roundtrip_and_metadata_agreement(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        with self.store.engine.begin() as db:
            self.assertEqual(compare_metadata(MigrationContext.configure(db),metadata),[])
            self.config.attributes['connection']=db
            command.downgrade(self.config,'base')
            command.upgrade(self.config,'head')

    def test_create_save_tenant_and_event_binding(self):
        b=self.store.create(self.body)
        self.assertEqual(self.store.get(b['id'],'tenant-a'),b)
        with self.assertRaises(KeyError):self.store.get(b['id'],'other')
        self.assertEqual(self.store.list('other'),[])
        event(b,'mapping-draft','operator',{'unknown_source_field':'ä — preserved'})
        saved=self.store.save(b,0)
        with self.assertRaises(Conflict):self.store.save(b,0)
        with self.store.engine.connect() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(outbox)),2)
        self.assertEqual(saved['events'],b['events'])

    def test_paged_transfers(self):
        for i in range(35):
            body=self.service.create(f'Transfer {i:03}','tenant-a','operator')
            if i==0:body['state']='INSPECTED';body['sips']=[{},{}]
            self.store.create(body)
            if i==0:self.store.save(body,0)
        self.store.create(self.service.create('Other tenant','tenant-b','operator'))
        first=self.store.list_page('tenant-a','','title','ascending',0,20)
        second=self.store.list_page('tenant-a','','title','ascending',first['next_offset'],20)
        self.assertEqual((first['total'],first['matched'],first['next_offset']),(35,35,20))
        self.assertEqual([x['title'] for x in first['items']+second['items']],
                         [f'Transfer {i:03}' for i in range(35)])
        self.assertIsNone(second['next_offset'])
        self.assertEqual(self.store.list_page('tenant-a','034','title','descending',0,20)['matched'],1)
        for sort in ('state','sips','revision'):
            self.assertEqual(self.store.list_page('tenant-a','',sort,'descending',0,1)['items'][0]['title'],
                             'Transfer 000' if sort!='state' else 'Transfer 034')
        self.assertEqual(self.store.list_page('tenant-b','','created','descending',0,20)['total'],1)

    def test_outbox_failure_rolls_back_aggregate_update(self):
        b=self.store.create(self.body)
        # A conflict with another aggregate's globally unique event ID must roll back both writes.
        other=self.service.create('Other','tenant-a','operator')
        other['events'][0]['id']=b['events'][0]['id']
        other['events'][0]['hash']=sha(canonical({k:v for k,v in other['events'][0].items() if k!='hash'}).encode())
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):self.store.create(other)
        with self.assertRaises(KeyError):self.store.get(other['id'],'tenant-a')
        # A delivery insert failure after snapshot update must leave the old revision intact.
        event(b,'next','operator',{})
        original=self.store.notify
        self.store.notify=lambda *args: (_ for _ in ()).throw(RuntimeError('Injected outbox failure'))
        try:
            with self.assertRaises(RuntimeError):self.store.save(b,0)
        finally:self.store.notify=original
        self.assertEqual(self.store.get(b['id'],'tenant-a')['revision'],0)

    def test_concurrent_updates_have_one_winner(self):
        b=self.store.create(self.body)
        def write(actor):
            changed=copy.deepcopy(b);event(changed,'change',actor,{})
            try:self.store.save(changed,0);return 'saved'
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(write,['worker-a','worker-b']))
        self.assertCountEqual(results,['saved','conflict'])

    def test_exclusive_claim_expiry_replay_and_tenant(self):
        self.store.create(self.body)
        def claim(worker):return self.store.claim('tenant-a',worker)
        with ThreadPoolExecutor(2) as pool:claims=list(pool.map(claim,['a','b']))
        active=next(c for c in claims if c)
        owner='a' if claims[0] else 'b'
        self.assertEqual(sum(c is not None for c in claims),1)
        until=self.store.heartbeat(active['id'],'tenant-a',owner,active['lease_token'],180)
        self.assertGreater(until,active['lease_until'])
        self.assertIsNone(self.store.claim('other','a'))
        with self.assertRaises(Conflict):self.store.finish(active['id'],'tenant-a','wrong',active['lease_token'])
        with self.store.engine.begin() as db:
            db.execute(update(outbox).values(lease_until=func.now()-timedelta(seconds=1)))
        reclaimed=self.store.claim('tenant-a','replacement')
        self.assertEqual(reclaimed['attempt'],2)
        with self.assertRaises(Conflict):self.store.finish(active['id'],'tenant-a',owner,active['lease_token'])
        with self.assertRaises(Conflict):self.store.heartbeat(active['id'],'tenant-a',owner,active['lease_token'])
        self.store.finish(reclaimed['id'],'tenant-a','replacement',reclaimed['lease_token'])
        self.store.finish(reclaimed['id'],'tenant-a','replacement',reclaimed['lease_token'])
        self.assertIsNone(self.store.claim('tenant-a','next'))

    def test_retry_is_delayed_and_capability_is_superseded(self):
        self.store.create(self.body);leased=self.store.claim('tenant-a','worker')
        self.store.finish(leased['id'],'tenant-a','worker',leased['lease_token'],retry=True)
        self.assertIsNone(self.store.claim('tenant-a','worker'))
        with self.assertRaises(Conflict):self.store.finish(leased['id'],'tenant-a','worker',leased['lease_token'])

    def test_sqlite_import_exact_bytes_idempotence_and_reverse_export(self):
        b=self.service.upload(self.body['id'],'tenant-a','operator',0,'1.3.0',(ROOT/'fixtures/reference-1.3.zip').read_bytes())
        b['institution_extension']={'unknown':['ä','undetermined']}
        self.service.store.save(b,b['revision'])
        source=self.service.store.path;before=source.read_bytes()
        report=import_sqlite(self.store,source,quiesced=True)
        self.assertEqual(report['imported'],1);self.assertEqual(report['verified_source_blobs'],1)
        self.assertEqual(import_sqlite(self.store,source,quiesced=True)['reused'],1)
        exported=Path(self.temp.name)/'export.sqlite'
        export_sqlite(self.store,exported)
        with closing(sqlite3.connect(source)) as a,closing(sqlite3.connect(exported)) as b:
            self.assertEqual(a.execute('SELECT id,tenant,revision,body FROM aggregates').fetchall(),
                             b.execute('SELECT id,tenant,revision,body FROM aggregates').fetchall())
        with self.assertRaises(FileExistsError):export_sqlite(self.store,source)
        self.assertEqual(source.read_bytes(),before)
        with self.store.engine.connect() as db:self.assertEqual(db.scalar(select(func.count()).select_from(outbox)),0)

    def test_import_rejects_conflicts_and_bad_blobs(self):
        with self.assertRaises(ValueError):import_sqlite(self.store,self.service.store.path)
        self.store.create(self.body)
        changed=self.service.create('New','tenant-a','operator')
        event(self.body,'change','operator',{});self.service.store.save(self.body,0)
        with self.assertRaises(Conflict):import_sqlite(self.store,self.service.store.path,quiesced=True)
        with self.assertRaises(KeyError):self.store.get(changed['id'],'tenant-a')
        uploaded=self.service.upload(changed['id'],'tenant-a','operator',0,'1.3.0',(ROOT/'fixtures/reference-1.3.zip').read_bytes())
        (Path(self.temp.name)/'blobs'/uploaded['sips'][0]['sha256']).write_bytes(b'tampered')
        with self.assertRaisesRegex(PackageError,'blob digest'):import_sqlite(self.store,self.service.store.path,quiesced=True)

    def test_snapshot_corruption_and_event_replacement_are_rejected(self):
        b=self.store.create(self.body)
        changed=copy.deepcopy(b);changed['events']=[]
        with self.assertRaisesRegex(PackageError,'cannot be replaced'):self.store.save(changed,0)
        with self.store.engine.begin() as db:db.execute(update(aggregates).values(body='{}'))
        with self.assertRaises(PackageError):self.store.get(b['id'],'tenant-a')

    def test_aggregate_notifications_are_delivered_in_event_order(self):
        b=self.store.create(self.body);event(b,'second','operator',{})
        self.store.save(b,0)
        first=self.store.claim('tenant-a','one')
        self.assertEqual(first['payload']['id'],b['events'][0]['id'])
        self.assertIsNone(self.store.claim('tenant-a','two'))
        self.store.finish(first['id'],'tenant-a','one',first['lease_token'])
        second=self.store.claim('tenant-a','two')
        self.assertEqual(second['payload']['id'],b['events'][1]['id'])

    def test_corrupt_notification_does_not_receive_a_lease(self):
        self.store.create(self.body)
        with self.store.engine.begin() as db:db.execute(update(outbox).values(payload='{}'))
        with self.assertRaisesRegex(PackageError,'Outbox payload'):self.store.claim('tenant-a','worker')
        with self.store.engine.connect() as db:self.assertEqual(db.scalar(select(outbox.c.state)),'READY')
