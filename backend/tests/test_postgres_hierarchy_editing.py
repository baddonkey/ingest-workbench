"""Hierarchy contracts against isolated native PostgreSQL schemas."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
from test_postgres_metadata_drafts import IsolatedDraftStore
import test_hierarchy_editing as hierarchy


class PostgresHierarchyTests(IsolatedDraftStore,hierarchy.HierarchyTests):pass
class PostgresHierarchyCandidateTests(IsolatedDraftStore,hierarchy.HierarchyCandidateTests):pass
