"""KOST journal parity against an isolated native PostgreSQL schema."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
from test_postgres_metadata_drafts import IsolatedDraftStore
import test_kost_validation as kost


class PostgresKostJournalTests(IsolatedDraftStore, kost.KostJournalTests):
    def service_store(self):
        return self.new_store()


class PostgresKostWorkflowTests(IsolatedDraftStore, kost.KostWorkflowTests):
    def service_store(self):
        return self.new_store()