"""Frozen-input preparation review on isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):raise unittest.SkipTest('Native PostgreSQL URL required')
import test_prepared_review as contracts
from test_postgres_metadata_drafts import IsolatedDraftStore
from app.persistence_transfer import export_sqlite,import_sqlite
from app.service import Service
from app import prepared_review


class PostgresReviewTests(IsolatedDraftStore,contracts.ReviewTests):
    def test_aggregate_migration_preserves_review_snapshots_and_active_pointer(self):
        self.plan();record=self.review()['review']
        snapshot=self.service.data/'review-export.sqlite'
        export_sqlite(self.service.store,snapshot)
        store=self.new_store();import_sqlite(store,snapshot,quiesced=True)
        migrated=Service(self.service.data,self.service.schemas,store=store)
        self.assertEqual(migrated.get(self.transfer['id'],'archive-a'),self.current())
        self.assertEqual(prepared_review.artifact(migrated,self.transfer['id'],'archive-a',record['id']),self.service.blob(record['evidence_sha256']))


class PostgresReviewApiTests(IsolatedDraftStore,contracts.ReviewApiTests):
    pass
