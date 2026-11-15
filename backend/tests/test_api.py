import os,tempfile,unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.auth import principal, Principal
from app.service import Service
from app import main
ROOT=Path(__file__).resolve().parents[2]

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.original=main.service;main.service=Service(Path(self.temp.name),ROOT/'schemas')
        self.client=TestClient(main.app)
    def tearDown(self):
        main.app.dependency_overrides.clear();main.service=self.original;self.temp.cleanup()
    def user(self,name,tenant='demo',roles=('archive-reader','archive-operator')):
        main.app.dependency_overrides[principal]=lambda:Principal(name,tenant,frozenset(roles))
    def test_auth_required(self):self.assertEqual(self.client.get('/api/transfers').status_code,401)
    def test_retired_delivery_routes_are_not_exposed(self):
        routes=main.app.openapi()['paths']
        self.assertFalse(any('delivery-operations' in path or 'crosswalk-exports' in path or path=='/api/delivery/policy' for path in routes))
        from importlib.util import find_spec
        self.assertIsNone(find_spec('app.delivery'))
        self.assertIsNone(find_spec('app.delivery_worker'))
    def test_roles_and_tenant(self):
        self.user('reader',roles=('archive-reader',));self.assertEqual(self.client.post('/api/transfers',json={'title':'x'}).status_code,403)
        self.user('operator');t=self.client.post('/api/transfers',json={'title':'x'}).json()
        self.user('operator','other');self.assertEqual(self.client.get('/api/transfers/'+t['id']).status_code,404)
    def test_paged_transfers_filter_sort_and_tenant(self):
        self.user('operator')
        for i in range(105):main.service.create(f'Transfer {i:03}','demo','operator')
        main.service.create('Special 100% Ä','demo','operator')
        main.service.create('Other tenant','other','operator')
        results=[]
        for offset in range(0,106,20):
            page=self.client.get('/api/transfer-pages',params={'sort':'title','direction':'ascending','offset':offset,'limit':20}).json()
            self.assertEqual((page['total'],page['matched']),(106,106))
            results.extend(t['title'] for t in page['items'])
            self.assertEqual(page['next_offset'],offset+20 if offset+20<106 else None)
        self.assertEqual(len(results),106);self.assertEqual(len(set(results)),106)
        self.assertEqual(results[0],'Special 100% Ä');self.assertEqual(results[-1],'Transfer 104')
        page=self.client.get('/api/transfer-pages',params={'q':'100% ä'}).json()
        self.assertEqual((page['total'],page['matched'],page['items'][0]['title']),(106,1,'Special 100% Ä'))
        self.assertEqual(self.client.get('/api/transfer-pages',params={'q':'received'}).json()['matched'],106)
        self.assertEqual(self.client.get('/api/transfer-pages',params={'limit':101}).status_code,422)
        self.assertEqual(self.client.get('/api/transfer-pages',params={'sort':'invalid'}).status_code,422)
        self.user('reader','other',roles=('archive-reader',))
        page=self.client.get('/api/transfer-pages').json()
        self.assertEqual((page['total'],page['matched'],page['items'][0]['title']),(1,1,'Other tenant'))
    def test_paged_transfer_sort_columns(self):
        self.user('operator')
        alpha=main.service.create('Alpha','demo','operator')
        beta=main.service.create('Beta','demo','operator')
        alpha['state']='INSPECTED';alpha['sips']=[{},{}];main.service.store.save(alpha,0)
        main.service.store.save(alpha,1)
        beta['sips']=[{}];main.service.store.save(beta,0)
        for column,first in [('title','Alpha'),('state','Alpha'),('sips','Beta'),('revision','Beta')]:
            with self.subTest(column=column):
                params={'sort':column,'direction':'ascending','limit':1}
                self.assertEqual(self.client.get('/api/transfer-pages',params=params).json()['items'][0]['title'],first)
                params['direction']='descending'
                self.assertNotEqual(self.client.get('/api/transfer-pages',params=params).json()['items'][0]['title'],first)
    def test_finalization_is_closed(self):
        self.user('reviewer',roles=('archive-reader','archive-operator','archive-approver'))
        t=self.client.post('/api/transfers',json={'title':'x'}).json()
        self.assertEqual(self.client.post('/api/transfers/'+t['id']+'/finalize',json={'revision':0}).status_code,409)
    def test_invalid_profile_is_audited(self):
        self.user('operator');t=self.client.post('/api/transfers',json={'title':'x'}).json()
        with (ROOT/'fixtures/reference-1.3.zip').open('rb') as f:
            result=self.client.post('/api/transfers/'+t['id']+'/sips',data={'profile':'1.1','revision':'0'},files={'file':('reference.zip',f,'application/zip')})
        self.assertEqual(result.status_code,422)
        t=self.client.get('/api/transfers/'+t['id']).json();self.assertEqual(t['revision'],1);self.assertEqual(t['events'][-1]['outcome'],'failure')

if __name__=='__main__':unittest.main()
