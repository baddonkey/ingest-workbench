import time,unittest
from unittest.mock import patch
from types import SimpleNamespace
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.auth import principal

class OidcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    def token(self,**changes):
        claims={'sub':'subject','iss':'http://localhost:8080/realms/ingest-workbench','aud':'archive-api','azp':'archive-workbench','typ':'Bearer','iat':int(time.time()),'exp':int(time.time())+60,'archive_id':'archive-a','realm_access':{'roles':['archive-reader']}}
        claims.update(changes);return jwt.encode(claims,self.key,algorithm='RS256')
    def read(self,token):
        jwks=SimpleNamespace(get_signing_key_from_jwt=lambda t:SimpleNamespace(key=self.key.public_key()))
        with patch('app.auth.keyset',return_value=jwks):return principal(HTTPAuthorizationCredentials(scheme='Bearer',credentials=token))
    def test_signed_access_token(self):self.assertEqual(self.read(self.token()).tenant,'archive-a')
    def test_wrong_audience_issuer_expired_and_wrong_client(self):
        for change in ({'aud':'other'},{'iss':'https://attacker.invalid'},{'exp':0},{'azp':'other'},{'typ':'ID'},{'archive_id':None}):
            with self.subTest(change=change):
                with self.assertRaises(HTTPException):self.read(self.token(**change))
    def test_forged_signature(self):
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        claims=jwt.decode(self.token(),options={'verify_signature':False})
        with self.assertRaises(HTTPException):self.read(jwt.encode(claims,key,algorithm='RS256'))

if __name__=='__main__':unittest.main()
