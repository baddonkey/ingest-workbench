"""Offline 1:3 package demonstration; API/OIDC is tested separately."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.service import Service
s=Service(ROOT/'var/demo',ROOT/'schemas')
b=s.create('Reference 1 SIP to 3 AIP candidates','offline-demo','demo-operator')
b=s.upload(b['id'],b['tenant'],'demo-operator',0,'1.3.0',(ROOT/'fixtures/reference-1.3.zip').read_bytes())
source=b['sips'][0]
targets=[{'label':u['title'],'units':[{'sip_id':source['id'],'unit_id':u['id']}]} for u in source['units']]
b=s.plan(b['id'],b['tenant'],'demo-operator',b['revision'],targets,'Demonstrate archival dossier boundaries')
b=s.approve(b['id'],b['tenant'],'demo-reviewer',b['revision'])
b=s.build(b['id'],b['tenant'],'demo-operator',b['revision'])
folder=ROOT/'var/demo/outputs'/b['id'];folder.mkdir(parents=True,exist_ok=True)
for out in b['outputs']:
    path=folder/('AIP-candidate-'+out['id']+'.zip');path.write_bytes(s.blob(out['sha256']))
    print(path, out['sha256'])
print('Candidate packages only. No archival repository or AIS acceptance has occurred.')
