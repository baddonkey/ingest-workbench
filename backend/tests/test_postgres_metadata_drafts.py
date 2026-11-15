"""Same metadata draft/recovery contracts against isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
import test_postgres
import test_metadata_drafts


class IsolatedDraftStore:
    def new_store(self):
        holder = type('IsolatedMetadataDraftStore', (), {})
        test_postgres.PostgresTests.setUpClass.__func__(holder)
        self.addCleanup(test_postgres.PostgresTests.tearDownClass.__func__, holder)
        return holder.store


class PostgresDraftTests(IsolatedDraftStore, test_metadata_drafts.DraftTests):
    pass


class PostgresDraftApiTests(IsolatedDraftStore, test_metadata_drafts.DraftApiTests):
    pass
