"""Register administrator-controlled tenant scope in the existing local development realm.

Preserves existing user-profile fields. Does not reset users, roles or sessions.
"""
import json,os
from pathlib import Path
import httpx

root=Path(__file__).resolve().parents[1]
attribute=next(a for a in json.loads((root/'config/user-profile.json').read_text())['attributes'] if a['name']=='archive_id')
with httpx.Client(base_url='http://127.0.0.1:8080',trust_env=False,timeout=10) as client:
    token=client.post('/realms/master/protocol/openid-connect/token',data={'grant_type':'password','client_id':'admin-cli',
        'username':os.getenv('KC_BOOTSTRAP_ADMIN_USERNAME','local-admin'),
        'password':os.getenv('KC_BOOTSTRAP_ADMIN_PASSWORD','local-admin-change-me')})
    token.raise_for_status();client.headers['Authorization']='Bearer '+token.json()['access_token']
    path='/admin/realms/ingest-workbench/users/profile'
    response=client.get(path);response.raise_for_status();profile=response.json()
    existing=next((a for a in profile['attributes'] if a['name']=='archive_id'),None)
    if existing is not None and existing!=attribute:
        raise SystemExit('Existing archive_id governance differs; review it before applying a change.')
    if existing is None:
        profile['attributes'].append(attribute)
        response=client.put(path,json=profile);response.raise_for_status()
    checked=client.get(path);checked.raise_for_status()
    actual=next(a for a in checked.json()['attributes'] if a['name']=='archive_id')
    if actual['permissions']['edit']!=['admin'] or actual['multivalued']:
        raise SystemExit('Local archive scope governance check failed')
print('Local archive_id registered, single-valued and editable only by administrators. No production qualification implied.')
