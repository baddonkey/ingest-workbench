"""The same note and candidate contracts against isolated native PostgreSQL."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
from test_postgres_metadata_drafts import IsolatedDraftStore
import test_preparation_notes as notes


class PostgresNotesTests(IsolatedDraftStore, notes.NotesTests):
    pass


class PostgresNoteCandidateTests(IsolatedDraftStore, notes.NoteCandidateTests):
    pass
