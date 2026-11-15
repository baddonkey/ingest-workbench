"""OIDC resource server: signature, issuer, audience and expiry verified on every request."""
import os
from functools import lru_cache
from dataclasses import dataclass
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

@dataclass(frozen=True)
class Principal:
    sub: str
    tenant: str
    roles: frozenset[str]
    client_id: str = "archive-workbench"

bearer=HTTPBearer(auto_error=False)
@lru_cache
def keyset():
    issuer=os.getenv('OIDC_ISSUER','http://localhost:8080/realms/ingest-workbench')
    return jwt.PyJWKClient(os.getenv('OIDC_JWKS_URL',issuer+'/protocol/openid-connect/certs'),cache_jwk_set=True,lifespan=300,timeout=5)

def principal(credentials: HTTPAuthorizationCredentials|None=Depends(bearer)):
    if credentials is None: raise HTTPException(401,'Bearer token required',headers={'WWW-Authenticate':'Bearer'})
    try:
        token=credentials.credentials
        key=keyset().get_signing_key_from_jwt(token).key
        claims=jwt.decode(token,key,algorithms=['RS256'],audience='archive-api',
                          issuer=os.getenv('OIDC_ISSUER','http://localhost:8080/realms/ingest-workbench'),
                          options={'require':['exp','iat','sub','iss','aud']},leeway=10)
        if claims.get('azp') not in os.getenv('OIDC_ALLOWED_CLIENTS','archive-workbench').split(','): raise ValueError('Untrusted client')
        if claims.get('typ')!='Bearer': raise ValueError('Access token required')
        tenant=claims.get('archive_id')
        if not isinstance(tenant,str) or not tenant: raise ValueError('Archive scope missing')
        return Principal(claims['sub'],tenant,frozenset(claims.get('realm_access',{}).get('roles',[])),claims['azp'])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(401,'Invalid access token',headers={'WWW-Authenticate':'Bearer'}) from exc

def require(role):
    def dependency(user:Principal=Depends(principal)):
        if role not in user.roles: raise HTTPException(403,'Required role: '+role)
        return user
    return dependency
