"""Same working editor contracts against isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):raise unittest.SkipTest('Native PostgreSQL URL required')
import test_postgres_metadata_drafts as postgres
import test_working_metadata as working


class PostgresWorkingTests(postgres.IsolatedDraftStore,working.WorkingTests):pass
class PostgresWorkingApiTests(postgres.IsolatedDraftStore,working.WorkingApiTests):pass
