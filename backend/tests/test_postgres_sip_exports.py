"""Derived export contracts against isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
import test_postgres_metadata_drafts as postgres
import test_sip_exports as exports


class PostgresExportTests(postgres.IsolatedDraftStore, exports.ExportTests):
    pass


class PostgresExportApiTests(postgres.IsolatedDraftStore, exports.ExportApiTests):
    pass
