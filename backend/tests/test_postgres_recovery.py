"""Native PostgreSQL recovery with isolated schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):raise unittest.SkipTest('Native PostgreSQL URL required')
import test_recovery
import test_postgres
from sqlalchemy import select,update,func
from app.postgres import aggregates,outbox
from app.recovery import restore
from app.store import Conflict
from unittest.mock import patch
from app.service import Service
from app.packages import PackageError


class PostgresRecoveryTests(test_recovery.RecoveryTests):
    def new_store(self):
        holder=type('IsolatedRecoveryStore',(),{})
        test_postgres.PostgresTests.setUpClass.__func__(holder)
        self.addCleanup(test_postgres.PostgresTests.tearDownClass.__func__,holder)
        return holder.store

    def test_retired_database_records_are_refused_and_preserved(self):
        from alembic.autogenerate import compare_metadata
        from alembic.migration import MigrationContext
        from app.postgres import metadata
        with self.source.store.engine.begin() as db:
            self.assertEqual(compare_metadata(MigrationContext.configure(db), metadata), [])
            db.exec_driver_sql('CREATE TABLE workflow_ais_jobs (id TEXT PRIMARY KEY)')
            db.exec_driver_sql("INSERT INTO workflow_ais_jobs VALUES ('retained')")
        with self.assertRaisesRegex(Conflict, 'Retired'):
            self.save()
        with self.source.store.engine.connect() as db:
            self.assertEqual(db.exec_driver_sql('SELECT id FROM workflow_ais_jobs').scalar_one(), 'retained')
        self.assertFalse(self.snapshot.exists())

    def test_retired_destination_records_are_not_overwritten(self):
        self.save()
        with self.target_store.engine.begin() as db:
            db.exec_driver_sql('CREATE TABLE workflow_ais_jobs (id TEXT PRIMARY KEY)')
            db.exec_driver_sql("INSERT INTO workflow_ais_jobs VALUES ('retained')")
        with self.assertRaisesRegex(Conflict, 'Retired'):
            self.recover()
        with self.target_store.engine.connect() as db:
            self.assertEqual(db.exec_driver_sql('SELECT id FROM workflow_ais_jobs').scalar_one(), 'retained')
        self.assertFalse(self.data.exists())

    def test_outbox_order_state_and_identity_highwater_are_preserved_and_leases_fenced(self):
        store=self.source.store
        first=store.claim('archive-demo','notifications');store.finish(first['id'],'archive-demo','notifications',first['lease_token'])
        active=store.claim('archive-demo','notifications')
        with store.engine.begin() as db:
            db.execute(select(func.setval(func.pg_get_serial_sequence(aggregates.name,'sequence'),5000,True)))
            originals=[dict(r) for r in db.execute(select(outbox).order_by(outbox.c.position)).mappings()]
        self.save();self.recover()
        with self.target_store.engine.connect() as db:
            rows={r['id']:dict(r) for r in db.execute(select(outbox)).mappings()}
            for original in originals:
                retained=rows[original['id']]
                for column in ('id','tenant','aggregate_id','position','payload','payload_sha256','revision','attempt','available_at'):
                    self.assertEqual(retained[column],original[column])
                self.assertEqual(retained['state'],'READY' if original['state']=='LEASED' else original['state'])
        with self.assertRaises(Conflict):self.target_store.heartbeat(active['id'],'archive-demo','notifications',active['lease_token'])
        resumed=self.target_store.claim('archive-demo','recovered-notifications');self.assertEqual(resumed['id'],active['id'])
        body=self.target.create('Post-restore identity','archive-demo','operator')
        with self.target_store.engine.connect() as db:
            self.assertGreater(db.scalar(select(aggregates.c.sequence).where(aggregates.c.id==body['id'])),5000)

    def test_nonempty_destination_is_refused_without_publishing_data(self):
        self.save();Service(self.folder/'occupied-store',test_recovery.ROOT/'schemas',store=self.target_store).create('Existing data','archive-demo','operator')
        with self.assertRaises(Conflict):self.recover()
        self.assertFalse(self.data.exists())

    def test_database_failure_rolls_back_all_rows_and_retains_complete_vault(self):
        self.save()
        with patch.object(self.target_store,'notify',side_effect=RuntimeError('Synthetic destination write failure')):
            with self.assertRaises(RuntimeError):self.recover()
        from app.recovery import TABLES
        with self.target_store.engine.connect() as db:
            self.assertTrue(all(db.scalar(select(func.count()).select_from(t))==0 for t in TABLES))
        self.assertFalse((self.data/'recovery-receipt.json').exists())
        self.assertTrue((self.data/'blobs'/self.fixture.output['sha256']).is_file())
