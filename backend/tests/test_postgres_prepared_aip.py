"""Prepared AIP contracts on native PostgreSQL with isolated schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):raise unittest.SkipTest('Native PostgreSQL URL required')
import test_prepared_aip as contracts
from test_postgres_metadata_drafts import IsolatedDraftStore
from app.persistence_transfer import export_sqlite,import_sqlite
from app import prepared_aip


class PostgresPreparedAipTests(IsolatedDraftStore,contracts.PreparedAipTests):
    def test_migration_retains_prepared_authorization_and_candidate(self):
        self.prepare();self.build()
        path=self.service.data/'prepared-candidate.sqlite';export_sqlite(self.service.store,path)
        store=self.new_store();result=import_sqlite(store,path,quiesced=True)
        self.assertEqual(result['imported'],1)
        body=store.get(self.transfer['id'],'archive-a');self.assertEqual(body,self.current())
        prepared_aip.validate_retained(body,self.service.blob,self.service.schemas)


class PostgresPreparedAipApiTests(IsolatedDraftStore,contracts.PreparedAipApiTests):
    pass
