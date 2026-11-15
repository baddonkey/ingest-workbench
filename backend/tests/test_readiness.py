"""Readiness failures stay separate from application liveness and acceptance."""
import shutil
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import main
from app.service import Service

ROOT=Path(__file__).resolve().parents[2]


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        shutil.copytree(ROOT/'schemas',self.root/'schemas')
        self.original=main.service
        main.service=Service(self.root/'data',self.root/'schemas')
        self.client=TestClient(main.app)

    def tearDown(self):
        self.client.close()
        main.service=self.original
        self.temp.cleanup()

    def test_ready_is_public_and_does_not_enable_acceptance(self):
        before=main.service.store.path.read_bytes()
        response=self.client.get('/ready')
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(response.json()['checks'].values()))
        self.assertFalse(response.json()['finalization_enabled'])
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(main.service.store.path.read_bytes(),before)

    def test_missing_or_changed_schema_fails_readiness_only(self):
        for relative in ('1.0/arelda.xsd','preservation/mets.xsd'):
            path=main.service.schemas/relative
            original=path.read_bytes()
            for changed in (None,original+b'<!-- unexpected drift -->'):
                with self.subTest(relative=relative,missing=changed is None):
                    if changed is None:path.unlink()
                    else:path.write_bytes(changed)
                    response=self.client.get('/ready')
                    self.assertEqual(response.status_code,503)
                    self.assertEqual(self.client.get('/health').status_code,200)
                    self.assertNotIn(str(self.root),response.text)
            path.write_bytes(original)

    def test_missing_database_is_not_recreated(self):
        main.service.store.path.unlink()
        response=self.client.get('/ready')
        self.assertEqual(response.status_code,503)
        self.assertFalse(response.json()['checks']['workflow_database'])
        self.assertFalse(main.service.store.path.exists())
