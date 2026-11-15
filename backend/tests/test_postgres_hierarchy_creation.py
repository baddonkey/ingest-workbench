"""Native PostgreSQL creation/history/freeze/candidate contracts in isolated schemas."""
import os,unittest
if not os.environ.get('TEST_POSTGRES_URL'):raise unittest.SkipTest('Native PostgreSQL URL required')
from test_postgres_metadata_drafts import IsolatedDraftStore
import test_hierarchy_creation as creation

class PostgresCreationTests(IsolatedDraftStore,creation.CreationTests):pass
class PostgresCreationCandidateTests(IsolatedDraftStore,creation.CreationCandidateTests):pass
