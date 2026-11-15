"""Frozen input contracts against isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
from test_postgres_metadata_drafts import IsolatedDraftStore
import test_prepared_inputs as prepared
from app import prepared_inputs
from app.persistence_transfer import export_sqlite, import_sqlite
from app.service import Service


class PostgresPreparedTests(IsolatedDraftStore, prepared.PreparedTests):
    def test_aggregate_sqlite_migration_preserves_exact_frozen_records_and_artifacts(self):
        self.edit()
        first = self.freeze()['prepared_input']
        self.operation('checkpoint', name='Successor preparation checkpoint')
        self.freeze()
        before = self.current()
        snapshot = self.service.data/'aggregate-export.sqlite'
        export_sqlite(self.service.store, snapshot)
        target = self.new_store()
        import_sqlite(target, snapshot, quiesced=True)
        migrated = Service(self.service.data, self.service.schemas, store=target)
        self.assertEqual(migrated.get(before['id'], before['tenant']), before)
        for kind in ('zip', 'evidence'):
            self.assertEqual(prepared_inputs.artifact(migrated, before['id'], before['tenant'], self.sip['id'], first['id'], kind),
                             prepared_inputs.artifact(self.service, before['id'], before['tenant'], self.sip['id'], first['id'], kind))


class PostgresPreparedApiTests(IsolatedDraftStore, prepared.PreparedApiTests):
    pass
