"""Read-only Ingest Packager HTTP contracts against an isolated native database."""
import os
import unittest
if not os.environ.get('TEST_POSTGRES_URL'):
    raise unittest.SkipTest('Native PostgreSQL URL required')
import test_postgres
import test_package_handler


class PostgresPackageHandlerTests(test_package_handler.PackageHandlerApiTests):
    @classmethod
    def setUpClass(cls):
        test_postgres.PostgresTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        test_postgres.PostgresTests.tearDownClass.__func__(cls)

    def service_store(self):
        return self.store
