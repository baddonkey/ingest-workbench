"""Local service readiness, independent of archival acceptance or external qualification."""
from .packages import sha, ech_schema_dir

# Pins match the unchanged official bundles and retained corpus/provenance.
ECH_BUNDLES={
    '1.0':'96960741c39e523eee7f889d815b66ff74f14df47547dd19e62029a2e1f87e81',
    '1.1':'bd5456ab3651d4d257238e2fabc5eb22d0578e57451f963dfa0f89dbf9f2cf05',
    '1.2.0':'2ea37ab70097e3d5bb10e0bfb6dab360c05b44c14fd2879797db9c25dd2b41c0',
    '1.3.0':'86ef1b881f46375f2d756e706fb6e61dff6fe96ae9082f19b1d84c7e4cbb4fcf',
}
PRESERVATION_SCHEMAS={
    'mets.xsd':'8f289c776e490e4763dab0e4b958c74993e5f271718cf244f24d00bb5af62a1f',
    'premis.xsd':'03b8a77a20b32b882ad799e12262671d07ad18210c60233f4e613a1289491cba',
    'xlink.xsd':'f1f5bb6003165cdd8f6c1fcc32f8fd1f965e1681010f3b9806d9460bcffa8a3c',
    'xml.xsd':'61960fb3131e38022caad5360e2f33a3382578ab3c80cd58bd74320ede61b20c',
}


def check(service):
    checks={}
    for profile,expected in ECH_BUNDLES.items():
        try:
            paths=sorted(ech_schema_dir(service.schemas,profile).glob('*.xsd'))
            checks['ech_'+profile]=sha(b''.join(p.name.encode()+p.read_bytes() for p in paths))==expected
        except OSError:
            checks['ech_'+profile]=False
    try:
        checks['preservation_schemas']=all(
            sha((service.schemas/'preservation'/name).read_bytes())==digest
            for name,digest in PRESERVATION_SCHEMAS.items())
    except OSError:
        checks['preservation_schemas']=False
    checks['workflow_database']=service.store.ready()
    return {'status':'ready' if all(checks.values()) else 'not_ready','mode':'reference',
            'persistence':service.store.backend,'finalization_enabled':False,'checks':checks}
