"""Offline reference corpus qualification. Exit nonzero on any mismatch."""
import json,sys,hashlib,platform
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.packages import inspect,sha

def qualify():
    manifest=json.loads((ROOT/'fixtures/corpus.json').read_text());results=[]
    for case in manifest['cases']:
        result={'file':case['file'],'profile':case['profile'],'reference_variant':case['reference_variant'],'expected_sha256':case['sha256']}
        try:
            data=(ROOT/case['file']).read_bytes();result['actual_sha256']=sha(data)
            if sha(data)!=case['sha256'] or len(data)!=case['size_bytes']:raise ValueError('Fixture bytes differ from registered original')
            report=inspect(data,case['profile'],ROOT/'schemas')
            actual={'schema_version':report['schema_version'],'kind':report['kind'],'payload_files':len(report['files']),'top_level_dossiers':len(report['units'])}
            for key,value in actual.items():
                if value!=case[key]:raise ValueError(f'{key}: expected {case[key]}, received {value}')
        except Exception as exc:result.update(status='FAIL',error=str(exc))
        results.append(result)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),'scope':manifest['scope'],
            'all_passed':all(r['status']=='PASS' for r in results),'results':results}

if __name__=='__main__':
    report=qualify();destination=ROOT/'var/local-evidence/current/corpus-results.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2)+'\n')
    for r in report['results']:print(r['status'],r['profile'],r['reference_variant'],Path(r['file']).name,r.get('error',''))
    print('Report:',destination)
    sys.exit(0 if report['all_passed'] else 1)
